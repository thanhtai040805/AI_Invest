from __future__ import annotations

import argparse
import asyncio
import logging
import signal
from contextlib import suppress
from pathlib import Path

from sag_api.core.config import settings
from sag_api.core.db import SessionLocal, dispose_db, init_db
from sag_api.enums import ProcessingStageStatus
from sag_api.services.financial_v2_service import process_document_run
from sag_api.services.processing_run_service import claim_next_processing_run, heartbeat_processing_run

log = logging.getLogger("sag_api.worker")


class Worker:
    def __init__(self, *, once: bool = False, stage: str = "document") -> None:
        self.once = once
        self.stage = stage
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run(self) -> None:
        await init_db()
        try:
            while not self._stop.is_set():
                processed = await self._tick()
                if self.once:
                    return
                if not processed:
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._stop.wait(), timeout=float(settings.worker_poll_seconds))
        finally:
            await dispose_db()

    async def _tick(self) -> bool:
        async with SessionLocal() as session:
            run = await claim_next_processing_run(
                session,
                stage=self.stage,
                lease_seconds=int(settings.processing_lease_seconds),
            )
            await session.commit()
        if run is None:
            return False

        async def heartbeat() -> None:
            while True:
                await asyncio.sleep(float(settings.processing_heartbeat_seconds))
                async with SessionLocal() as heartbeat_session:
                    current = await heartbeat_session.get(type(run), run.id)
                    if current is None or current.status != ProcessingStageStatus.RUNNING.value:
                        return
                    await heartbeat_processing_run(
                        heartbeat_session,
                        current,
                        lease_seconds=int(settings.processing_lease_seconds),
                    )
                    await heartbeat_session.commit()

        heartbeat_task = asyncio.create_task(heartbeat(), name=f"processing-heartbeat-{run.id}")

        try:
            async with SessionLocal() as session:
                run = await session.merge(run)
                try:
                    await process_document_run(session, run)
                    await session.commit()
                except Exception:
                    await session.rollback()
                    log.exception("processing run failed unexpectedly: %s", run.id)
                    async with SessionLocal() as fail_session:
                        failed = await fail_session.merge(run)
                        failed.status = ProcessingStageStatus.FAILED.value
                        failed.error = "worker_unhandled_exception"
                        await fail_session.commit()
        finally:
            heartbeat_task.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat_task
        return True


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="sag-worker", description="SAG v2 PostgreSQL-leased document worker")
    parser.add_argument("--once", action="store_true", help="Claim and process at most one job, then exit.")
    parser.add_argument("--stage", default="document", help="Processing run stage to claim.")
    parser.add_argument("--log-file", default=None, help="UTF-8 log file for a long-lived worker.")
    args = parser.parse_args(argv)

    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if args.log_file:
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        handlers=handlers,
        force=True,
    )
    worker = Worker(once=bool(args.once), stage=str(args.stage))
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    for signame in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, signame, None)
        if sig is not None:
            with suppress(NotImplementedError):
                loop.add_signal_handler(sig, worker.stop)
    loop.run_until_complete(worker.run())


if __name__ == "__main__":
    main()
