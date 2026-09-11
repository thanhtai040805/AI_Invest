"""Measure MinerU provider concurrency for one API key with ten direct URL tasks."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import datetime, timezone

import httpx
import psycopg2

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sag_api.core.config import settings
from sag_api.parsing.mineru import MinerUClient


def source_urls(limit: int, db_url: str) -> list[tuple[str, str]]:
    with psycopg2.connect(db_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT DISTINCT upper(symbol), unnest(article_pdf_urls)
                FROM knowledge_documents
                WHERE symbol IS NOT NULL
                  AND article_pdf_urls IS NOT NULL
                  AND cardinality(article_pdf_urls) > 0
                ORDER BY upper(symbol)
                LIMIT 80
                """
            )
            return [(str(symbol), str(url)) for symbol, url in cur.fetchall() if url][:limit]


async def main(limit: int, db_url: str) -> int:
    # Force every client in this probe to use the primary key only.
    settings.mineru_api_keys = ""
    candidates = source_urls(limit, db_url)
    if len(candidates) < limit:
        raise RuntimeError(f"Chỉ tìm thấy {len(candidates)} source URL, cần {limit}")

    events: list[tuple[float, str, str]] = []
    lock = asyncio.Lock()
    started = time.monotonic()

    async def run_one(index: int, ticker: str, url: str) -> dict[str, object]:
        async def on_state(state: dict[str, object]) -> None:
            provider_state = str(
                state.get("provider_state")
                or ("done" if state.get("status") == "done" else "submitted")
            )
            elapsed = round(time.monotonic() - started, 1)
            async with lock:
                events.append((time.monotonic(), f"{ticker}_{index + 1}", provider_state))
            print(f"{elapsed:7.1f}s {ticker}_{index + 1:02d} {provider_state}", flush=True)

        try:
            client = MinerUClient(settings)
            markdown = await client.parse_url(url, f"{ticker}_{index + 1}.pdf", on_state=on_state)
            return {"ticker": ticker, "status": "done", "chars": len(markdown)}
        except Exception as exc:  # noqa: BLE001
            return {"ticker": ticker, "status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    print(f"Submitting {len(candidates)} tasks with ONE MinerU API key", flush=True)
    results = await asyncio.gather(
        *(run_one(index, ticker, url) for index, (ticker, url) in enumerate(candidates))
    )
    peak_running = 0
    peak_pending = 0
    # Reconstruct the observed counts from state transitions in event order.
    current: dict[str, str] = {}
    for _timestamp, task, state in sorted(events):
        current[task] = state
        peak_running = max(peak_running, sum(value == "running" for value in current.values()))
        peak_pending = max(peak_pending, sum(value == "pending" for value in current.values()))

    print({
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "one_key": True,
        "submitted": len(candidates),
        "peak_running_observed": peak_running,
        "peak_pending_observed": peak_pending,
        "results": results,
    }, flush=True)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument(
        "--db-url",
        default=os.getenv("AI_ENGINE_DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest"),
    )
    args = parser.parse_args()
    raise SystemExit(asyncio.run(main(max(1, args.count), args.db_url)))
