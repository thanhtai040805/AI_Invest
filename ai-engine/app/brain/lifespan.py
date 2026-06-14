"""FastAPI lifespan — start/stop DNSE WebSocket hub + SessionService."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.services.dnse.stream_hub import get_stream_hub
from app.services.dnse.rest_client import get_rest_client
from app.database.models import init_db
from app.modules.news import news_module

# ── Logging (attach to root so it survives uvicorn) ────────────────────
root = logging.getLogger()
root.setLevel(logging.INFO)
if not root.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(_h)
# Silence noisy libs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("groq").setLevel(logging.WARNING)
logging.getLogger("langgraph").setLevel(logging.WARNING)
logging.getLogger("langchain").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)
logging.getLogger("playwright").setLevel(logging.WARNING)
# Bump our own loggers to DEBUG so user sees full ReAct trace
logging.getLogger("app.brain.agents.core.loop").setLevel(logging.DEBUG)
logging.getLogger("app.brain.state.service").setLevel(logging.DEBUG)


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
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    init_db()

    logger = logging.getLogger("ai_engine.lifespan")
    logger.info("=" * 50)
    logger.info("AIInvest Engine starting — AI flow logging active")
    logger.info("=" * 50)

    hub = get_stream_hub()

    hub.start()
    news_module.start()

    # Wire SessionService into app.state
    svc = get_session_service()
    svc.event_bus.set_loop(asyncio.get_running_loop())
    app.state.session_service = svc

    yield

    hub.stop()
    news_module.stop()
    get_rest_client().close()
    # Clear any lingering SSE subscribers
    svc.event_bus.clear("__all__")
