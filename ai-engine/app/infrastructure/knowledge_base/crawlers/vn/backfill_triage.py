"""
backfill_triage.py — Rate-limited AI triage for backfill.
Processes documents from knowledge_documents WHERE triaged_at IS NULL
in controlled batches to avoid Groq rate limits.

Usage:
  python -m app.infrastructure.knowledge_base.crawlers.vn.backfill_triage
  python -m app.infrastructure.knowledge_base.crawlers.vn.backfill_triage --rpm 20 --concurrency 2 --limit 500
  python -m app.infrastructure.knowledge_base.crawlers.vn.backfill_triage --dry-run
"""
import asyncio, logging, time, json
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from app.config.settings import get_settings
from app.infrastructure.database.pg_pool import get_cursor
from app.infrastructure.knowledge_base.crawlers.vn.triage_engine import get_triage_engine

logger = logging.getLogger(__name__)

class TokenBucket:
    """Sliding-window rate limiter to stay under API RPM limit."""

    def __init__(self, rpm: int = 25, max_concurrent: int = 2):
        self.rpm = rpm
        self.semaphore = asyncio.Semaphore(max_concurrent)
        self._timestamps: deque[float] = deque()

    async def acquire(self):
        await self.semaphore.acquire()
        while True:
            now = time.monotonic()
            # forget calls older than 60s
            while self._timestamps and now - self._timestamps[0] > 60:
                self._timestamps.popleft()
            if len(self._timestamps) < self.rpm:
                self._timestamps.append(now)
                return True
            # wait until the oldest timestamp expires
            wait = self._timestamps[0] + 60 - now
            await asyncio.sleep(max(wait, 0.1))

    def release(self):
        self.semaphore.release()


def count_pending() -> int:
    with get_cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM knowledge_documents WHERE triaged_at IS NULL")
        return cur.fetchone()[0]


def fetch_pending(limit: int = 500) -> list[dict]:
    with get_cursor() as cur:
        cur.execute("""
            SELECT id, symbol, url, title, article_content, article_pdf_text
            FROM knowledge_documents
            WHERE triaged_at IS NULL
            ORDER BY published_date DESC
            LIMIT %s
        """, (limit,))
        cols = [desc[0] for desc in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def update_triage(art: dict):
    with get_cursor() as cur:
        cur.execute("""
            UPDATE knowledge_documents SET
                event_type = %s,
                severity = %s,
                ai_sentiment_score = %s,
                ai_summary = %s,
                sentiment_score = %s,
                direction = %s,
                magnitude = %s,
                investment_impact = %s,
                materiality = %s,
                materiality_score = %s,
                surprise_score = %s,
                business_horizon = %s,
                pricing_horizon = %s,
                persistence = %s,
                persistence_score = %s,
                reversibility = %s,
                apparent_novelty = %s,
                novelty = %s,
                evidence_strength = %s,
                credibility = %s,
                affected_entities = %s,
                triaged_at = NOW()
            WHERE id = %s AND triaged_at IS NULL
        """, (
            art.get("event_type"), art.get("severity"),
            art.get("ai_sentiment_score"), art.get("ai_summary"),
            art.get("sentiment_score"),
            art.get("direction"), art.get("magnitude"), art.get("investment_impact"),
            art.get("materiality"), art.get("materiality_score"), art.get("surprise_score"),
            art.get("business_horizon"), art.get("pricing_horizon"), art.get("persistence"),
            art.get("persistence_score"), art.get("reversibility"), art.get("apparent_novelty"),
            art.get("novelty"), art.get("evidence_strength"), art.get("credibility"),
            json.dumps(art.get("affected_entities")) if art.get("affected_entities") is not None else None,
            art["id"],
        ))


async def run(
    rpm: int = 25,
    concurrency: int = 2,
    limit: int = 500,
    dry_run: bool = False,
    batch_size: int = 10,
):
    triage = get_triage_engine()
    bucket = TokenBucket(rpm=rpm, max_concurrent=concurrency)

    pending = fetch_pending(limit)
    if not pending:
        logger.info("No pending documents to triage.")
        return {"status": "done", "triaged": 0}

    if dry_run:
        estimated_tokens = sum(len(a.get("article_content", "") or "") for a in pending)
        logger.info("DRY RUN: %d documents, ~%d chars, ~%d tokens (est.)",
                     len(pending), estimated_tokens, estimated_tokens // 4)
        return {"status": "dry_run", "count": len(pending), "est_chars": estimated_tokens}

    total = len(pending)
    done = 0
    errors = 0
    t0 = time.monotonic()

    # process in small batches for progress visibility
    for batch_start in range(0, total, batch_size):
        batch = pending[batch_start: batch_start + batch_size]

        async def process_one(art: dict):
            nonlocal errors
            try:
                await bucket.acquire()
                result = await triage.triage_article(art)
                art.update(result)
                art["sentiment_score"] = result["ai_sentiment_score"]
                update_triage(art)
            except Exception as e:
                logger.error("Triage failed id=%d: %s", art["id"], e)
                errors += 1
            finally:
                bucket.release()

        await asyncio.gather(*[process_one(art) for art in batch])
        done += len(batch)

        elapsed = time.monotonic() - t0
        rate = done / elapsed if elapsed > 0 else 0
        logger.info("Progress: %d/%d (%.1f%%), %.1f docs/min, %d errors",
                     done, total, done / total * 100, rate * 60, errors)

    elapsed = time.monotonic() - t0
    logger.info("Triage complete: %d docs in %.1f min (%.1f docs/min), %d errors",
                 done, elapsed / 60, done / elapsed * 60 if elapsed > 0 else 0, errors)
    return {"status": "done", "triaged": done, "errors": errors, "elapsed_min": round(elapsed / 60, 1)}


def refresh_triage(rpm: int = 25, concurrency: int = 2, limit: int = 500, dry_run: bool = False):
    return asyncio.run(run(rpm=rpm, concurrency=concurrency, limit=limit, dry_run=dry_run))


if __name__ == "__main__":
    import sys
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    args = sys.argv[1:]
    kwargs = {}
    for a in args:
        if a.startswith("--rpm="):
            kwargs["rpm"] = int(a.split("=", 1)[1])
        elif a.startswith("--concurrency="):
            kwargs["concurrency"] = int(a.split("=", 1)[1])
        elif a.startswith("--limit="):
            kwargs["limit"] = int(a.split("=", 1)[1])
        elif a == "--dry-run":
            kwargs["dry_run"] = True

    pending = count_pending()
    logger.info("Pending documents needing triage: %d", pending)
    result = refresh_triage(**kwargs)
    logger.info("Result: %s", result)
