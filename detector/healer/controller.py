"""The control loop: poll metrics, detect, remediate, record the incident."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from prometheus_client import Counter, Gauge

from .anomaly import AnomalyDetector, Verdict
from .config import Settings
from .prometheus import MetricSample, PrometheusClient
from .remediation import Remediator, apply, choose_action
from .summarizer import Summarizer

log = logging.getLogger(__name__)

ANOMALY_SCORE = Gauge("healer_anomaly_score", "Isolation Forest decision score (lower is more anomalous)", ["service"])
METRIC_VALUE = Gauge("healer_observed_metric", "Latest metric value seen by the healer", ["service", "metric"])
INCIDENTS = Counter("healer_incidents_total", "Incidents detected", ["service", "type"])
REMEDIATIONS = Counter("healer_remediations_total", "Remediation actions taken", ["service", "action", "result"])


@dataclass
class Incident:
    id: str
    service: str
    incident_type: str
    reason: str
    zscores: dict
    metrics_at_detection: dict
    detected_at: str
    action: str | None = None
    action_success: bool | None = None
    action_detail: str | None = None
    summary: str | None = None
    summary_source: str | None = None
    metrics_after: dict | None = None
    recovered: bool | None = None
    timeline: list = field(default_factory=list)


class IncidentStore:
    def __init__(self, path: str | None = None, capacity: int = 200):
        self.path = path
        self.items: deque[Incident] = deque(maxlen=capacity)
        self.lock = threading.Lock()

    def add(self, incident: Incident) -> None:
        with self.lock:
            self.items.appendleft(incident)
        self._persist(incident)

    def update(self, incident: Incident) -> None:
        self._persist(incident)

    def all(self) -> list[dict]:
        with self.lock:
            return [asdict(i) for i in self.items]

    def _persist(self, incident: Incident) -> None:
        if not self.path:
            return
        try:
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(asdict(incident)) + "\n")
        except OSError as exc:
            log.warning("Could not write incident log: %s", exc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class HealingController:
    def __init__(self, settings: Settings, prometheus: PrometheusClient, remediator: Remediator,
                 summarizer: Summarizer, store: IncidentStore | None = None):
        self.settings = settings
        self.prometheus = prometheus
        self.remediator = remediator
        self.summarizer = summarizer
        self.store = store or IncidentStore(settings.incidents_file)
        self.detector = AnomalyDetector(settings.services, settings.warmup_samples, settings.anomaly_streak)
        self.last_action_at: dict[str, float] = {}
        self.pending_checks: list[tuple[float, Incident]] = []
        self.last_samples: dict[str, dict] = {}
        self.last_error: str | None = None
        self._stop = threading.Event()

    # ---- one iteration -------------------------------------------------------------------

    def tick(self, now: float | None = None) -> list[Incident]:
        now = time.time() if now is None else now
        try:
            samples = self.prometheus.collect(self.settings.services)
            self.last_error = None
        except Exception as exc:  # noqa: BLE001 - keep the loop alive if Prometheus blips
            self.last_error = str(exc)
            log.warning("Could not read metrics: %s", exc)
            return []

        created = []
        for service, sample in samples.items():
            self.last_samples[service] = sample.as_dict()
            for name, value in sample.as_dict().items():
                METRIC_VALUE.labels(service, name).set(value)
            verdict, actionable = self.detector.observe(service, sample)
            ANOMALY_SCORE.labels(service).set(verdict.score)
            if verdict.is_anomaly:
                log.info("%s anomalous (%s) streak=%d: %s", service, verdict.incident_type.value,
                         self.detector.streaks[service], verdict.reason)
            if actionable and self._cooled_down(service, now):
                created.append(self._handle(verdict, sample, now))

        self._check_recoveries(samples, now)
        return created

    def _cooled_down(self, service: str, now: float) -> bool:
        last = self.last_action_at.get(service)
        return last is None or now - last >= self.settings.cooldown_s

    def _handle(self, verdict: Verdict, sample: MetricSample, now: float) -> Incident:
        incident = Incident(
            id=f"inc-{uuid.uuid4().hex[:10]}",
            service=verdict.service,
            incident_type=verdict.incident_type.value,
            reason=verdict.reason,
            zscores=verdict.zscores,
            metrics_at_detection=sample.as_dict(),
            detected_at=_now(),
        )
        incident.timeline.append({"at": incident.detected_at, "event": f"detected: {verdict.reason}"})
        INCIDENTS.labels(verdict.service, incident.incident_type).inc()

        recent = self.remediator.recently_deployed(verdict.service, self.settings.rollback_window_s)
        replicas = self.remediator.replicas(verdict.service)
        action = choose_action(verdict.incident_type, recent, replicas, self.settings.max_replicas)
        result = apply(self.remediator, action, verdict.service, self.settings.max_replicas)

        incident.action = result.action.value
        incident.action_success = result.success
        incident.action_detail = result.detail
        incident.timeline.append({"at": _now(), "event": f"{result.action.value}: {result.detail}"})
        REMEDIATIONS.labels(verdict.service, result.action.value, "success" if result.success else "failed").inc()
        log.warning("INCIDENT %s %s on %s -> %s (%s)", incident.id, incident.incident_type,
                    incident.service, result.action.value, result.detail)

        incident.summary, incident.summary_source = self.summarizer.summarize(asdict(incident))
        self.store.add(incident)
        self.last_action_at[verdict.service] = now
        self.detector.acknowledge(verdict.service)
        # Look again after the cooldown to see whether the fix worked.
        self.pending_checks.append((now + min(self.settings.cooldown_s, 90), incident))
        return incident

    def _check_recoveries(self, samples: dict[str, MetricSample], now: float) -> None:
        still_pending = []
        for due, incident in self.pending_checks:
            if now < due or incident.service not in samples:
                still_pending.append((due, incident))
                continue
            sample = samples[incident.service]
            verdict = self.detector.detectors[incident.service]._score(sample)
            incident.metrics_after = sample.as_dict()
            incident.recovered = not verdict.is_anomaly
            incident.timeline.append({"at": _now(),
                                      "event": "recovered" if incident.recovered else "still unhealthy"})
            self.store.update(incident)
            log.info("Incident %s on %s %s", incident.id, incident.service,
                     "recovered" if incident.recovered else "is still unhealthy")
        self.pending_checks = still_pending

    # ---- background loop -----------------------------------------------------------------

    def run_forever(self) -> None:
        log.info("Healer watching %s via %s (mode=%s)", self.settings.services,
                 self.settings.prometheus_url, self.settings.remediation_mode)
        while not self._stop.is_set():
            started = time.time()
            self.tick(started)
            self._stop.wait(max(0.0, self.settings.poll_interval_s - (time.time() - started)))

    def stop(self) -> None:
        self._stop.set()

    def status(self) -> dict:
        return {
            "services": self.detector.status(),
            "latest_metrics": self.last_samples,
            "mode": self.settings.remediation_mode,
            "prometheus_error": self.last_error,
        }
