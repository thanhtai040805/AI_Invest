"""
Adj Close Pipeline — corporate_actions + OHLCV → adj_close/adj_factor
Full refresh or incremental per-symbol update.
"""
import logging
from collections import defaultdict
from datetime import date
from typing import Optional

import psycopg2
import psycopg2.extras

from app.services.pg_pool import DB_URL

logger = logging.getLogger(__name__)


def get_hose_symbols(cur) -> list[str]:
    cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
    return [r[0] for r in cur.fetchall()]


def get_corporate_actions(cur, symbol: str) -> list[tuple]:
    cur.execute(
        """SELECT action_date, action_type, value, ratio::float
           FROM corporate_actions
           WHERE symbol = %s
           ORDER BY action_date ASC""",
        (symbol,),
    )
    return cur.fetchall()


def get_ohlcv_prices(cur, symbol: str) -> list[tuple[date, float]]:
    cur.execute(
        """SELECT time::date, close::float
           FROM ohlcv
           WHERE symbol = %s
           ORDER BY time DESC""",
        (symbol,),
    )
    return [(r[0], r[1]) for r in cur.fetchall()]


def compute_adj_for_symbol(cur, symbol: str) -> int:
    """Compute adj_close/adj_factor for one symbol. Returns row count updated."""
    ohlcv = get_ohlcv_prices(cur, symbol)
    if len(ohlcv) < 2:
        return 0

    dates_only = [r[0] for r in ohlcv]
    close_map = {r[0]: r[1] for r in ohlcv}

    acts_raw = get_corporate_actions(cur, symbol)
    acts = defaultdict(list)
    for d, t, v, r in acts_raw:
        acts[d].append((t, float(v or 0), float(r or 0)))

    factor = 1.0
    rows = []
    for i, d in enumerate(dates_only):
        af = factor
        if d in acts:
            for at, v, rv in acts[d]:
                if at == "DIVIDEND" and v > 0:
                    if i + 1 < len(dates_only):
                        pc = close_map.get(dates_only[i + 1], 0)
                        if pc > 0:
                            adj = (pc * 1000 - v) / (pc * 1000)
                            factor *= adj
                elif at == "SPLIT" and rv > 0:
                    factor *= 1.0 / rv
                elif at == "STOCK_DIVIDEND" and rv > 0:
                    factor *= 1.0 / (1.0 + rv)
        rows.append((symbol, d, round(close_map[d] * af, 2), af))

    psycopg2.extras.execute_values(
        cur,
        """INSERT INTO _adj_tmp (sym, dt, adj_close, adj_factor)
           VALUES %s""",
        rows,
        page_size=1000,
    )
    return len(rows)


def refresh_all() -> dict:
    """Full refresh for all HOSE stocks."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("SELECT COUNT(*) FROM ohlcv WHERE symbol IN (SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX'))")
        total_ohlcv = cur.fetchone()[0]

        cur.execute("""UPDATE ohlcv SET adj_close = NULL, adj_factor = NULL
                       WHERE symbol IN (SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX'))""")
        conn.commit()
        logger.info("Reset adj_close/adj_factor for all HOSE symbols")

        cur.execute("DROP TABLE IF EXISTS _adj_tmp")
        cur.execute("CREATE TEMP TABLE _adj_tmp (sym TEXT, dt DATE, adj_close FLOAT, adj_factor FLOAT)")
        conn.commit()

        symbols = get_hose_symbols(cur)
        logger.info("Refreshing adj_close for %d HOSE symbols", len(symbols))

        total_rows = 0
        for idx, sym in enumerate(symbols):
            count = compute_adj_for_symbol(cur, sym)
            total_rows += count
            if idx > 0 and idx % 100 == 0:
                conn.commit()
                logger.info("  Progress: %d/%d symbols, %d rows", idx, len(symbols), total_rows)

        conn.commit()

        cur.execute("""
            UPDATE ohlcv o
            SET adj_close = t.adj_close,
                adj_factor = t.adj_factor
            FROM _adj_tmp t
            WHERE o.symbol = t.sym AND o.time::date = t.dt
        """)
        updated = cur.rowcount
        conn.commit()

        cur.execute("DROP TABLE IF EXISTS _adj_tmp")
        conn.commit()

        # Verify
        cur.execute("""SELECT COUNT(*) FROM ohlcv
                       WHERE adj_close IS NULL
                         AND symbol IN (SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX'))""")
        nulls = cur.fetchone()[0]

        logger.info("Refresh done: %d rows updated, %d NULL remaining", updated, nulls)
        return {"updated": updated, "nulls_remaining": nulls, "total_ohlcv": total_ohlcv}

    finally:
        cur.close()
        conn.close()


def refresh_incremental(symbols: Optional[list[str]] = None) -> dict:
    """Incremental update: only for symbols with new OHLCV lacking adj_close.

    If symbols is None, auto-detect symbols with NULL adj_close on latest date.
    """
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        if symbols is None:
            cur.execute("""
                SELECT DISTINCT o.symbol
                FROM ohlcv o
                WHERE o.adj_close IS NULL
                  AND o.symbol IN (SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX'))
            """)
            symbols = [r[0] for r in cur.fetchall()]

        if not symbols:
            logger.info("No symbols need incremental adj_close update")
            return {"updated": 0, "symbols": 0}

        logger.info("Incremental adj_close for %d symbols", len(symbols))

        cur.execute("DROP TABLE IF EXISTS _adj_tmp")
        cur.execute("CREATE TEMP TABLE _adj_tmp (sym TEXT, dt DATE, adj_close FLOAT, adj_factor FLOAT)")
        conn.commit()

        total_rows = 0
        for idx, sym in enumerate(symbols):
            count = compute_adj_for_symbol(cur, sym)
            total_rows += count
            if idx > 0 and idx % 100 == 0:
                conn.commit()
                logger.info("  Progress: %d/%d symbols, %d rows", idx, len(symbols), total_rows)

        conn.commit()

        cur.execute("""
            UPDATE ohlcv o
            SET adj_close = t.adj_close,
                adj_factor = t.adj_factor
            FROM _adj_tmp t
            WHERE o.symbol = t.sym AND o.time::date = t.dt
        """)
        updated = cur.rowcount
        conn.commit()

        cur.execute("DROP TABLE IF EXISTS _adj_tmp")
        conn.commit()

        logger.info("Incremental done: %d rows updated for %d symbols", updated, len(symbols))
        return {"updated": updated, "symbols": len(symbols)}

    finally:
        cur.close()
        conn.close()
