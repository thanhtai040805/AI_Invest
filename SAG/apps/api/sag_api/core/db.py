"""Async database engine/session wiring for SAG.

Alembic owns schema creation. Runtime API/worker deployments are PostgreSQL
first; SQLite is only accepted when Settings explicitly enables the test/legacy
escape hatch.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from sag_api.core.config import settings


def _ensure_sqlite_dir(url: str) -> None:
    """Create a SQLite parent directory only for explicit test/legacy mode."""
    marker = "sqlite+aiosqlite:///"
    if url.startswith(marker):
        path = url[len(marker) :]
        if path and path not in (":memory:",):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)


if settings.database_url.startswith("sqlite"):
    if not settings.allow_sqlite_runtime:
        raise RuntimeError(
            "SAG runtime attempted to start with SQLite while SAG_ALLOW_SQLITE_RUNTIME is false. "
            "Use PostgreSQL for API/worker/E2E runtime."
        )
    _ensure_sqlite_dir(settings.database_url)

_engine_options = {
    "echo": False,
    "future": True,
    "pool_pre_ping": True,
}
if not settings.database_url.startswith("sqlite"):
    _engine_options.update(
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
    )

engine: AsyncEngine = create_async_engine(settings.database_url, **_engine_options)

if settings.database_url.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()
else:

    @event.listens_for(engine.sync_engine, "connect")
    def _postgres_search_path(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("SET search_path TO sag, public")
        cur.close()


SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """Register ORM models; DDL is owned by Alembic/sagctl, not runtime startup."""
    from sag_api.db import models  # noqa: F401

    return None


async def dispose_db() -> None:
    await engine.dispose()
