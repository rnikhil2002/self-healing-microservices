from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from healer.anomaly import IncidentType
from healer.remediation import REVISION, Action, KubernetesRemediator, apply, choose_action


def test_error_spike_after_a_deploy_rolls_back():
    assert choose_action(IncidentType.ERROR_SPIKE, True, 1, 4) is Action.ROLLBACK


def test_error_spike_without_a_deploy_restarts():
    assert choose_action(IncidentType.ERROR_SPIKE, False, 1, 4) is Action.RESTART


def test_latency_scales_out_until_the_cap():
    assert choose_action(IncidentType.HIGH_LATENCY, False, 2, 4) is Action.SCALE_UP
    assert choose_action(IncidentType.HIGH_LATENCY, False, 4, 4) is Action.RESTART


def test_memory_pressure_restarts():
    assert choose_action(IncidentType.MEMORY_PRESSURE, True, 1, 4) is Action.RESTART


# ---- Kubernetes remediator against a fake API ---------------------------------------------

def _template(env_value):
    return NS(metadata=NS(labels={"app": "payment-service", "pod-template-hash": "abc"}),
              spec=NS(containers=[NS(name="app", env=[NS(name="CHAOS_ERROR_RATE", value=env_value)])]))


def _rs(rev, created, uid="dep-uid", env_value="0"):
    return NS(metadata=NS(annotations={REVISION: str(rev)}, owner_references=[NS(uid=uid)],
                          creation_timestamp=created),
              spec=NS(template=_template(env_value)))


class FakeApps:
    def __init__(self, replica_sets, revision=2, replicas=1):
        self.deployment = NS(
            metadata=NS(uid="dep-uid", annotations={REVISION: str(revision)}),
            spec=NS(replicas=replicas, selector=NS(match_labels={"app": "payment-service"}),
                    template=_template("0.6")))
        self.replica_sets = replica_sets
        self.replaced = None
        self.scaled = None

    def read_namespaced_deployment(self, name, ns):
        return self.deployment

    def list_namespaced_replica_set(self, ns, label_selector):
        assert label_selector == "app=payment-service"
        return NS(items=self.replica_sets)

    def replace_namespaced_deployment(self, name, ns, body):
        self.replaced = body

    def patch_namespaced_deployment_scale(self, name, ns, body):
        self.scaled = body


class FakeCore:
    def __init__(self, pods):
        self.pods = pods
        self.deleted = []

    def list_namespaced_pod(self, ns, label_selector):
        return NS(items=[NS(metadata=NS(name=p)) for p in self.pods])

    def delete_namespaced_pod(self, name, ns):
        self.deleted.append(name)


def test_rollback_restores_previous_pod_template():
    now = datetime.now(timezone.utc)
    apps = FakeApps([_rs(1, now - timedelta(hours=2), env_value="0"),
                     _rs(2, now - timedelta(minutes=3), env_value="0.6"),
                     _rs(5, now, uid="someone-else")])
    remediator = KubernetesRemediator("ns", apps_api=apps, core_api=FakeCore([]))

    result = remediator.rollback("payment-service")

    assert result.success
    assert "revision 2 to 1" in result.detail
    restored = apps.replaced.spec.template
    assert restored.spec.containers[0].env[0].value == "0"
    assert "pod-template-hash" not in restored.metadata.labels


def test_rollback_without_history_fails_cleanly():
    apps = FakeApps([_rs(1, datetime.now(timezone.utc))], revision=1)
    result = KubernetesRemediator("ns", apps_api=apps, core_api=FakeCore([])).rollback("payment-service")
    assert not result.success
    assert apps.replaced is None


def test_recently_deployed_looks_at_the_newest_replica_set():
    now = datetime.now(timezone.utc)
    fresh = FakeApps([_rs(1, now - timedelta(days=1)), _rs(2, now - timedelta(minutes=2))])
    stale = FakeApps([_rs(1, now - timedelta(days=2)), _rs(2, now - timedelta(days=1))])
    single = FakeApps([_rs(1, now)], revision=1)
    assert KubernetesRemediator("ns", fresh, FakeCore([])).recently_deployed("payment-service", 900)
    assert not KubernetesRemediator("ns", stale, FakeCore([])).recently_deployed("payment-service", 900)
    assert not KubernetesRemediator("ns", single, FakeCore([])).recently_deployed("payment-service", 900)


def test_restart_deletes_the_service_pods():
    core = FakeCore(["payment-service-1", "payment-service-2"])
    result = KubernetesRemediator("ns", FakeApps([]), core).restart("payment-service")
    assert result.success
    assert core.deleted == ["payment-service-1", "payment-service-2"]


def test_scale_up_adds_one_replica_and_respects_the_cap():
    apps = FakeApps([], replicas=2)
    r = KubernetesRemediator("ns", apps, FakeCore([]))
    assert r.scale_up("payment-service", 4).success
    assert apps.scaled == {"spec": {"replicas": 3}}

    capped = FakeApps([], replicas=4)
    assert not KubernetesRemediator("ns", capped, FakeCore([])).scale_up("payment-service", 4).success


def test_apply_turns_api_errors_into_failed_results():
    class Broken:
        def restart(self, service):
            raise RuntimeError("api server unavailable")

    result = apply(Broken(), Action.RESTART, "svc", 4)
    assert not result.success
    assert "api server unavailable" in result.detail
