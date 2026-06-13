"""News Event Store and labeling helpers.

Provides a simple news_events table to record ingested news with timestamps and
compute ingest latency. Also supplies a labeling helper script that computes
forward returns for event-study labeling (e.g., next 1/3/5 day returns).

This implementation is intentionally simple and synchronous; it is meant as a
starter module to integrate with existing news_ingestion and market_data_service.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL
from app.services.market_data_service import market_data_svc

logger = logging.getLogger(__name__)

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS news_events (
    id SERIAL PRIMARY KEY,
    source TEXT,
    symbol TEXT,
    title TEXT,
    body TEXT,
    published_at timestamptz,
    ingest_at timestamptz DEFAULT now(),
    sentiment numeric,
    labels jsonb,
    latency_ms integer,
    processed boolean DEFAULT false,
    created_at timestamptz DEFAULT now()
);
"""


class NewsEventStore:
    def __init__(self, db_url: str = DB_URL):
        self.db_url = db_url
        self._ensure_table()

    def _get_conn(self):
        return psycopg2.connect(self.db_url)

    def _ensure_table(self):
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute(CREATE_SQL)
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.exception("Failed to ensure news_events table: %s", e)

    def insert_event(self, source: str, symbol: str, title: str, body: str, published_at: Optional[datetime], sentiment: Optional[float] = None) -> Optional[int]:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            pub_ts = published_at.astimezone(timezone.utc) if published_at else None
            ingest_ts = datetime.now(timezone.utc)
            latency_ms = None
            if pub_ts:
                latency_ms = int((ingest_ts - pub_ts).total_seconds() * 1000)
            cur.execute(
                """
                INSERT INTO news_events (source, symbol, title, body, published_at, ingest_at, sentiment, latency_ms)
                VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id
                """,
                (source, symbol, title, body, pub_ts, ingest_ts, sentiment),
            )
            row = cur.fetchone()
            conn.commit()
            cur.close()
            conn.close()
            return row[0] if row else None
        except Exception as e:
            logger.exception("Failed to insert news event: %s", e)
            return None

    def fetch_unlabeled(self, limit: int = 100) -> list:
        try:
            conn = self._get_conn()
            cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
            cur.execute("SELECT * FROM news_events WHERE processed = false ORDER BY id LIMIT %s", (limit,))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return [dict(r) for r in rows]
        except Exception as e:
            logger.exception("Failed fetch unlabeled news: %s", e)
            return []

    def mark_processed(self, event_id: int, labels: Dict[str, Any]) -> None:
        try:
            conn = self._get_conn()
            cur = conn.cursor()
            cur.execute("UPDATE news_events SET processed = true, labels = %s WHERE id = %s", (json.dumps(labels), event_id))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.exception("Failed mark_processed for %s: %s", event_id, e)


news_store = NewsEventStore()


# ---------------------------------------------------------------------------
# Labeling helper: event-study style labels (next N trading day return)
# ---------------------------------------------------------------------------

def compute_forward_return(symbol: str, published_date: str, forward_days: int = 5) -> Optional[float]:
    """Compute forward % return over next forward_days trading days using OHLCV daily data.

    published_date: ISO date string YYYY-MM-DD or full timestamp
    """
    try:
        # normalize to date portion
        if "T" in published_date:
            d = published_date.split("T")[0]
        else:
            d = published_date[:10]

        # fetch OHLCV including next days
        import asyncio
        ohlcv = asyncio.run(market_data_svc.get_ohlcv(symbol, interval="1D", start=d, end=None))
        data = ohlcv.get("data", [])
        if not data:
            return None
        # find index of published day
        dates = [str(x.get("time"))[:10] for x in data]
        try:
            idx = dates.index(d)
        except ValueError:
            # published date may be intra-day; find first index > published_date
            idx = 0
        target_idx = idx + forward_days
        if target_idx >= len(data):
            return None
        open_price = float(data[idx].get("close", 0))
        future_price = float(data[target_idx].get("close", 0))
        if open_price == 0:
            return None
        return (future_price / open_price) - 1.0
    except Exception as e:
        logger.exception("Failed compute_forward_return %s %s: %s", symbol, published_date, e)
        return None


def label_unlabeled_events(forward_days: int = 5, batch: int = 100) -> int:
    """Fetch unlabeled events, compute forward return and mark processed with label info.

    Returns number of processed events.
    """
    rows = news_store.fetch_unlabeled(limit=batch)
    count = 0
    for r in rows:
        ev_id = r.get("id")
        sym = r.get("symbol")
        pub = r.get("published_at")
        if not pub or not sym:
            news_store.mark_processed(ev_id, {"error": "missing pub/sym"})
            count += 1
            continue
        # compute forward return
        fwd = compute_forward_return(sym, str(pub), forward_days=forward_days)
        labels = {"forward_days": forward_days, "forward_return": fwd}
        news_store.mark_processed(ev_id, labels)
        count += 1
    return count
