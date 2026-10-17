"""Remediation actions.

KubernetesRemediator talks to the Kubernetes API (pod restart, rollback to the previous
ReplicaSet, scale out). DockerRemediator restarts docker compose containers for local
runs. DryRunRemediator only logs, which is useful for tests and first deployments.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Protocol

from .anomaly import IncidentType

log = logging.getLogger(__name__)

REVISION = "deployment.kubernetes.io/revision"


class Action(str, Enum):
    RESTART = "RESTART"
    ROLLBACK = "ROLLBACK"
    SCALE_UP = "SCALE_UP"


@dataclass
class ActionResult:
    action: Action
    success: bool
    detail: str


class Remediator(Protocol):
    def recently_deployed(self, service: str, window_s: float) -> bool: ...

    def replicas(self, service: str) -> int: ...

    def restart(self, service: str) -> ActionResult: ...

    def rollback(self, service: str) -> ActionResult: ...

    def scale_up(self, service: str, max_replicas: int) -> ActionResult: ...


def choose_action(incident: IncidentType, recently_deployed: bool, replicas: int, max_replicas: int) -> Action:
    """Pick the least disruptive fix that addresses the likely cause."""
    if incident is IncidentType.ERROR_SPIKE:
        # A fresh rollout is the most common cause of a sudden error spike.
        return Action.ROLLBACK if recently_deployed else Action.RESTART
    if incident is IncidentType.HIGH_LATENCY:
        return Action.SCALE_UP if replicas < max_replicas else Action.RESTART
    return Action.RESTART  # memory pressure: a fresh JVM clears the leak


def apply(remediator: Remediator, action: Action, service: str, max_replicas: int) -> ActionResult:
    try:
        if action is Action.ROLLBACK:
            return remediator.rollback(service)
        if action is Action.SCALE_UP:
            return remediator.scale_up(service, max_replicas)
        return remediator.restart(service)
    except Exception as exc:  # noqa: BLE001 - report any API failure as a failed action
        log.exception("%s on %s failed", action.value, service)
        return ActionResult(action, False, f"{type(exc).__name__}: {exc}")


class DryRunRemediator:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def recently_deployed(self, service: str, window_s: float) -> bool:
        return False

    def replicas(self, service: str) -> int:
        return 1

    def restart(self, service: str) -> ActionResult:
        self.calls.append(("restart", service))
        return ActionResult(Action.RESTART, True, f"[dry-run] would restart {service}")

    def rollback(self, service: str) -> ActionResult:
        self.calls.append(("rollback", service))
        return ActionResult(Action.ROLLBACK, True, f"[dry-run] would roll back {service}")

    def scale_up(self, service: str, max_replicas: int) -> ActionResult:
        self.calls.append(("scale_up", service))
        return ActionResult(Action.SCALE_UP, True, f"[dry-run] would scale {service} up")


class KubernetesRemediator:
    """Acts on Deployments named after the service, in one namespace."""

    def __init__(self, namespace: str, apps_api=None, core_api=None):
        if apps_api is None or core_api is None:
            from kubernetes import client, config

            try:
                config.load_incluster_config()
            except config.ConfigException:
                config.load_kube_config()
            apps_api = apps_api or client.AppsV1Api()
            core_api = core_api or client.CoreV1Api()
        self.namespace = namespace
        self.apps = apps_api
        self.core = core_api

    def _deployment(self, service: str):
        return self.apps.read_namespaced_deployment(service, self.namespace)

    @staticmethod
    def _selector(deployment) -> str:
        labels = deployment.spec.selector.match_labels or {}
        return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))

    def _owned_replica_sets(self, deployment) -> list:
        rs_list = self.apps.list_namespaced_replica_set(self.namespace, label_selector=self._selector(deployment))
        uid = deployment.metadata.uid
        owned = [rs for rs in rs_list.items
                 if any(ref.uid == uid for ref in (rs.metadata.owner_references or []))]
        return sorted(owned, key=lambda rs: int((rs.metadata.annotations or {}).get(REVISION, "0")))

    def replicas(self, service: str) -> int:
        return int(self._deployment(service).spec.replicas or 1)

    def recently_deployed(self, service: str, window_s: float) -> bool:
        deployment = self._deployment(service)
        owned = self._owned_replica_sets(deployment)
        if len(owned) < 2:
            return False
        newest = owned[-1].metadata.creation_timestamp
        age = (datetime.now(timezone.utc) - newest).total_seconds()
        return age <= window_s

    def restart(self, service: str) -> ActionResult:
        deployment = self._deployment(service)
        pods = self.core.list_namespaced_pod(self.namespace, label_selector=self._selector(deployment)).items
        for pod in pods:
            self.core.delete_namespaced_pod(pod.metadata.name, self.namespace)
        names = ", ".join(p.metadata.name for p in pods)
        return ActionResult(Action.RESTART, True, f"deleted {len(pods)} pod(s) of {service}: {names}")

    def rollback(self, service: str) -> ActionResult:
        deployment = self._deployment(service)
        current = int((deployment.metadata.annotations or {}).get(REVISION, "0"))
        previous = [rs for rs in self._owned_replica_sets(deployment)
                    if int((rs.metadata.annotations or {}).get(REVISION, "0")) < current]
        if not previous:
            return ActionResult(Action.ROLLBACK, False, f"{service} has no earlier revision to roll back to")
        target = previous[-1]
        target_rev = (target.metadata.annotations or {}).get(REVISION)

        # Same as `kubectl rollout undo`: copy the old pod template back onto the deployment.
        template = target.spec.template
        labels = dict(template.metadata.labels or {})
        labels.pop("pod-template-hash", None)
        template.metadata.labels = labels
        deployment.spec.template = template
        self.apps.replace_namespaced_deployment(service, self.namespace, deployment)
        return ActionResult(Action.ROLLBACK, True, f"rolled {service} back from revision {current} to {target_rev}")

    def scale_up(self, service: str, max_replicas: int) -> ActionResult:
        deployment = self._deployment(service)
        current = int(deployment.spec.replicas or 1)
        if current >= max_replicas:
            return ActionResult(Action.SCALE_UP, False, f"{service} already at max replicas ({max_replicas})")
        self.apps.patch_namespaced_deployment_scale(
            service, self.namespace, {"spec": {"replicas": current + 1}})
        return ActionResult(Action.SCALE_UP, True, f"scaled {service} from {current} to {current + 1} replicas")


class DockerRemediator:
    """For docker compose: restarts the service's containers. Rollback and scaling fall back to restart."""

    def __init__(self, docker_client=None):
        if docker_client is None:
            import docker

            docker_client = docker.from_env()
        self.docker = docker_client

    def _containers(self, service: str):
        return self.docker.containers.list(filters={"label": f"com.docker.compose.service={service}"})

    def recently_deployed(self, service: str, window_s: float) -> bool:
        return False

    def replicas(self, service: str) -> int:
        return max(1, len(self._containers(service)))

    def restart(self, service: str) -> ActionResult:
        containers = self._containers(service)
        for c in containers:
            c.restart(timeout=10)
        return ActionResult(Action.RESTART, bool(containers), f"restarted {len(containers)} container(s) of {service}")

    def rollback(self, service: str) -> ActionResult:
        result = self.restart(service)
        return ActionResult(Action.RESTART, result.success, result.detail + " (rollback not available in compose)")

    def scale_up(self, service: str, max_replicas: int) -> ActionResult:
        result = self.restart(service)
        return ActionResult(Action.RESTART, result.success, result.detail + " (scaling not available in compose)")


def build_remediator(mode: str, namespace: str) -> Remediator:
    if mode == "kubernetes":
        return KubernetesRemediator(namespace)
    if mode == "docker":
        return DockerRemediator()
    return DryRunRemediator()
