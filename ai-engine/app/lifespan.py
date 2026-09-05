"""App-level FastAPI lifespan — infrastructure + AI lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

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

    # Khởi động EOD Learning Daemon (15:15 EOD Cron) và Position Monitoring Daemon
    tasks = []
    try:
        from app.infrastructure.workers.eod_learning_daemon import eod_daemon
        eod_task = asyncio.create_task(eod_daemon.start())
        tasks.append((eod_task, eod_daemon))
        logger.info("[Lifespan] EOD Learning Daemon (15:15 Cron) đã được khởi động.")
    except Exception as e_eod:
        logger.warning(f"[Lifespan] Không thể khởi động EOD Learning Daemon: {e_eod}")

    try:
        from app.infrastructure.workers.position_monitoring_daemon import daemon as pos_daemon
        pos_task = asyncio.create_task(pos_daemon.start())
        tasks.append((pos_task, pos_daemon))
        logger.info("[Lifespan] Position Monitoring Daemon đã được khởi động.")
    except Exception as e_pos:
        logger.warning(f"[Lifespan] Không thể khởi động Position Monitoring Daemon: {e_pos}")

    yield

    # Dừng an toàn các Daemon khi tắt server (Graceful Shutdown)
    for task, d in tasks:
        try:
            d.stop()
            task.cancel()
        except Exception:
            pass
    logger.info("[Lifespan] Đã dừng toàn bộ background daemons.")
