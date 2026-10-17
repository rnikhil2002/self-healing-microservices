"""Isolation Forest anomaly detection on per-service metrics.

Each service gets its own model, trained on a warm-up window of healthy samples.
A sample is only treated as an incident when it is an outlier (the forest flags it, or a
health metric is far outside the baseline range) AND at least one health metric has moved
by a meaningful absolute amount. The second check stops a quiet service with tiny natural
variance from looking "anomalous" on noise alone.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
from sklearn.ensemble import IsolationForest

from .prometheus import MetricSample

log = logging.getLogger(__name__)

FEATURES = ["latency_p95_ms", "error_rate", "heap_ratio", "request_rate"]

# Smallest standard deviation we trust per feature, so near-constant baselines do not
# turn every tiny wiggle into a huge z-score.
STD_FLOOR = np.array([15.0, 0.01, 0.03, 0.5])

# How far a metric must move above its baseline mean to count as a real problem.
MIN_ABSOLUTE_SHIFT = {"latency_p95_ms": 150.0, "error_rate": 0.05, "heap_ratio": 0.15}
HARD_LIMITS = {"heap_ratio": 0.80, "error_rate": 0.20}
# Standard deviations from the baseline mean that count as extreme on their own.
Z_EXTREME = 6.0


class IncidentType(str, Enum):
    ERROR_SPIKE = "ERROR_SPIKE"
    HIGH_LATENCY = "HIGH_LATENCY"
    MEMORY_PRESSURE = "MEMORY_PRESSURE"


@dataclass
class Verdict:
    service: str
    is_anomaly: bool
    score: float
    incident_type: IncidentType | None = None
    zscores: dict[str, float] = field(default_factory=dict)
    reason: str = ""


class ServiceDetector:
    def __init__(self, service: str, warmup_samples: int = 24, contamination: float = 0.02, seed: int = 42):
        self.service = service
        self.warmup_samples = warmup_samples
        self.contamination = contamination
        self.seed = seed
        self.baseline: list[list[float]] = []
        self.model: IsolationForest | None = None
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None

    @property
    def ready(self) -> bool:
        return self.model is not None

    def observe(self, sample: MetricSample) -> Verdict:
        if not self.ready:
            self.baseline.append(sample.vector())
            if len(self.baseline) >= self.warmup_samples:
                self._fit()
            return Verdict(self.service, False, 0.0, reason=f"warming up ({len(self.baseline)}/{self.warmup_samples})")
        return self._score(sample)

    def reset(self) -> None:
        """Forget the baseline, e.g. after scaling changed what normal looks like."""
        self.baseline.clear()
        self.model = None

    def _fit(self) -> None:
        data = np.array(self.baseline, dtype=float)
        self.mean = data.mean(axis=0)
        self.std = np.maximum(data.std(axis=0), STD_FLOOR)
        z = (data - self.mean) / self.std
        # Jitter the training set a little so a perfectly flat baseline still has a shape.
        rng = np.random.default_rng(self.seed)
        augmented = np.vstack([z, z + rng.normal(0, 0.5, z.shape)])
        self.model = IsolationForest(n_estimators=200, contamination=self.contamination, random_state=self.seed)
        self.model.fit(augmented)
        log.info("%s baseline trained on %d samples: mean=%s", self.service, len(self.baseline),
                 dict(zip(FEATURES, self.mean.round(3))))

    def _score(self, sample: MetricSample) -> Verdict:
        x = np.array(sample.vector(), dtype=float)
        z = (x - self.mean) / self.std
        zscores = {name: round(float(v), 2) for name, v in zip(FEATURES, z)}
        score = float(self.model.decision_function([z])[0])
        # The forest catches unusual *combinations* inside the range it has seen. Its splits
        # only span the training data, so it cannot tell "far beyond normal" from "at the edge
        # of normal"; the z-score check covers those out-of-range spikes.
        forest_outlier = self.model.predict([z])[0] == -1
        extreme = max(abs(z[0]), abs(z[1]), abs(z[2])) >= Z_EXTREME

        values = sample.as_dict()
        baseline = dict(zip(FEATURES, self.mean))
        # Some levels are bad no matter how noisy the baseline was (e.g. a GC-heavy heap).
        hard_breach = any(values[name] >= limit for name, limit in HARD_LIMITS.items())
        outlier = forest_outlier or extreme or hard_breach
        shifted = {
            name: values[name] - baseline[name] >= MIN_ABSOLUTE_SHIFT[name]
            or values[name] >= HARD_LIMITS.get(name, float("inf"))
            for name in MIN_ABSOLUTE_SHIFT
        }

        if not (outlier and any(shifted.values())):
            return Verdict(self.service, False, score, zscores=zscores)

        incident = self._classify(zscores, shifted)
        reason = self._explain(incident, values, baseline)
        return Verdict(self.service, True, score, incident, zscores, reason)

    @staticmethod
    def _classify(zscores: dict[str, float], shifted: dict[str, bool]) -> IncidentType:
        candidates = {
            IncidentType.ERROR_SPIKE: zscores["error_rate"] if shifted["error_rate"] else float("-inf"),
            IncidentType.HIGH_LATENCY: zscores["latency_p95_ms"] if shifted["latency_p95_ms"] else float("-inf"),
            IncidentType.MEMORY_PRESSURE: zscores["heap_ratio"] if shifted["heap_ratio"] else float("-inf"),
        }
        # Errors are the most user visible, so they win ties.
        return max(candidates, key=lambda k: (candidates[k], k == IncidentType.ERROR_SPIKE))

    @staticmethod
    def _explain(incident: IncidentType, values: dict, baseline: dict) -> str:
        if incident is IncidentType.ERROR_SPIKE:
            return f"error rate {values['error_rate']:.1%} vs baseline {baseline['error_rate']:.1%}"
        if incident is IncidentType.HIGH_LATENCY:
            return f"p95 latency {values['latency_p95_ms']:.0f} ms vs baseline {baseline['latency_p95_ms']:.0f} ms"
        return f"heap at {values['heap_ratio']:.0%} vs baseline {baseline['heap_ratio']:.0%}"


class AnomalyDetector:
    """Keeps one ServiceDetector per service and tracks anomaly streaks."""

    def __init__(self, services: list[str], warmup_samples: int = 24, streak_required: int = 3):
        self.detectors = {s: ServiceDetector(s, warmup_samples) for s in services}
        self.streak_required = streak_required
        self.streaks = {s: 0 for s in services}

    def observe(self, service: str, sample: MetricSample) -> tuple[Verdict, bool]:
        """Returns the verdict and whether the streak is long enough to act on."""
        verdict = self.detectors[service].observe(sample)
        self.streaks[service] = self.streaks[service] + 1 if verdict.is_anomaly else 0
        return verdict, self.streaks[service] >= self.streak_required

    def acknowledge(self, service: str) -> None:
        self.streaks[service] = 0

    def status(self) -> dict:
        out = {}
        for name, d in self.detectors.items():
            out[name] = {
                "ready": d.ready,
                "baseline_samples": len(d.baseline),
                "baseline_mean": dict(zip(FEATURES, d.mean.round(4).tolist())) if d.ready else None,
                "streak": self.streaks[name],
            }
        return out
