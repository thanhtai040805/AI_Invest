"""FastAPI lifespan — start/stop DNSE WebSocket hub + SessionService."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client
from app.services.news_ingestion import get_news_ingestion_service
from app.database.models import init_db


_session_service: "SessionService | None" = None  # noqa: F821


def get_session_service() -> "SessionService":  # noqa: F821
    from app.brain.state.service import SessionService
    from app.brain.state.events import EventBus
    from app.brain.state.session_store import SessionStore

    global _session_service
    if _session_service is None:
        store = SessionStore()
        event_bus = EventBus()
        runs_dir = Path(__file__).resolve().parents[1] / "runs"
        runs_dir.mkdir(exist_ok=True)
        _session_service = SessionService(store=store, event_bus=event_bus, runs_dir=runs_dir)
    return _session_service


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()

    hub = get_stream_hub()
    news_service = get_news_ingestion_service()

    hub.start()
    news_service.start()

    # Wire SessionService into app.state
    svc = get_session_service()
    svc.event_bus.set_loop(asyncio.get_running_loop())
    app.state.session_service = svc

    yield

    hub.stop()
    news_service.stop()
    get_rest_client().close()
    # Clear any lingering SSE subscribers
    svc.event_bus.clear("__all__")
