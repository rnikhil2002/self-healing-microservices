import random

from healer.anomaly import AnomalyDetector, IncidentType, ServiceDetector
from healer.prometheus import MetricSample


def healthy(rng: random.Random) -> MetricSample:
    return MetricSample(
        latency_p95_ms=rng.uniform(40, 70),
        error_rate=rng.uniform(0.0, 0.004),
        heap_ratio=rng.uniform(0.25, 0.35),
        request_rate=rng.uniform(18, 22),
    )


def trained(seed: int = 1) -> tuple[ServiceDetector, random.Random]:
    rng = random.Random(seed)
    det = ServiceDetector("payment-service", warmup_samples=30)
    for _ in range(30):
        det.observe(healthy(rng))
    assert det.ready
    return det, rng


def test_warms_up_before_judging():
    det = ServiceDetector("svc", warmup_samples=5)
    rng = random.Random(0)
    verdicts = [det.observe(healthy(rng)) for _ in range(4)]
    assert not det.ready
    assert all(not v.is_anomaly for v in verdicts)
    assert "warming up" in verdicts[-1].reason


def test_healthy_traffic_is_not_flagged():
    det, rng = trained()
    flagged = sum(det.observe(healthy(rng)).is_anomaly for _ in range(200))
    assert flagged == 0


def test_error_spike_is_detected_and_classified():
    det, _ = trained()
    v = det.observe(MetricSample(55, 0.45, 0.3, 20))
    assert v.is_anomaly
    assert v.incident_type is IncidentType.ERROR_SPIKE
    assert "error rate" in v.reason


def test_latency_spike_is_detected():
    det, _ = trained()
    v = det.observe(MetricSample(900, 0.0, 0.3, 20))
    assert v.is_anomaly
    assert v.incident_type is IncidentType.HIGH_LATENCY


def test_memory_pressure_is_detected():
    det, _ = trained()
    v = det.observe(MetricSample(60, 0.0, 0.84, 20))
    assert v.is_anomaly
    assert v.incident_type is IncidentType.MEMORY_PRESSURE


def test_traffic_change_alone_is_not_an_incident():
    det, _ = trained()
    v = det.observe(MetricSample(60, 0.002, 0.3, 80))
    assert not v.is_anomaly


def test_small_wobble_is_ignored_even_if_unusual():
    det, _ = trained()
    # Latency up 60 ms: statistically odd for a very stable service, but not worth paging anyone.
    v = det.observe(MetricSample(120, 0.0, 0.3, 20))
    assert not v.is_anomaly


def test_streak_is_required_before_acting():
    rng = random.Random(3)
    ad = AnomalyDetector(["svc"], warmup_samples=20, streak_required=3)
    for _ in range(20):
        ad.observe("svc", healthy(rng))
    spike = MetricSample(50, 0.5, 0.3, 20)
    results = [ad.observe("svc", spike)[1] for _ in range(3)]
    assert results == [False, False, True]
    ad.acknowledge("svc")
    assert ad.streaks["svc"] == 0
    # A healthy sample breaks the streak.
    ad.observe("svc", spike)
    ad.observe("svc", healthy(rng))
    assert ad.streaks["svc"] == 0
