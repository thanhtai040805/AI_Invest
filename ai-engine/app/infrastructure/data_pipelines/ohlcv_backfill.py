"""Daily backfill — stocks + OHLCV, fetch from DNSE REST API, save to PostgreSQL."""

import os
import time
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from app.config.settings import get_settings
from app.infrastructure.external_api.dnse.api.client import DNSEClient
from app.infrastructure.vendors.vn.sector_groups import SYMBOL_OVERRIDES, classify
from app.infrastructure.data_pipelines.ohlc_validation import is_valid_ohlc
from psycopg2.extras import Json, execute_values

TZ_VN = timezone(timedelta(hours=7))
CW_PATTERN = re.compile(r'^C[A-Z]{2,4}\d{4,6}$')
ETF_PREFIXES = ('FUE', 'FU_', 'E1', 'KIS', 'SSI')


def is_real_stock(sym: str) -> bool:
    if not sym:
        return False
    if CW_PATTERN.match(sym):
        return False
    if sym != 'SSI' and sym.startswith(ETF_PREFIXES):
        return False
    return True


def get_db_conn():
    import psycopg2
    db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
    return psycopg2.connect(db_url)


def get_all_stocks(client, market_ids: list[str]) -> list[dict]:
    items = []
    for mid in market_ids:
        page = 1
        while True:
            status, body = client.get_instruments(
                symbol='', market_id=mid, security_group_id='ST',
                index_name='', limit=200, page=page,
            )
            data = json.loads(body) if isinstance(body, str) else body
            batch = data if isinstance(data, list) else data.get('data', [])
            if not batch:
                break
            items.extend(batch)
            if len(batch) < 200:
                break
            page += 1
    return items


def _normalize_row_date(value) -> date:
    return value.date() if hasattr(value, "date") else value


def _resolve_end_dt(target_date: Optional[date] = None) -> datetime:
    if target_date:
        return datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=TZ_VN)
    return datetime.now(TZ_VN)


def _fetch_ohlc_range(
    client,
    symbol: str,
    t_start: datetime,
    t_end: datetime,
    bar_type: str = "STOCK",
) -> Optional[dict]:
    """Fetch daily OHLC bars from DNSE REST API over [t_start, t_end] with retry."""
    for attempt in range(3):
        try:
            status, body = client.get_ohlc(
                bar_type=bar_type,
                query={
                    "symbol": symbol,
                    "resolution": "1D",
                    "from": int(t_start.timestamp()),
                    "to": int(t_end.timestamp()),
                },
                dry_run=False,
            )
            if status == 429:
                time.sleep(30)
                continue
            if status == 200 and body:
                if isinstance(body, str):
                    body = json.loads(body)
                if isinstance(body, dict) and body.get('t') and len(body['t']) > 0:
                    return body
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"  [Error] {symbol}: {e}")
            return None
    return None


