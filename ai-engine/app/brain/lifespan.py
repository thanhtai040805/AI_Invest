"""FastAPI lifespan — start/stop DNSE WebSocket hub + SessionService + News scheduler."""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from app.infrastructure.external_api.dnse.stream_hub import get_stream_hub
from app.infrastructure.external_api.dnse.rest_client import get_rest_client
from app.infrastructure.database.models import init_db
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

    # ── Start news background scheduler (15-min interval) ─────────────
    _news_task: asyncio.Task | None = None

    async def _news_loop():
        """Fetch new CafeF news listing + deep crawl content every 15 minutes."""
        log = logging.getLogger("ai_engine.news_scheduler")
        log.info("News scheduler started (15-min interval)")
        while True:
            try:
                from app.infrastructure.knowledge_base.crawlers.vn.cafef_listing_crawl import refresh_listing
                result = await asyncio.to_thread(refresh_listing, max_pages=1, deep_crawl=True)
                inserted = result.get("inserted", 0)
                deep = result.get("deep_crawl", {})
                log.info("Listing: %d new | Deep: %s", inserted, deep.get("crawled", "N/A"))
            except Exception as e:
                log.warning("News scheduler cycle failed: %s", e)
            await asyncio.sleep(900)  # 15 min

    _news_task = asyncio.create_task(_news_loop())

    # Wire SessionService into app.state
    svc = get_session_service()
    svc.event_bus.set_loop(asyncio.get_running_loop())
    app.state.session_service = svc

    yield

    if _news_task is not None:
        _news_task.cancel()
    hub.stop()
    get_rest_client().close()
    # Clear any lingering SSE subscribers
    svc.event_bus.clear("__all__")
