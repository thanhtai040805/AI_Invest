"""App-level FastAPI lifespan — infrastructure + AI lifecycle."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.infrastructure.database.pg_pool import migrate as pg_migrate, run_agent_migrations

logger = logging.getLogger("ai_engine.lifespan")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── 1. Database Connectivity Check ───────────────────────────────
    try:
        pg_migrate()
        logger.info("✅ [Lifespan] PostgreSQL connected & verified (Schema managed by Prisma).")
    except Exception as e:
        logger.warning(f"⚠️ [Lifespan] PostgreSQL connection check failed: {e}")

    # ── 2. RabbitMQ EventBus Connect ─────────────────────────────────
    try:
        from app.adapters.rabbitmq_event_bus import event_bus
        await event_bus.connect()
        logger.info(f"✅ [Lifespan] EventBus mode: {event_bus.mode}")
    except Exception as e_mq:
        logger.warning(f"⚠️ [Lifespan] EventBus connect failed: {e_mq}")

    # ── 3. Background Daemons ────────────────────────────────────────
    # Khởi động:
    # 1. Daily Pipeline Daemon (09:15 Mở phiên - 12 Agents & Standalone ML)
    # 2. Position Monitoring Daemon (09:00 - 14:45 Trong phiên - Realtime Ticks & Stop Loss)
    # 3. EOD Learning Daemon (15:15 Cuối phiên - Causal Learning & PnL Settlement)
    tasks = []
    try:
        from app.infrastructure.workers.daily_pipeline_daemon import daily_daemon
        daily_task = asyncio.create_task(daily_daemon.start())
        tasks.append((daily_task, daily_daemon))
        logger.info("[Lifespan] Daily Pipeline Daemon (09:15 Morning Cron) đã được khởi động.")
    except Exception as e_daily:
        logger.warning(f"[Lifespan] Không thể khởi động Daily Pipeline Daemon: {e_daily}")

    try:
        from app.infrastructure.workers.position_monitoring_daemon import daemon as pos_daemon
        pos_task = asyncio.create_task(pos_daemon.start())
        tasks.append((pos_task, pos_daemon))
        logger.info("[Lifespan] Position Monitoring Daemon đã được khởi động.")
    except Exception as e_pos:
        logger.warning(f"[Lifespan] Không thể khởi động Position Monitoring Daemon: {e_pos}")

    try:
        from app.infrastructure.workers.eod_learning_daemon import eod_daemon
        eod_task = asyncio.create_task(eod_daemon.start())
        tasks.append((eod_task, eod_daemon))
        logger.info("[Lifespan] EOD Learning Daemon (15:15 EOD Cron) đã được khởi động.")
    except Exception as e_eod:
        logger.warning(f"[Lifespan] Không thể khởi động EOD Learning Daemon: {e_eod}")

    try:
        from app.infrastructure.workers.daily_etl_daemon import etl_daemon
        etl_task = asyncio.create_task(etl_daemon.start())
        tasks.append((etl_task, etl_daemon))
        logger.info("[Lifespan] Daily ETL Daemon (18:00 Post-Market Cron) đã được khởi động.")
    except Exception as e_etl:
        logger.warning(f"[Lifespan] Không thể khởi động Daily ETL Daemon: {e_etl}")

    yield

    # ── Graceful Shutdown ────────────────────────────────────────────
    # Đóng EventBus
    try:
        from app.adapters.rabbitmq_event_bus import event_bus
        await event_bus.close()
    except Exception:
        pass

    # Dừng an toàn các Daemon khi tắt server
    for task, d in tasks:
        try:
            d.stop()
            task.cancel()
        except Exception:
            pass
    logger.info("[Lifespan] Đã dừng toàn bộ background daemons và EventBus.")

