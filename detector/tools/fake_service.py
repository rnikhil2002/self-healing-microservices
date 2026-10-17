"""A stand-in for a Spring Boot service that only serves Prometheus metrics.

It emits the same metric names and labels Micrometer does (http_server_requests_seconds_*,
jvm_memory_*), simulates ~20 requests a second internally, and accepts the same /chaos
calls as the real services. Useful for trying the healer without building the Java apps:

    python tools/fake_service.py --name payment-service --port 9101
    curl -X POST 'localhost:9101/chaos/errors?rate=0.5'
"""

from __future__ import annotations

import argparse
import random
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

BUCKETS = [0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0]
HEAP_MAX = 256 * 1024 * 1024


class FakeService:
    def __init__(self, name: str, rate: float = 20.0):
        self.name = name
        self.rate = rate
        self.lock = threading.Lock()
        self.latency_ms = 0
        self.error_rate = 0.0
        self.leak_mb_per_s = 0
        self.leaked = 0
        self.counts = {"200": 0, "500": 0}
        self.sums = {"200": 0.0, "500": 0.0}
        self.buckets = {"200": [0] * len(BUCKETS), "500": [0] * len(BUCKETS)}

    def simulate(self) -> None:
        while True:
            time.sleep(1)
            with self.lock:
                for _ in range(int(self.rate)):
                    seconds = (random.uniform(15, 60) + self.latency_ms * random.uniform(1.0, 1.2)) / 1000
                    status = "500" if random.random() < self.error_rate else "200"
                    self.counts[status] += 1
                    self.sums[status] += seconds
                    for i, le in enumerate(BUCKETS):
                        if seconds <= le:
                            self.buckets[status][i] += 1
                if self.leak_mb_per_s:
                    self.leaked = min(self.leaked + self.leak_mb_per_s, 200)

    def metrics(self) -> str:
        lines = ["# TYPE http_server_requests_seconds histogram"]
        base = f'application="{self.name}",method="POST",uri="/api/payments/authorize"'
        with self.lock:
            for status in ("200", "500"):
                if self.counts[status] == 0:
                    continue
                outcome = "SUCCESS" if status == "200" else "SERVER_ERROR"
                labels = f'{base},status="{status}",outcome="{outcome}"'
                for i, le in enumerate(BUCKETS):
                    lines.append(f'http_server_requests_seconds_bucket{{{labels},le="{le}"}} {self.buckets[status][i]}')
                lines.append(f'http_server_requests_seconds_bucket{{{labels},le="+Inf"}} {self.counts[status]}')
                lines.append(f"http_server_requests_seconds_count{{{labels}}} {self.counts[status]}")
                lines.append(f"http_server_requests_seconds_sum{{{labels}}} {self.sums[status]:.4f}")
            used = (70 + self.leaked + random.uniform(-5, 5)) * 1024 * 1024
        lines += [
            "# TYPE jvm_memory_used_bytes gauge",
            f'jvm_memory_used_bytes{{application="{self.name}",area="heap",id="G1 Old Gen"}} {used:.0f}',
            f'jvm_memory_used_bytes{{application="{self.name}",area="heap",id="G1 Eden Space"}} {8 * 1024 * 1024}',
            "# TYPE jvm_memory_max_bytes gauge",
            f'jvm_memory_max_bytes{{application="{self.name}",area="heap",id="G1 Old Gen"}} {HEAP_MAX}',
            f'jvm_memory_max_bytes{{application="{self.name}",area="heap",id="G1 Eden Space"}} -1',
        ]
        return "\n".join(lines) + "\n"


def serve(service: FakeService, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _reply(self, code: int, body: str, ctype: str = "application/json"):
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path.startswith("/actuator/prometheus"):
                self._reply(200, service.metrics(), "text/plain; version=0.0.4")
            elif self.path.startswith("/actuator/health"):
                self._reply(200, '{"status":"UP"}')
            else:
                self._reply(404, "{}")

        def do_POST(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            with service.lock:
                if url.path == "/chaos/errors":
                    service.error_rate = float(q.get("rate", 0))
                elif url.path == "/chaos/latency":
                    service.latency_ms = int(q.get("ms", 0))
                elif url.path == "/chaos/memory-leak":
                    service.leak_mb_per_s = int(q.get("mbPerSecond", 8))
                elif url.path == "/chaos/reset":
                    service.error_rate, service.latency_ms, service.leak_mb_per_s, service.leaked = 0.0, 0, 0, 0
                else:
                    self._reply(404, "{}")
                    return
            self._reply(200, '{"ok":true}')

    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=service.simulate, daemon=True).start()
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    serve(FakeService(args.name), args.port).serve_forever()
