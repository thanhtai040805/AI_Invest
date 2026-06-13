"""
Rebuild Adjusted Prices — Chạy lại toàn bộ adj_close/adj_factor cho HOSE.

Usage:
    python scripts/rebuild_adj_prices.py              # Full refresh
    python scripts/rebuild_adj_prices.py --incremental # Chỉ update mã mới
    python scripts/rebuild_adj_prices.py --symbol VNM  # Chỉ 1 mã
"""
import argparse
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="Rebuild adjusted prices for HOSE")
    parser.add_argument("--incremental", action="store_true", help="Incremental update only")
    parser.add_argument("--symbol", type=str, default=None, help="Single symbol to rebuild")
    args = parser.parse_args()

    from app.dataflows.vendors.vn.adj_close import (
        refresh_all,
        refresh_incremental,
        compute_adj_for_symbol,
        get_hose_symbols,
    )
    import psycopg2
    from app.services.pg_pool import DB_URL

    if args.incremental:
        logger.info("Running incremental adj_close refresh...")
        result = refresh_incremental()
        logger.info("Done: %s", result)
    elif args.symbol:
        logger.info("Rebuilding adj_close for single symbol: %s", args.symbol)
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        try:
            count = compute_adj_for_symbol(cur, args.symbol)
            conn.commit()
            logger.info("Updated %d rows", count)

            cur.execute("DROP TABLE IF EXISTS _adj_tmp")
            cur.execute(
                """CREATE TEMP TABLE _adj_tmp (sym TEXT, dt DATE, adj_close FLOAT, adj_factor FLOAT)"""
            )
            compute_adj_for_symbol(cur, args.symbol)
            conn.commit()
            cur.execute(
                """UPDATE ohlcv o
                   SET adj_close = t.adj_close, adj_factor = t.adj_factor
                   FROM _adj_tmp t
                   WHERE o.symbol = t.sym AND o.time::date = t.dt"""
            )
            updated = cur.rowcount
            conn.commit()
            cur.execute("DROP TABLE IF EXISTS _adj_tmp")
            conn.commit()
            logger.info("Symbol %s: %d rows updated", args.symbol, updated)
        finally:
            cur.close()
            conn.close()
    else:
        logger.info("Running full adj_close refresh for all HOSE symbols...")
        result = refresh_all()
        logger.info("Done: %s", result)


if __name__ == "__main__":
    main()
