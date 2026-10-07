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
from psycopg2.extras import Json

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


def fetch_today_ohlcv(client, symbol: str, target_date: Optional[date] = None, days_back: int = 14) -> Optional[dict]:
    """Fetch daily OHLCV from DNSE REST API up to target_date (or today)."""
    now_vn = datetime.now(TZ_VN)
    if target_date:
        t_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=TZ_VN)
    else:
        t_end = now_vn
    t_start = t_end - timedelta(days=days_back)

    for attempt in range(3):
        try:
            status, body = client.get_ohlc(
                bar_type="STOCK",
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


def upsert_today(cur, rows: list[tuple]):
    if not rows:
        return 0
    valid_rows = [row for row in rows if len(row) >= 7 and is_valid_ohlc(row[2], row[3], row[4], row[5])]
    invalid_count = len(rows) - len(valid_rows)
    if invalid_count:
        print(f"[DailyBackfill] Skipped {invalid_count} OHLC-invalid bars")
    if not valid_rows:
        return 0
    cur.executemany("""
        INSERT INTO ohlcv (time, symbol, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (time, symbol) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
    """, valid_rows)

    # Đồng bộ sang bảng market_data_daily cho toàn bộ 12 Agents và EOD Pipeline
    mkt_rows = [
        (
            r[1],  # ticker
            r[0].date() if hasattr(r[0], "date") else r[0],  # date
            float(r[2]),  # open_adj
            float(r[3]),  # high_adj
            float(r[4]),  # low_adj
            float(r[5]),  # close_adj
            float(r[5]),  # Seed the raw close; keep it when adjusted history is rebuilt.
            float(r[5]),  # vwap
            None,         # Daily candles do not identify auction/continuous volume split.
            None,         # Unknown ATO/ATC cannot be recorded as zero.
            None,
            int(r[6]),    # Observed total volume from the daily candle.
            "dnse_daily", # data_source
        )
        for r in valid_rows
    ]
    cur.executemany("""
        INSERT INTO market_data_daily (
            ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj,
            vwap, volume_continuous, volume_atc, volume_ato, volume_total, data_source
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (ticker, date) DO UPDATE SET
            open_adj = EXCLUDED.open_adj,
            high_adj = EXCLUDED.high_adj,
            low_adj = EXCLUDED.low_adj,
            close_adj = EXCLUDED.close_adj,
            close_unadj = COALESCE(market_data_daily.close_unadj, EXCLUDED.close_unadj),
            vwap = EXCLUDED.vwap,
            volume_continuous = EXCLUDED.volume_continuous,
            volume_atc = EXCLUDED.volume_atc,
            volume_ato = EXCLUDED.volume_ato,
            volume_total = EXCLUDED.volume_total,
            data_source = EXCLUDED.data_source
    """, mkt_rows)
    return len(valid_rows)


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
    start_time = time.time()

    for sym, _ in symbol_map.items():
        count += 1
        elapsed = time.time() - start_time
        rate = count / elapsed if elapsed > 0 else 0
        remaining = len(symbol_map) - count
        eta = remaining / rate if rate > 0 else 0
        print(f"  [{count}/{len(symbol_map)}] {sym} [{rate:.1f}/s, ETA {eta:.0f}s]")

        result = fetch_today_ohlcv(client, sym, target_date=target_date, days_back=days_back)
        if not result or not result.get('t'):
            continue

        rows = []
        for i in range(len(result['t'])):
            candle_date = datetime.fromtimestamp(result['t'][i], tz=TZ_VN).date()
            rows.append((
                candle_date, sym,
                result.get('o', [0])[i],
                result.get('h', [0])[i],
                result.get('l', [0])[i],
                result.get('c', [0])[i],
                int(result.get('v', [0])[i]),
            ))

        if rows:
            conn = get_db_conn()
            cur = conn.cursor()
            saved = upsert_today(cur, rows)
            conn.commit()
            cur.close()
            conn.close()
            total_rows += saved
            target_rows += sum(
                row[0] == expected_date and is_valid_ohlc(row[2], row[3], row[4], row[5])
                for row in rows
            )
            if saved:
                print(f"    [OK] {saved} rows")

        if progress_callback:
            progress_callback(sym, count, len(symbol_map))

    duration = time.time() - start_time

    # Sync VNINDEX index candle for Agent-01 Market Regime
    try:
        now_vn = datetime.now(TZ_VN)
        if target_date:
            idx_end = datetime(target_date.year, target_date.month, target_date.day, 23, 59, 59, tzinfo=TZ_VN)
        else:
            idx_end = now_vn
        idx_start = idx_end - timedelta(days=days_back)
        status, body = client.get_ohlc(
            bar_type="INDEX",
            query={
                "symbol": "VNINDEX",
                "resolution": "1D",
                "from": int(idx_start.timestamp()),
                "to": int(idx_end.timestamp()),
            },
            dry_run=False,
        )
        if status == 200 and body:
            data_idx = json.loads(body) if isinstance(body, str) else body
            if isinstance(data_idx, dict) and data_idx.get("t"):
                v_rows = [
                    (
                        datetime.fromtimestamp(data_idx["t"][i], tz=TZ_VN).date(),
                        "VNINDEX",
                        data_idx.get("o", [0])[i],
                        data_idx.get("h", [0])[i],
                        data_idx.get("l", [0])[i],
                        data_idx.get("c", [0])[i],
                        int(data_idx.get("v", [0])[i]),
                    )
                    for i in range(len(data_idx["t"]))
                ]
                if v_rows:
                    conn = get_db_conn()
                    cur = conn.cursor()
                    upsert_today(cur, v_rows)
                    conn.commit()
                    cur.close()
                    conn.close()
                    print("  [DailyBackfill] [OK] VNINDEX synced into ohlcv and market_data_daily")
    except Exception as e_idx:
        print(f"  [DailyBackfill] Warning: Failed to sync VNINDEX: {e_idx}")

    if target_rows > 0:
        from app.infrastructure.data_pipelines.latest_quote_cache import refresh_latest_quote_cache
        cached_symbols = refresh_latest_quote_cache()
        print(f"[DailyBackfill] Refreshed {cached_symbols} persistent Redis EOD quote snapshots")

    print(f"[DailyBackfill] DONE: {count} symbols, {total_rows} rows in {duration:.0f}s")
    return {
        "total_symbols": count,
        "total_rows": total_rows,
        "target_rows": target_rows,
        "duration_seconds": duration,
    }

