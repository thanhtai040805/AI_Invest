"""Refresh one persistent Redis quote snapshot per HOSE symbol from EOD data."""

import json
import logging
from datetime import datetime, timedelta, timezone

from app.infrastructure.external_api.dnse.redis_pub import get_redis

logger = logging.getLogger(__name__)
TZ_VN = timezone(timedelta(hours=7))


def _to_vnd(value) -> float | None:
    if value is None:
        return None
    price = float(value)
    return price * 1000 if 0 < price < 500 else price


def refresh_latest_quote_cache() -> int:
    """Seed/refresh quote keys after EOD ingestion; failures never fail the ETL."""
    conn = None
    try:
        from app.infrastructure.data_pipelines.ohlcv_backfill import get_db_conn

        as_of = datetime.now(TZ_VN).date()
        conn = get_db_conn()
        with conn.cursor() as cur:
            cur.execute("""
                WITH latest AS (
                    SELECT ticker, MAX(date) AS date
                    FROM market_data_daily
                    WHERE date <= %s
                    GROUP BY ticker
                )
                SELECT d.ticker, d.date,
                       COALESCE(NULLIF(d.close_unadj, 0), NULLIF(d.close_adj, 0)) AS price,
                       COALESCE(NULLIF(prev.close_unadj, 0), NULLIF(prev.close_adj, 0), s.ref_price) AS ref,
                       d.volume_total, s.name, s.ceiling, s.floor
                FROM latest l
                JOIN market_data_daily d ON d.ticker = l.ticker AND d.date = l.date
                JOIN stocks s ON s.symbol = d.ticker AND s.exchange = 'HOSE'
                LEFT JOIN LATERAL (
                    SELECT close_unadj, close_adj
                    FROM market_data_daily
                    WHERE ticker = d.ticker AND date < d.date
                    ORDER BY date DESC
                    LIMIT 1
                ) prev ON TRUE
                WHERE COALESCE(NULLIF(d.close_unadj, 0), NULLIF(d.close_adj, 0)) > 0
            """, (as_of,))
            rows = cur.fetchall()

        if not rows:
            return 0

        redis = get_redis()
        pipe = redis.pipeline(transaction=False)
        for symbol, trade_date, raw_price, raw_ref, volume, name, ceiling, floor in rows:
            price = _to_vnd(raw_price)
            ref = _to_vnd(raw_ref)
            change = price - ref if price and ref else None
            change_pct = change / ref * 100 if change is not None and ref else None
            date = trade_date.isoformat()
            quote = {
                "symbol": symbol,
                "name": name or symbol,
                "price": price,
                "close": price,
                "ref": ref,
                "prevClose": ref,
                "change": change,
                "changePercent": change_pct,
                "change_pct": change_pct,
                "volume": int(volume or 0),
                "ceiling": _to_vnd(ceiling),
                "floor": _to_vnd(floor),
                "timestamp": date,
                "asOf": date,
                "lastUpdate": f"{date}T15:00:00+07:00",
                "source": "postgres-eod",
                "stale": True,
                "isSnapshot": True,
            }
            pipe.set(f"stock:{symbol}:quote", json.dumps(quote, default=str))
        pipe.execute()
        return len(rows)
    except Exception as exc:
        logger.warning("Could not refresh persistent Redis EOD quote snapshots: %s", exc)
        return 0
    finally:
        if conn:
            try:
                conn.close()
            except Exception as exc:
                logger.warning("Could not close EOD quote-cache database connection: %s", exc)
