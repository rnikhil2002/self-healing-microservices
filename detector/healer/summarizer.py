"""Plain-English incident summaries.

Uses the OpenAI Chat Completions API when OPENAI_API_KEY is set. Without a key, or if the
call fails, it falls back to a template so incidents always get a readable summary.
"""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are an SRE assistant. Write a short incident summary for an on-call engineer: "
    "what broke, the likely root cause based on the metrics, what automated action was "
    "taken, and what to check next. Three to five sentences, no markdown."
)


def template_summary(incident: dict) -> str:
    action = incident.get("action") or "no action"
    result = "succeeded" if incident.get("action_success") else "did not succeed"
    z = incident.get("zscores", {})
    top = max(z, key=lambda k: abs(z[k])) if z else "metrics"
    return (
        f"{incident['service']} showed {incident['incident_type'].replace('_', ' ').lower()}: "
        f"{incident['reason']}. The biggest deviation was in {top} "
        f"({z.get(top, 0):+.1f} standard deviations from its baseline). "
        f"The healer chose {action.replace('_', ' ').lower()}, which {result}: {incident.get('action_detail', '')}. "
        f"Check the latest deployment and the service logs around "
        f"{incident.get('detected_at', 'the detection time')}."
    )


class Summarizer:
    def __init__(self, api_key: str | None, model: str = "gpt-4o-mini", timeout: float = 20.0):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def summarize(self, incident: dict) -> tuple[str, str]:
        """Returns (summary, source) where source is 'llm' or 'template'."""
        if not self.api_key:
            return template_summary(incident), "template"
        try:
            resp = requests.post(
                "https://api.openai.com/v1/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "temperature": 0.2,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": json.dumps(incident, default=str)},
                    ],
                },
                timeout=self.timeout,
            )
            resp.raise_for_status()
            text = resp.json()["choices"][0]["message"]["content"].strip()
            return text, "llm"
        except Exception as exc:  # noqa: BLE001 - never let the LLM break remediation
            log.warning("LLM summary failed, using template: %s", exc)
            return template_summary(incident), "template"
