"""Settings, all overridable through environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_list(name: str, default: str) -> list[str]:
    return [s.strip() for s in os.getenv(name, default).split(",") if s.strip()]


@dataclass
class Settings:
    prometheus_url: str = field(default_factory=lambda: os.getenv("PROMETHEUS_URL", "http://localhost:9090"))
    services: list[str] = field(default_factory=lambda: _env_list("SERVICES", "order-service,payment-service"))
    poll_interval_s: float = field(default_factory=lambda: float(os.getenv("POLL_INTERVAL_SECONDS", "5")))
    # Samples collected while the system is healthy before the model is trained.
    warmup_samples: int = field(default_factory=lambda: int(os.getenv("WARMUP_SAMPLES", "24")))
    # Consecutive anomalous samples needed before acting, to avoid reacting to a blip.
    anomaly_streak: int = field(default_factory=lambda: int(os.getenv("ANOMALY_STREAK", "3")))
    cooldown_s: float = field(default_factory=lambda: float(os.getenv("COOLDOWN_SECONDS", "120")))
    # kubernetes | docker | dry-run
    remediation_mode: str = field(default_factory=lambda: os.getenv("REMEDIATION_MODE", "dry-run"))
    namespace: str = field(default_factory=lambda: os.getenv("NAMESPACE", "self-healing"))
    max_replicas: int = field(default_factory=lambda: int(os.getenv("MAX_REPLICAS", "4")))
    # A deployment rolled out within this window is suspected first when errors spike.
    rollback_window_s: float = field(default_factory=lambda: float(os.getenv("ROLLBACK_WINDOW_SECONDS", "900")))
    openai_api_key: str | None = field(default_factory=lambda: os.getenv("OPENAI_API_KEY") or None)
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-4o-mini"))
    incidents_file: str = field(default_factory=lambda: os.getenv("INCIDENTS_FILE", "incidents.jsonl"))
