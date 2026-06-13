import json
import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)

CREATE_SIGNAL_LOG_SQL = """
CREATE TABLE IF NOT EXISTS signal_log (
    id              SERIAL PRIMARY KEY,
    symbol          TEXT NOT NULL,
    signal_date     DATE NOT NULL,
    direction       TEXT NOT NULL,         -- BUY / SELL / HOLD
    confidence      REAL,
    entry_price     REAL,
    target_price    REAL,
    stop_loss       REAL,
    holding_period  INTEGER DEFAULT 5,     -- trading days
    source          TEXT,                   -- "factor", "hypothesis", "agent", "composite"
    source_agents   TEXT[],
    factors_used    TEXT[],
    hypothesis_id   TEXT,
    hypothesis_title TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    -- Eval fields (filled after holding period)
    evaluated_at    TIMESTAMPTZ,
    exit_price      REAL,
    actual_return   REAL,
    hit             BOOLEAN,               -- True if direction matches actual outcome
    hit_pct         REAL,                   -- return pct if hit, 0 otherwise
    max_favorable   REAL,
    max_adverse     REAL,
    eval_status     TEXT DEFAULT 'pending'  -- pending / evaluated / expired
);

CREATE INDEX IF NOT EXISTS idx_signal_log_eval_status ON signal_log (eval_status);
CREATE INDEX IF NOT EXISTS idx_signal_log_symbol_date ON signal_log (symbol, signal_date);
"""


