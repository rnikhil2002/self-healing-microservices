"""HTTP API for the healer plus the background control loop.

    uvicorn healer.main:app --port 8090
"""

from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .config import Settings
from .controller import HealingController
from .prometheus import PrometheusClient
from .remediation import build_remediator
from .summarizer import Summarizer

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s - %(message)s")

state: dict = {}


def build_controller(settings: Settings) -> HealingController:
    return HealingController(
        settings=settings,
        prometheus=PrometheusClient(settings.prometheus_url),
        remediator=build_remediator(settings.remediation_mode, settings.namespace),
        summarizer=Summarizer(settings.openai_api_key, settings.openai_model),
    )


@asynccontextmanager
async def lifespan(_: FastAPI):
    controller = build_controller(Settings())
    thread = threading.Thread(target=controller.run_forever, name="healer-loop", daemon=True)
    thread.start()
    state["controller"] = controller
    yield
    controller.stop()


app = FastAPI(title="Self-healing controller", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    return {"status": "UP"}


@app.get("/status")
def status() -> dict:
    return state["controller"].status()


@app.get("/incidents")
def incidents() -> list[dict]:
    return state["controller"].store.all()


@app.get("/metrics")
def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