def _parse_ohlcv_candles(payload: Optional[dict], symbol: str) -> list[tuple]:
    """Convert DNSE OHLC JSON arrays into (candle_date, symbol, o, h, l, c, v) tuples."""
    if not payload or not payload.get("t"):
        return []
    timestamps = payload["t"]
    opens = payload.get("o") or []
    highs = payload.get("h") or []
    lows = payload.get("l") or []
    closes = payload.get("c") or []
    volumes = payload.get("v") or []
    rows = []
    for i, ts in enumerate(timestamps):
        if i >= min(len(opens), len(highs), len(lows), len(closes)):
            break
        candle_date = datetime.fromtimestamp(ts, tz=TZ_VN).date()
        vol = int(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0
        rows.append((
            candle_date,
            symbol,
            opens[i],
            highs[i],
            lows[i],
            closes[i],
            vol,
        ))
    return rows


def fetch_today_ohlcv(client, symbol: str, target_date: Optional[date] = None, days_back: int = 14) -> Optional[dict]:
    """Fetch daily OHLCV from DNSE REST API up to target_date (or today)."""
    t_end = _resolve_end_dt(target_date)
    t_start = t_end - timedelta(days=days_back)
    return _fetch_ohlc_range(client, symbol, t_start, t_end, bar_type="STOCK")


def fetch_full_history_ohlcv(client, symbol: str, target_date: Optional[date] = None) -> Optional[dict]:
    """Fetch complete daily backward-adjusted OHLCV history from DNSE REST API."""
    t_end = _resolve_end_dt(target_date)
    t_start = datetime(2000, 1, 1, 0, 0, 0, tzinfo=TZ_VN)
    return _fetch_ohlc_range(client, symbol, t_start, t_end, bar_type="STOCK")


def has_adjusted_price_drift(
    cur,
    symbol: str,
    rows: list[tuple],
    expected_date: Optional[date] = None,
    tolerance: float = 0.005,
) -> bool:
    """Return True if historical DNSE closes shifted vs stored market_data_daily or ohlcv."""
    valid = [r for r in rows if len(r) >= 6 and is_valid_ohlc(r[2], r[3], r[4], r[5])]
    if not valid:
        return False
    cutoff_date = expected_date or max(_normalize_row_date(r[0]) for r in valid)
    hist = [
        (_normalize_row_date(r[0]), float(r[5]))
        for r in valid
        if _normalize_row_date(r[0]) < cutoff_date
    ]
    if not hist:
        return False

    min_dt = min(d for d, _ in hist)
    max_dt = max(d for d, _ in hist)

    cur.execute(
        """SELECT date, close_adj
           FROM market_data_daily
           WHERE ticker = %s AND date >= %s AND date <= %s""",
        (symbol, min_dt, max_dt),
    )
    mdd_map = {
        _normalize_row_date(r[0]): float(r[1])
        for r in (cur.fetchall() or [])
        if r[1] is not None
    }

    cur.execute(
        """SELECT time, close
           FROM ohlcv
           WHERE symbol = %s AND time >= %s AND time <= %s""",
        (symbol, min_dt, max_dt),
    )
    ohlcv_map = {
        _normalize_row_date(r[0]): float(r[1])
        for r in (cur.fetchall() or [])
        if r[1] is not None
    }

    for dt, close_val in hist:
        if dt in mdd_map and abs(close_val - mdd_map[dt]) > tolerance:
            return True
        if dt in ohlcv_map and abs(close_val - ohlcv_map[dt]) > tolerance:
            return True
    return False


def upsert_today(
    cur,
    rows: list[tuple],
    expected_date: Optional[date] = None,
    has_drift: bool = False,
):
    if not rows:
        return 0
    valid_rows = [row for row in rows if len(row) >= 7 and is_valid_ohlc(row[2], row[3], row[4], row[5])]
    invalid_count = len(rows) - len(valid_rows)
    if invalid_count:
        print(f"[DailyBackfill] Skipped {invalid_count} OHLC-invalid bars")
    if not valid_rows:
        return 0

    ohlcv_rows = [
        (r[0], r[1], r[2], r[3], r[4], r[5], r[6], float(r[5]))
        for r in valid_rows
    ]
    cur.executemany("""
        INSERT INTO ohlcv (time, symbol, open, high, low, close, volume, adj_close)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (time, symbol) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume,
            adj_close = EXCLUDED.adj_close
    """, ohlcv_rows)

    # Đồng bộ sang bảng market_data_daily cho toàn bộ 12 Agents và EOD Pipeline
    mkt_rows = []
    symbol_ranges: dict[str, tuple[date, date]] = {}
    for r in valid_rows:
        candle_date = _normalize_row_date(r[0])
        sym = str(r[1])
        close_val = float(r[5])
        seed_unadj = (
            None
            if (has_drift and expected_date is not None and candle_date < expected_date)
            else close_val
        )
        mkt_rows.append((
            sym,
            candle_date,
            float(r[2]),  # open_adj
            float(r[3]),  # high_adj
            float(r[4]),  # low_adj
            close_val,    # close_adj
            seed_unadj,   # Seed raw close on first insert; preserve existing unadj when adjusted history changes.
            close_val,    # vwap
            None,         # Daily candles do not identify auction/continuous volume split.
            None,         # Unknown ATO/ATC cannot be recorded as zero.
            None,
            int(r[6]),    # Observed total volume from the daily candle.
            1.0,          # adj_factor initial seed
            "dnse_daily", # data_source
        ))
        prev_range = symbol_ranges.get(sym)
        if prev_range is None:
            symbol_ranges[sym] = (candle_date, candle_date)
        else:
            symbol_ranges[sym] = (min(prev_range[0], candle_date), max(prev_range[1], candle_date))

    cur.executemany("""
        INSERT INTO market_data_daily (
            ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj,
            vwap, volume_continuous, volume_atc, volume_ato, volume_total, adj_factor, data_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, date) DO UPDATE SET
            open_adj = EXCLUDED.open_adj,
            high_adj = EXCLUDED.high_adj,
            low_adj = EXCLUDED.low_adj,
            close_adj = EXCLUDED.close_adj,
            close_unadj = CASE
                WHEN market_data_daily.close_unadj IS NOT NULL AND market_data_daily.close_unadj > 0
                THEN market_data_daily.close_unadj
                ELSE EXCLUDED.close_unadj
            END,
            adj_factor = CASE
                WHEN COALESCE(NULLIF(market_data_daily.close_unadj, 0), EXCLUDED.close_unadj) > 0
                THEN ROUND((EXCLUDED.close_adj / COALESCE(NULLIF(market_data_daily.close_unadj, 0), EXCLUDED.close_unadj))::numeric, 6)::double precision
                ELSE 1.0
            END,
            vwap = EXCLUDED.vwap,
            volume_continuous = COALESCE(EXCLUDED.volume_continuous, market_data_daily.volume_continuous),
            volume_atc = COALESCE(EXCLUDED.volume_atc, market_data_daily.volume_atc),
            volume_ato = COALESCE(EXCLUDED.volume_ato, market_data_daily.volume_ato),
            volume_total = EXCLUDED.volume_total,
            data_source = EXCLUDED.data_source
    """, mkt_rows)

    for sym, (min_dt, max_dt) in symbol_ranges.items():
        cur.execute("""
            UPDATE ohlcv AS o
            SET adj_factor = COALESCE(m.adj_factor, 1.0)
            FROM market_data_daily AS m
            WHERE m.ticker = o.symbol
              AND m.date = o.time
              AND o.symbol = %s
              AND o.time >= %s::date
              AND o.time <= %s::date
        """, (sym, min_dt, max_dt))

    return len(valid_rows)


def sync_adjusted_history_for_symbol(
    client,
    cur,
    symbol: str,
    target_date: Optional[date] = None,
) -> int:
    """Update only adjusted price columns across full history while keeping close_unadj untouched."""
    payload = fetch_full_history_ohlcv(client, symbol, target_date=target_date)
    candles = _parse_ohlcv_candles(payload, symbol)
    adj_rows = [
        (symbol, _normalize_row_date(r[0]), float(r[2]), float(r[3]), float(r[4]), float(r[5]))
        for r in candles
        if is_valid_ohlc(r[2], r[3], r[4], r[5])
    ]
    if not adj_rows:
        return 0

    execute_values(
        cur,
        """
        UPDATE market_data_daily AS m
        SET open_adj = v.open_adj,
            high_adj = v.high_adj,
            low_adj = v.low_adj,
            close_adj = v.close_adj,
            vwap = CASE
                WHEN m.vwap IS NOT NULL AND m.close_adj IS NOT NULL AND ABS(m.vwap - m.close_adj) < 1e-4
                THEN v.close_adj
                WHEN m.vwap IS NOT NULL AND m.close_adj IS NOT NULL AND m.close_adj > 0
                     AND m.close_unadj IS NOT NULL AND ABS(m.vwap - m.close_unadj) > 1e-3
                THEN ROUND((m.vwap * (v.close_adj / m.close_adj))::numeric, 6)::double precision
                ELSE m.vwap
            END,
            adj_factor = CASE
                WHEN m.close_unadj IS NOT NULL AND m.close_unadj > 0
                THEN ROUND((v.close_adj / m.close_unadj)::numeric, 6)::double precision
                ELSE 1.0
            END
        FROM (VALUES %s) AS v(ticker, dt, open_adj, high_adj, low_adj, close_adj)
        WHERE m.ticker = v.ticker
          AND m.date = v.dt::date
          AND (
              ABS(COALESCE(m.open_adj, 0) - v.open_adj) > 1e-6
              OR ABS(COALESCE(m.high_adj, 0) - v.high_adj) > 1e-6
              OR ABS(COALESCE(m.low_adj, 0) - v.low_adj) > 1e-6
              OR ABS(COALESCE(m.close_adj, 0) - v.close_adj) > 1e-6
              OR m.adj_factor IS NULL
              OR (
                  m.close_unadj > 0
                  AND ABS(COALESCE(m.adj_factor, 1.0) - ROUND((v.close_adj / m.close_unadj)::numeric, 6)::double precision) > 1e-6
              )
          )
        """,
        adj_rows,
        page_size=1000,
    )
    updated_mdd = cur.rowcount

    execute_values(
        cur,
        """
        UPDATE ohlcv AS o
        SET open = v.open_adj,
            high = v.high_adj,
            low = v.low_adj,
            close = v.close_adj,
            adj_close = v.close_adj,
            adj_factor = COALESCE(
                (
                    SELECT CASE
                        WHEN m.close_unadj IS NOT NULL AND m.close_unadj > 0
                        THEN ROUND((v.close_adj / m.close_unadj)::numeric, 6)::double precision
                        ELSE 1.0
                    END
                    FROM market_data_daily m
                    WHERE m.ticker = o.symbol AND m.date = o.time
                ),
                1.0
            )
        FROM (VALUES %s) AS v(sym, dt, open_adj, high_adj, low_adj, close_adj)
        WHERE o.symbol = v.sym
          AND o.time = v.dt::date
          AND (
              ABS(o.open::double precision - v.open_adj) > 1e-6
              OR ABS(o.high::double precision - v.high_adj) > 1e-6
              OR ABS(o.low::double precision - v.low_adj) > 1e-6
              OR ABS(o.close::double precision - v.close_adj) > 1e-6
              OR o.adj_close IS NULL
              OR ABS(o.adj_close - v.close_adj) > 1e-6
              OR o.adj_factor IS NULL
              OR EXISTS (
                  SELECT 1 FROM market_data_daily m
                  WHERE m.ticker = o.symbol AND m.date = o.time AND m.close_unadj > 0
                    AND ABS(COALESCE(o.adj_factor, 1.0) - ROUND((v.close_adj / m.close_unadj)::numeric, 6)::double precision) > 1e-6
              )
          )
        """,
        adj_rows,
        page_size=1000,
    )
    return updated_mdd


def sync_stocks(
    exchanges: Optional[list[str]] = None,
) -> int:
    """Fetch stock master data from DNSE REST API and upsert into PostgreSQL `stocks` table.

    Returns number of stocks upserted.
    """
    settings = get_settings()
    client = DNSEClient(
        api_key=settings.dnse_api_key,
        api_secret=settings.dnse_api_secret,
        base_url=settings.dnse_base_url,
    )

    exchanges = exchanges or ["STO"]
    print(f"[SyncStocks] Fetching stocks from: {exchanges}...")
    all_instruments = get_all_stocks(client, exchanges)

    now_str = datetime.now(timezone.utc).isoformat()
    conn = get_db_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS stocks (
            symbol    TEXT PRIMARY KEY,
            name      TEXT NOT NULL,
            exchange  TEXT NOT NULL,
            industry  TEXT,
            market_cap BIGINT,
            ceiling   DECIMAL(12,2),
            floor     DECIMAL(12,2),
            ref_price DECIMAL(12,2),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)

    count = 0
    for item in all_instruments:
        sym = item.get("symbol") or item.get("Symbol") or ""
        if not sym or not is_real_stock(sym):
            continue

        name = item.get("companyName") or item.get("CompanyName") or sym
        exchange = item.get("market") or item.get("Market") or "HOSE"
        industry = (item.get("industryName") or item.get("IndustryName") or "").strip() or None
        sector = classify(industry, sym) if industry or sym in SYMBOL_OVERRIDES else None

        ceiling = None
        floor = None
        ref_price = None
        market_cap = None

        try:
            c = item.get("ceilingPrice") or item.get("CeilingPrice") or item.get("ceiling")
            if c is not None:
                ceiling = float(c)
        except (ValueError, TypeError):
            pass
        try:
            f = item.get("floorPrice") or item.get("FloorPrice") or item.get("floor")
            if f is not None:
                floor = float(f)
        except (ValueError, TypeError):
            pass
        try:
            r = item.get("referencePrice") or item.get("ReferencePrice") or item.get("refPrice")
            if r is not None:
                ref_price = float(r)
        except (ValueError, TypeError):
            pass
        try:
            mc = item.get("marketCap") or item.get("MarketCap") or item.get("market_cap")
            if mc is not None:
                market_cap = int(mc) if not isinstance(mc, int) else mc
            market_cap = market_cap if market_cap is not None and market_cap > 0 else None
        except (ValueError, TypeError, OverflowError):
            pass

        cur.execute("""
            INSERT INTO stocks (symbol, name, exchange, industry, sector, market_cap, ceiling, floor, ref_price, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                name = EXCLUDED.name,
                exchange = EXCLUDED.exchange,
                industry = COALESCE(EXCLUDED.industry, stocks.industry),
                sector = COALESCE(EXCLUDED.sector, stocks.sector),
                market_cap = COALESCE(EXCLUDED.market_cap, stocks.market_cap),
                ceiling = EXCLUDED.ceiling,
                floor = EXCLUDED.floor,
                ref_price = EXCLUDED.ref_price,
                updated_at = EXCLUDED.updated_at
        """, (sym, name, exchange, industry, sector, market_cap, ceiling, floor, ref_price, now_str))
        count += 1

    cur.execute("""SELECT s.symbol, s.name FROM stocks s
                    LEFT JOIN instrument_master im ON im.symbol = s.symbol
                    WHERE im.symbol IS NULL OR im.isin IS NULL OR im.first_listed IS NULL
                       OR im.metadata IS NULL OR im.metadata = '{}'::jsonb
                    ORDER BY s.symbol""")
    missing_metadata = cur.fetchall()
    secdef_count = 0
    for sym, name in missing_metadata:
        try:
            status, body = client.get_security_definition(sym)
            payload = json.loads(body) if isinstance(body, str) else body
            payload = payload.get("data", payload) if isinstance(payload, dict) else {}
            if status != 200 or not isinstance(payload, dict):
                print(f"[SyncStocks] DNSE secdef unavailable for {sym}: HTTP {status}")
                continue
            isin = payload.get("isin")
            listed = payload.get("listingDate")
            delisted = payload.get("finalTradeDate")
            metadata = {key: payload[key] for key in ("marketId", "indexName", "symbolType", "securityGroupId") if key in payload}
            cur.execute("""INSERT INTO instrument_master (symbol, isin, name, first_listed, delist_date, metadata)
                           VALUES (%s, %s, %s, %s, %s, %s)
                           ON CONFLICT (symbol) DO UPDATE SET
                             isin = COALESCE(instrument_master.isin, EXCLUDED.isin),
                             name = COALESCE(instrument_master.name, EXCLUDED.name),
                             first_listed = COALESCE(instrument_master.first_listed, EXCLUDED.first_listed),
                             delist_date = COALESCE(instrument_master.delist_date, EXCLUDED.delist_date),
                             metadata = CASE WHEN instrument_master.metadata IS NULL OR instrument_master.metadata = '{}'::jsonb
                                             THEN EXCLUDED.metadata ELSE instrument_master.metadata END,
                             updated_at = NOW()""",
                        (sym, isin, name, date.fromisoformat(listed[:10]) if listed else None,
                         date.fromisoformat(delisted[:10]) if delisted else None, Json(metadata)))
            secdef_count += cur.rowcount
        except Exception as exc:
            print(f"[SyncStocks] DNSE secdef failed for {sym}: {exc}")

    conn.commit()
    cur.close()
    conn.close()
    print(f"[SyncStocks] Upserted {count} stocks; instrument master secdef rows: {secdef_count}")
    return count


def run_daily_backfill(
    exchanges: Optional[list[str]] = None,
    max_symbols: int = 0,
    progress_callback=None,
    target_date: Optional[date] = None,
    days_back: int = 14,
) -> dict:
    """Fetch today's OHLCV for all symbols from DNSE REST API and save to PostgreSQL.

    Runs once at end of trading day. Uses ON CONFLICT DO UPDATE for idempotency.
    """
    settings = get_settings()
    client = DNSEClient(
        api_key=settings.dnse_api_key,
        api_secret=settings.dnse_api_secret,
        base_url=settings.dnse_base_url,
    )

    exchanges = exchanges or ["STO"]
    print(f"[DailyBackfill] Fetching stocks from: {exchanges}...")
    all_stocks = get_all_stocks(client, exchanges)
    real = {s['symbol']: s.get('listedDate', '') for s in all_stocks if is_real_stock(s['symbol'])}
    symbol_map = dict(sorted(real.items(), key=lambda x: x[1] or '9999'))
    print(f"[DailyBackfill] Found {len(symbol_map)} real stocks")

    if max_symbols > 0:
        symbol_map = dict(list(symbol_map.items())[:max_symbols])

    today_str = datetime.now(TZ_VN).strftime("%Y-%m-%d")
    expected_date = target_date or datetime.now(TZ_VN).date()
    count = 0
    total_rows = 0
    target_rows = 0
    adjusted_symbols: list[str] = []
    total_adjusted_rows = 0
    start_time = time.time()

    for sym, _ in symbol_map.items():
        count += 1
        elapsed = time.time() - start_time
        rate = count / elapsed if elapsed > 0 else 0
        remaining = len(symbol_map) - count
        eta = remaining / rate if rate > 0 else 0
        print(f"  [{count}/{len(symbol_map)}] {sym} [{rate:.1f}/s, ETA {eta:.0f}s]")

        result = fetch_today_ohlcv(client, sym, target_date=target_date, days_back=days_back)
        rows = _parse_ohlcv_candles(result, sym)
        if not rows:
            continue

        conn = get_db_conn()
        cur = conn.cursor()
        try:
            drift = has_adjusted_price_drift(cur, sym, rows, expected_date=expected_date)
            saved = upsert_today(cur, rows, expected_date=expected_date, has_drift=drift)
            adj_rows = 0
            if drift:
                adj_rows = sync_adjusted_history_for_symbol(client, cur, sym, target_date=expected_date)
                adjusted_symbols.append(sym)
                total_adjusted_rows += adj_rows
            conn.commit()
        finally:
            cur.close()
            conn.close()

        total_rows += saved
        target_rows += sum(
            row[0] == expected_date and is_valid_ohlc(row[2], row[3], row[4], row[5])
            for row in rows
        )
        if saved or adj_rows:
            msg = f"    [OK] {saved} rows"
            if adj_rows:
                msg += f" (+{adj_rows} full-history adj_ rows)"
            print(msg)

        if progress_callback:
            progress_callback(sym, count, len(symbol_map))

    duration = time.time() - start_time

    # Sync VNINDEX index candle for Agent-01 Market Regime
    try:
        idx_end = _resolve_end_dt(target_date)
        idx_start = idx_end - timedelta(days=days_back)
        data_idx = _fetch_ohlc_range(client, "VNINDEX", idx_start, idx_end, bar_type="INDEX")
        v_rows = _parse_ohlcv_candles(data_idx, "VNINDEX")
        if v_rows:
            conn = get_db_conn()
            cur = conn.cursor()
            try:
                upsert_today(cur, v_rows, expected_date=expected_date)
                conn.commit()
            finally:
                cur.close()
                conn.close()
            print("  [DailyBackfill] [OK] VNINDEX synced into ohlcv and market_data_daily")
    except Exception as e_idx:
        print(f"  [DailyBackfill] Warning: Failed to sync VNINDEX: {e_idx}")

    if target_rows > 0:
        from app.infrastructure.data_pipelines.latest_quote_cache import refresh_latest_quote_cache
        cached_symbols = refresh_latest_quote_cache()
        print(f"[DailyBackfill] Refreshed {cached_symbols} persistent Redis EOD quote snapshots")

    print(
        f"[DailyBackfill] DONE: {count} symbols, {total_rows} rows, "
        f"{len(adjusted_symbols)} adj-resynced ({total_adjusted_rows} rows) in {duration:.0f}s"
    )
    return {
        "total_symbols": count,
        "total_rows": total_rows,
        "target_rows": target_rows,
        "adjusted_symbols": adjusted_symbols,
        "adjusted_rows": total_adjusted_rows,
        "duration_seconds": duration,
    }


def reconcile_adjusted_prices(
    symbols: Optional[list[str]] = None,
    target_date: Optional[date] = None,
    lookback_days: int = 30,
) -> dict:
    """Reconcile full historical adj_ columns for stocks with recent corporate actions or partial-window drift."""
    settings = get_settings()
    client = DNSEClient(
        api_key=settings.dnse_api_key,
        api_secret=settings.dnse_api_secret,
        base_url=settings.dnse_base_url,
    )
    ref_date = target_date or datetime.now(TZ_VN).date()
    since_date = ref_date - timedelta(days=max(1, lookback_days))

    conn = get_db_conn()
    cur = conn.cursor()
    updated_symbols: list[str] = []
    failed_symbols: list[str] = []
    total_updated = 0
    try:
        if symbols is None:
            cur.execute(
                """
                SELECT DISTINCT ca.symbol
                FROM corporate_actions ca
                JOIN stocks s ON s.symbol = ca.symbol
                WHERE s.exchange IN ('HOSE', 'HSX')
                  AND ca.action_date BETWEEN %s AND %s
                UNION
                SELECT DISTINCT ticker
                FROM (
                    SELECT ticker,
                           close_unadj,
                           close_adj / NULLIF(close_unadj, 0) AS r,
                           LAG(close_adj / NULLIF(close_unadj, 0)) OVER (PARTITION BY ticker ORDER BY date) AS prev_r
                    FROM market_data_daily
                    WHERE date >= '2026-09-01' AND close_unadj > 0 AND close_adj > 0
                ) ratios
                WHERE prev_r IS NOT NULL
                  AND (prev_r - r) > 0.005
                  AND (prev_r - r) * close_unadj > 0.05
                ORDER BY 1
                """,
                (since_date, ref_date),
            )
            candidates = [r[0] for r in (cur.fetchall() or []) if r and r[0]]
        else:
            candidates = [str(s).upper().strip() for s in symbols if s]

        for sym in candidates:
            if not is_real_stock(sym):
                continue
            try:
                updated = sync_adjusted_history_for_symbol(client, cur, sym, target_date=ref_date)
                conn.commit()
                if updated > 0:
                    updated_symbols.append(sym)
                    total_updated += updated
            except Exception as exc:
                conn.rollback()
                failed_symbols.append(sym)
                print(f"  [AdjReconcile] Failed {sym}: {exc}")

        return {
            "checked_symbols": len(candidates),
            "updated_symbols": updated_symbols,
            "updated_rows": total_updated,
            "failed_symbols": failed_symbols,
        }
    finally:
        cur.close()
        conn.close()

