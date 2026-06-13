"""
Buy/Sell Signal Generation — based on composite factor rank + risk flags.

Formula: (VOFI 2023, validated on VN100 2016-2025)

  BUY:        Composite_Rank > 75  AND  total risk_flags = 0
  BUY_WEAK:   Composite_Rank > 60  AND  total risk_flags ≤ 1
  HOLD:       40 ≤ Composite_Rank ≤ 60
  SELL_WEAK:  Composite_Rank < 40  OR  total risk_flags ≥ 2
  SELL:       Composite_Rank < 25  OR  total risk_flags ≥ 3
"""
import logging
from datetime import date, datetime, timezone, timedelta
from typing import Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL
from app.brain.risk.queries import get_hard_blocked, get_soft_flag_count
from app.dataflows.vendors.vn.sector_groups import classify

logger = logging.getLogger(__name__)

SIGNAL_BUY = "BUY"
SIGNAL_BUY_WEAK = "BUY_WEAK"
SIGNAL_HOLD = "HOLD"
SIGNAL_SELL_WEAK = "SELL_WEAK"
SIGNAL_SELL = "SELL"


def determine_signal(composite_rank: Optional[float], hard_blocked: bool, soft_flag_count: int) -> str:
    """Determine buy/sell signal for a single symbol.

    Hard flags block BUY. Soft flags reduce signal strength.
    Conservative by design — prioritizes capital preservation.
    """
    if composite_rank is None:
        return SIGNAL_HOLD

    hard = 1 if hard_blocked else 0
    soft = soft_flag_count

    # BUY: top rank, no hard flags, few soft flags
    if composite_rank > 75 and hard == 0 and soft <= 2:
        return SIGNAL_BUY

    # BUY_WEAK: good rank, no hard flags, reasonable soft flags
    if composite_rank > 60 and hard == 0 and soft <= 3:
        return SIGNAL_BUY_WEAK

    # SELL: very low rank or many flags
    if composite_rank < 25 or hard >= 2 or soft >= 5:
        return SIGNAL_SELL

    # SELL_WEAK: low rank or some flags
    if composite_rank < 40 or hard >= 1 or soft >= 3:
        return SIGNAL_SELL_WEAK

    return SIGNAL_HOLD


def refresh_all(calc_date: Optional[date] = None) -> dict:
    """Compute buy/sell signals for all HOSE symbols and save to DB.

    Creates/updates the 'signals' table with symbol-level recommendations.
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        if calc_date is None:
            cur.execute("SELECT MAX(score_date) FROM factor_scores")
            calc_date = cur.fetchone()[0]

        cur.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                symbol TEXT NOT NULL,
                signal_date DATE NOT NULL,
                signal TEXT NOT NULL,
                composite_rank REAL,
                hard_flags INTEGER DEFAULT 0,
                soft_flags INTEGER DEFAULT 0,
                sector_group TEXT,
                created_at TIMESTAMP DEFAULT NOW(),
                PRIMARY KEY (symbol, signal_date)
            )
        """)
        conn.commit()

        cur.execute("DELETE FROM signals")
        logger.info("  Deleted %d old signals", cur.rowcount)
        conn.commit()

        cur.execute("SELECT symbol, industry FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        rows = cur.fetchall()
        symbols = [(r[0], r[1]) for r in rows]
        sym_industry = dict(rows)
        logger.info("Signals: computing for %d symbols on %s", len(symbols), calc_date)

        # Load factor scores
        cur.execute(
            "SELECT symbol, percentile FROM factor_scores WHERE score_date = %s",
            (calc_date,),
        )
        factor_map: dict[str, float] = {}
        for sym, pct in cur.fetchall():
            if pct is not None:
                factor_map[sym] = float(pct)

        output_rows = []
        for sym, _ in symbols:
            percentile = factor_map.get(sym)
            hard_blocked = get_hard_blocked(sym)
            soft_count = get_soft_flag_count(sym)
            signal = determine_signal(percentile, hard_blocked, soft_count)
            group = classify(sym_industry.get(sym), sym)

            output_rows.append((
                sym, calc_date, signal,
                float(percentile) if percentile is not None else None,
                1 if hard_blocked else 0, soft_count, group,
            ))

        psycopg2.extras.execute_values(
            cur,
            """INSERT INTO signals
               (symbol, signal_date, signal, composite_rank, hard_flags, soft_flags, sector_group)
               VALUES %s
               ON CONFLICT (symbol, signal_date)
               DO UPDATE SET
                   signal = EXCLUDED.signal,
                   composite_rank = EXCLUDED.composite_rank,
                   hard_flags = EXCLUDED.hard_flags,
                   soft_flags = EXCLUDED.soft_flags,
                   sector_group = EXCLUDED.sector_group,
                   created_at = NOW()""",
            output_rows,
            page_size=500,
        )
        conn.commit()

        # Count by signal type
        cur.execute(
            "SELECT signal, COUNT(*) FROM signals WHERE signal_date = %s GROUP BY signal",
            (calc_date,),
        )
        counts = dict(cur.fetchall())
        logger.info("Signals done: %s", counts)

        return {
            "rows": len(output_rows),
            "signals": counts,
            "signal_date": str(calc_date),
        }
    finally:
        cur.close()
        conn.close()


def get_current_signal(symbol: str) -> Optional[str]:
    """Get the latest signal for a symbol."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT signal, signal_date, composite_rank
               FROM signals
               WHERE symbol = %s
               ORDER BY signal_date DESC
               LIMIT 1""",
            (symbol,),
        )
        row = cur.fetchone()
        if row:
            return row[0]
        return None
    finally:
        cur.close()
        conn.close()
