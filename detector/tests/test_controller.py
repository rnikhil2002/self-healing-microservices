import random

import pytest
import requests

from healer.config import Settings
from healer.controller import HealingController, IncidentStore
from healer.prometheus import MetricSample
from healer.remediation import DryRunRemediator
from healer.summarizer import Summarizer, template_summary


class ScriptedPrometheus:
    """Returns healthy metrics until told to break a service."""

    def __init__(self):
        self.rng = random.Random(9)
        self.broken: dict[str, MetricSample] = {}

    def collect(self, services):
        out = {}
        for s in services:
            out[s] = self.broken.get(s) or MetricSample(
                self.rng.uniform(40, 60), self.rng.uniform(0, 0.003), self.rng.uniform(0.25, 0.3), 20)
        return out


@pytest.fixture
def controller():
    settings = Settings(prometheus_url="http://unused", services=["order-service", "payment-service"],
                        warmup_samples=20, anomaly_streak=3, cooldown_s=60, remediation_mode="dry-run",
                        openai_api_key=None, incidents_file="")
    return HealingController(settings, ScriptedPrometheus(), DryRunRemediator(), Summarizer(None),
                             IncidentStore(None))


def test_full_cycle_detect_remediate_and_recover(controller):
    t = 0.0
    for _ in range(25):
        assert controller.tick(t) == []
        t += 5

    controller.prometheus.broken["payment-service"] = MetricSample(50, 0.5, 0.28, 20)
    incidents = []
    for _ in range(3):
        incidents += controller.tick(t)
        t += 5

    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.service == "payment-service"
    assert incident.incident_type == "ERROR_SPIKE"
    assert incident.action == "RESTART"
    assert controller.remediator.calls == [("restart", "payment-service")]
    assert incident.summary_source == "template"
    assert "payment-service" in incident.summary

    # Still broken but inside the cooldown: no second action.
    for _ in range(5):
        assert controller.tick(t) == []
        t += 5

    # Fixed: the follow-up check marks the incident as recovered.
    controller.prometheus.broken.clear()
    for _ in range(20):
        controller.tick(t)
        t += 5
    assert incident.recovered is True
    assert controller.store.all()[0]["id"] == incident.id


def test_prometheus_outage_does_not_crash_the_loop(controller):
    class Down:
        def collect(self, services):
            raise requests.ConnectionError("connection refused")

    controller.prometheus = Down()
    assert controller.tick(0) == []
    assert "refused" in controller.status()["prometheus_error"]


def test_template_summary_mentions_action_and_cause():
    text = template_summary({
        "service": "payment-service", "incident_type": "ERROR_SPIKE",
        "reason": "error rate 45.0% vs baseline 0.2%", "zscores": {"error_rate": 44.8, "heap_ratio": 0.1},
        "action": "ROLLBACK", "action_success": True, "action_detail": "rolled back from 2 to 1",
        "detected_at": "2026-01-01T00:00:00+00:00"})
    assert "payment-service" in text
    assert "rollback" in text
    assert "error_rate" in text


def test_llm_failure_falls_back_to_template(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.Timeout("too slow")

    monkeypatch.setattr("healer.summarizer.requests.post", boom)
    summary, source = Summarizer("sk-test").summarize({
        "service": "svc", "incident_type": "HIGH_LATENCY", "reason": "p95 900 ms",
        "zscores": {"latency_p95_ms": 30}, "action": "SCALE_UP", "action_success": True, "action_detail": "ok"})
    assert source == "template"
    assert "svc" in summary


def test_llm_summary_is_used_when_available(monkeypatch):
    class Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"choices": [{"message": {"content": "  Payment errors spiked after deploy.  "}}]}

    captured = {}

    def fake_post(url, headers, json, timeout):
        captured["auth"] = headers["Authorization"]
        captured["model"] = json["model"]
        return Resp()

    monkeypatch.setattr("healer.summarizer.requests.post", fake_post)
    summary, source = Summarizer("sk-test", model="gpt-4o-mini").summarize({"service": "svc"})
    assert source == "llm"
    assert summary == "Payment errors spiked after deploy."
    assert captured == {"auth": "Bearer sk-test", "model": "gpt-4o-mini"}