class SignalTracker:
    def __init__(self):
        self._ensure_table()

    def _ensure_table(self):
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(CREATE_SIGNAL_LOG_SQL)
            conn.commit()
        finally:
            conn.close()

    def record_signal(self, signal: Dict[str, Any]) -> int:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO signal_log
                        (symbol, signal_date, direction, confidence,
                         entry_price, target_price, stop_loss,
                         holding_period, source, source_agents,
                         factors_used, hypothesis_id, hypothesis_title)
                    VALUES (%(symbol)s, %(signal_date)s, %(direction)s, %(confidence)s,
                            %(entry_price)s, %(target_price)s, %(stop_loss)s,
                            %(holding_period)s, %(source)s, %(source_agents)s,
                            %(factors_used)s, %(hypothesis_id)s, %(hypothesis_title)s)
                    RETURNING id
                """, {
                    "symbol": signal.get("symbol"),
                    "signal_date": signal.get("signal_date"),
                    "direction": signal.get("direction", "HOLD"),
                    "confidence": signal.get("confidence"),
                    "entry_price": signal.get("entry_price"),
                    "target_price": signal.get("target_price"),
                    "stop_loss": signal.get("stop_loss"),
                    "holding_period": signal.get("holding_period", 5),
                    "source": signal.get("source", "factor"),
                    "source_agents": signal.get("source_agents", []),
                    "factors_used": signal.get("factors_used", []),
                    "hypothesis_id": signal.get("hypothesis_id"),
                    "hypothesis_title": signal.get("hypothesis_title"),
                })
                signal_id = cur.fetchone()[0]
            conn.commit()
            return signal_id
        finally:
            conn.close()

    def evaluate_signal(self, signal_id: int, days_after: Optional[int] = None) -> Optional[Dict[str, Any]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("SELECT * FROM signal_log WHERE id = %s", (signal_id,))
                signal = cur.fetchone()
                if not signal:
                    logger.warning("Signal %s not found", signal_id)
                    return None

                holding = days_after or signal["holding_period"] or 5
                entry_date = signal["signal_date"]
                symbol = signal["symbol"]
                direction = signal["direction"]

                if direction == "HOLD":
                    self._mark_evaluated(conn, signal_id, None, None, True, 0.0)
                    return {"hit": True, "actual_return": 0.0}

                cur.execute("""
                    SELECT time, adj_close, close
                    FROM ohlcv
                    WHERE symbol = %s AND time >= %s
                    ORDER BY time
                    LIMIT %s
                """, (symbol, entry_date, holding + 2))

                rows = cur.fetchall()
                if len(rows) < 2:
                    self._mark_evaluated(conn, signal_id, None, None, None, None, "expired")
                    return None

                entry_price = float(rows[0].get("adj_close") or rows[0]["close"])
                price_col = "adj_close"

                prices = []
                for r in rows:
                    p = float(r.get(price_col) or r["close"])
                    prices.append(p)

                exit_price = prices[-1]
                actual_return = (exit_price / entry_price - 1) * 100

                if direction == "BUY":
                    hit = actual_return > 0
                elif direction == "SELL":
                    hit = actual_return < 0
                else:
                    hit = True

                max_fav = max(prices)
                max_adv = min(prices)

                if direction == "SELL":
                    max_fav_entry = (entry_price - max_adv) / entry_price * 100
                    max_adv_entry = (entry_price - max_fav) / entry_price * 100
                else:
                    max_fav_entry = (max_fav / entry_price - 1) * 100
                    max_adv_entry = (max_adv / entry_price - 1) * 100

                hit_pct = actual_return if hit else 0.0

                self._mark_evaluated(
                    conn, signal_id, exit_price, actual_return,
                    hit, hit_pct, max_fav_entry, max_adv_entry
                )

                return {
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "actual_return": actual_return,
                    "hit": hit,
                    "hit_pct": hit_pct,
                    "max_favorable": max_fav_entry,
                    "max_adverse": max_adv_entry,
                }
        finally:
            conn.close()

    def _mark_evaluated(self, conn, signal_id, exit_price, actual_return, hit, hit_pct,
                        max_fav=None, max_adv=None, status="evaluated"):
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE signal_log SET
                    evaluated_at = NOW(),
                    exit_price = %s,
                    actual_return = %s,
                    hit = %s,
                    hit_pct = %s,
                    max_favorable = %s,
                    max_adverse = %s,
                    eval_status = %s
                WHERE id = %s
            """, (exit_price, actual_return, hit, hit_pct,
                  max_fav, max_adv, status, signal_id))
        conn.commit()

    def evaluate_pending(self, limit: int = 100) -> List[Dict[str, Any]]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT id FROM signal_log
                    WHERE eval_status = 'pending'
                    ORDER BY signal_date
                    LIMIT %s
                """, (limit,))
                pending = cur.fetchall()
        finally:
            conn.close()

        results = []
        for row in pending:
            result = self.evaluate_signal(row["id"])
            if result:
                results.append(result)
        return results

    def get_stats(self, days: int = 90) -> Dict[str, Any]:
        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute("""
                    SELECT
                        COUNT(*) as total,
                        COUNT(*) FILTER (WHERE hit = TRUE) as hits,
                        COUNT(*) FILTER (WHERE hit = FALSE) as misses,
                        AVG(actual_return) FILTER (WHERE hit IS NOT NULL) as avg_return,
                        AVG(hit_pct) FILTER (WHERE hit = TRUE) as avg_hit_return,
                        AVG(actual_return) FILTER (WHERE hit = FALSE) as avg_miss_return,
                        COUNT(*) FILTER (WHERE direction = 'BUY') as buy_signals,
                        COUNT(*) FILTER (WHERE direction = 'SELL') as sell_signals,
                        COUNT(*) FILTER (WHERE direction = 'BUY' AND hit = TRUE) as buy_hits,
                        COUNT(*) FILTER (WHERE direction = 'SELL' AND hit = TRUE) as sell_hits
                    FROM signal_log
                    WHERE created_at >= NOW() - INTERVAL '%s days'
                """, (days,))
                stats = dict(cur.fetchone())
        finally:
            conn.close()

        if stats.get("total", 0) > 0:
            stats["win_rate"] = stats["hits"] / stats["total"]
            stats["buy_win_rate"] = (stats["buy_hits"] / stats["buy_signals"]
                                     if stats["buy_signals"] > 0 else 0)
            stats["sell_win_rate"] = (stats["sell_hits"] / stats["sell_signals"]
                                      if stats["sell_signals"] > 0 else 0)
        return stats
