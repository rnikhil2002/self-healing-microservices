"""Reads service health metrics from Prometheus.

The services expose Spring Boot Actuator / Micrometer metrics tagged with
`application=<service name>`, so every query is grouped by that label.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass

import requests

log = logging.getLogger(__name__)

# Business endpoints only: actuator scrapes and chaos calls would skew the numbers.
_FILTER = 'uri!~"/actuator.*|/chaos.*"'

QUERIES = {
    "latency_p95_ms": (
        "1000 * histogram_quantile(0.95, sum by (application, le) "
        f"(rate(http_server_requests_seconds_bucket{{{_FILTER}}}[1m])))"
    ),
    "error_rate": (
        "sum by (application) "
        f'(rate(http_server_requests_seconds_count{{{_FILTER},status=~"5.."}}[1m])) '
        "/ sum by (application) "
        f"(rate(http_server_requests_seconds_count{{{_FILTER}}}[1m]))"
    ),
    "heap_ratio": (
        'sum by (application) (jvm_memory_used_bytes{area="heap"}) '
        '/ sum by (application) (jvm_memory_max_bytes{area="heap"} > 0)'
    ),
    "request_rate": (
        f"sum by (application) (rate(http_server_requests_seconds_count{{{_FILTER}}}[1m]))"
    ),
}


@dataclass
class MetricSample:
    latency_p95_ms: float
    error_rate: float
    heap_ratio: float
    request_rate: float

    def as_dict(self) -> dict:
        return asdict(self)

    def vector(self) -> list[float]:
        return [self.latency_p95_ms, self.error_rate, self.heap_ratio, self.request_rate]


class PrometheusClient:
    def __init__(self, base_url: str, timeout: float = 5.0, session: requests.Session | None = None):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()

    def query(self, promql: str) -> dict[str, float]:
        """Run an instant query and return {application: value}."""
        resp = self.session.get(f"{self.base_url}/api/v1/query", params={"query": promql}, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()
        if body.get("status") != "success":
            raise RuntimeError(f"Prometheus query failed: {body}")
        out: dict[str, float] = {}
        for item in body["data"]["result"]:
            app = item["metric"].get("application")
            if not app:
                continue
            try:
                value = float(item["value"][1])
            except (TypeError, ValueError):
                continue
            if value != value or value in (float("inf"), float("-inf")):  # NaN or Inf
                continue
            out[app] = value
        return out

    def collect(self, services: list[str]) -> dict[str, MetricSample]:
        """One sample per service. Services with no traffic yet are skipped."""
        results = {name: self.query(q) for name, q in QUERIES.items()}
        samples = {}
        for service in services:
            if service not in results["request_rate"] and service not in results["heap_ratio"]:
                continue
            samples[service] = MetricSample(
                latency_p95_ms=results["latency_p95_ms"].get(service, 0.0),
                # No 5xx series at all means no errors.
                error_rate=results["error_rate"].get(service, 0.0),
                heap_ratio=results["heap_ratio"].get(service, 0.0),
                request_rate=results["request_rate"].get(service, 0.0),
            )
        return samples
