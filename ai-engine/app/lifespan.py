"""App-level FastAPI lifespan — infrastructure + AI lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.brain.lifespan import lifespan as brain_lifespan
from app.infrastructure.database.pg_pool import migrate as pg_migrate

logger = logging.getLogger("ai_engine.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Infrastructure startup
    try:
        pg_migrate()
        logger.info("PostgreSQL migration applied")
    except Exception as e:
        logger.warning(f"PostgreSQL migration failed: {e}")

    # NO auto-backfill — on-demand only
    # Delegate to brain lifespan for AI-specific lifecycle
    async with brain_lifespan(app):
        yield
