"""Signal Writer — bridges agent decisions into the paper-trading pipeline.

After the Portfolio Manager reaches a final decision, this node persists
the structured rating to the ``ai_signals`` table so that
``PaperTradingService`` can consume it alongside factor-based signals.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import psycopg2
import psycopg2.extras

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

_CREATE_AI_SIGNALS_SQL = """
CREATE TABLE IF NOT EXISTS ai_signals (
    symbol      TEXT NOT NULL,
    signal_date DATE NOT NULL,
    signal      TEXT NOT NULL,
    rating      TEXT NOT NULL,
    confidence  REAL DEFAULT 0.5,
    thesis      TEXT,
    price_target REAL,
    time_horizon TEXT,
    source      TEXT NOT NULL DEFAULT 'portfolio_manager',
    created_at  TIMESTAMP DEFAULT NOW(),
    PRIMARY KEY (symbol, signal_date)
);
"""


def _parse_decision(rating: str) -> str:
    rating_map = {
        "Buy": "BUY",
        "Overweight": "BUY_WEAK",
        "Hold": "HOLD",
        "Underweight": "SELL_WEAK",
        "Sell": "SELL",
    }
    return rating_map.get(rating, "HOLD")


def _ensure_table() -> None:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            cur.execute(_CREATE_AI_SIGNALS_SQL)
        conn.commit()
    finally:
        conn.close()


def create_signal_writer():
    _ensure_table()

    def signal_writer_node(state: dict) -> dict:
        symbol = state.get("company_of_interest", "")
        if not symbol:
            logger.warning("signal_writer: no symbol in state")
            return {}

        final_trade_decision = state.get("final_trade_decision", "")
        if not final_trade_decision:
            logger.info("signal_writer: no final_trade_decision, skipping")
            return {}

        rating = _extract_rating(final_trade_decision)
        signal = _parse_decision(rating)
        thesis = final_trade_decision[:2000]

        trade_date = state.get("trade_date")
        if trade_date:
            try:
                signal_date = date.fromisoformat(trade_date)
            except (ValueError, TypeError):
                signal_date = date.today()
        else:
            signal_date = date.today()

        conn = psycopg2.connect(DB_URL)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO ai_signals
                       (symbol, signal_date, signal, rating, confidence, thesis, source)
                       VALUES (%s, %s, %s, %s, %s, %s, %s)
                       ON CONFLICT (symbol, signal_date)
                       DO UPDATE SET
                           signal = EXCLUDED.signal,
                           rating = EXCLUDED.rating,
                           thesis = EXCLUDED.thesis,
                           source = EXCLUDED.source,
                           created_at = NOW()""",
                    (symbol, signal_date, signal, rating, 0.8, thesis, "portfolio_manager"),
                )
            conn.commit()
            logger.info("signal_writer: %s → %s (rating=%s)", symbol, signal, rating)
        except Exception as e:
            logger.error("signal_writer: DB error for %s: %s", symbol, e)
        finally:
            conn.close()

        return {}

    return signal_writer_node


def _extract_decision(final_trade_decision: str) -> str:
    return _extract_rating(final_trade_decision).upper()


def _extract_rating(final_trade_decision: str) -> str:
    for rating in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        if f"**Rating**: {rating}" in final_trade_decision:
            return rating
    for rating in ("Buy", "Overweight", "Hold", "Underweight", "Sell"):
        if rating in final_trade_decision:
            return rating
    return "Hold"


def get_today_ai_signals(trade_date: date) -> list[dict[str, Any]]:
    _ensure_table()
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """SELECT symbol, signal, rating, confidence, thesis, source
                   FROM ai_signals
                   WHERE signal_date = %s
                   ORDER BY confidence DESC""",
                (trade_date,),
            )
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()
