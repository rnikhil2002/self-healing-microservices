"""Steady order traffic so the healer has a baseline to learn from.

    python loadgen.py --url http://localhost:8081 --rate 20
"""

from __future__ import annotations

import argparse
import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests

log = logging.getLogger("loadgen")
SKUS = [f"SKU-{i:03d}" for i in range(1, 60)]


def make_order(rng: random.Random) -> dict:
    items = [{"sku": rng.choice(SKUS), "quantity": rng.randint(1, 3), "price": round(rng.uniform(3, 120), 2)}
             for _ in range(rng.randint(1, 4))]
    return {"customerId": f"cust-{rng.randint(1, 500):04d}", "items": items}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://localhost:8081")
    parser.add_argument("--rate", type=float, default=20.0, help="orders per second")
    parser.add_argument("--duration", type=float, default=None)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    rng = random.Random()
    session = requests.Session()
    stats = {"ok": 0, "error": 0}
    lock = threading.Lock()

    def send() -> None:
        try:
            r = session.post(f"{args.url}/api/orders", json=make_order(rng), timeout=10)
            key = "ok" if r.status_code < 500 else "error"
        except requests.RequestException:
            key = "error"
        with lock:
            stats[key] += 1

    started = last_report = time.time()
    interval = 1.0 / args.rate
    with ThreadPoolExecutor(max_workers=32) as pool:
        while args.duration is None or time.time() - started < args.duration:
            pool.submit(send)
            time.sleep(interval)
            if time.time() - last_report >= 30:
                with lock:
                    log.info("sent ok=%d error=%d", stats["ok"], stats["error"])
                last_report = time.time()


if __name__ == "__main__":
    main()
