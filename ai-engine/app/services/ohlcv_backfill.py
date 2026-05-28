"""Daily backfill — stocks + OHLCV, fetch from DNSE REST API, save to PostgreSQL."""

import os
import time
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config.settings import get_settings
from app.services.dnse.api.client import DNSEClient

TZ_VN = timezone(timedelta(hours=7))
CW_PATTERN = re.compile(r'^C[A-Z]{2,4}\d{4,6}$')
ETF_PREFIXES = ('FUE', 'FU_', 'E1', 'KIS', 'SSI')


def is_real_stock(sym: str) -> bool:
    if not sym:
        return False
    if CW_PATTERN.match(sym):
        return False
    if sym.startswith(ETF_PREFIXES):
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


def fetch_today_ohlcv(client, symbol: str) -> Optional[dict]:
    """Fetch today's daily OHLCV from DNSE REST API."""
    now_vn = datetime.now(TZ_VN)
    today_start = now_vn.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = now_vn

    for attempt in range(3):
        try:
            status, body = client.get_ohlc(
                bar_type="STOCK",
                query={
                    "symbol": symbol,
                    "resolution": "1D",
                    "from": int(today_start.timestamp()),
                    "to": int(today_end.timestamp()),
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
        return
    cur.executemany("""
        INSERT INTO ohlcv (time, symbol, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (time, symbol) DO UPDATE SET
            open = EXCLUDED.open,
            high = EXCLUDED.high,
            low = EXCLUDED.low,
            close = EXCLUDED.close,
            volume = EXCLUDED.volume
    """, rows)


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

    exchanges = exchanges or ["STO", "STX", "UPX"]
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
        industry = item.get("industryName") or item.get("IndustryName") or ""

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
            market_cap = int(market_cap) if market_cap else None
        except (ValueError, TypeError):
            pass

        cur.execute("""
            INSERT INTO stocks (symbol, name, exchange, industry, market_cap, ceiling, floor, ref_price, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (symbol) DO UPDATE SET
                name = EXCLUDED.name,
                exchange = EXCLUDED.exchange,
                industry = EXCLUDED.industry,
                market_cap = EXCLUDED.market_cap,
                ceiling = EXCLUDED.ceiling,
                floor = EXCLUDED.floor,
                ref_price = EXCLUDED.ref_price,
                updated_at = EXCLUDED.updated_at
        """, (sym, name, exchange, industry, market_cap, ceiling, floor, ref_price, now_str))
        count += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"[SyncStocks] Upserted {count} stocks")
    return count


def run_daily_backfill(
    exchanges: Optional[list[str]] = None,
    max_symbols: int = 0,
    progress_callback=None,
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

    exchanges = exchanges or ["STO", "STX", "UPX"]
    print(f"[DailyBackfill] Fetching stocks from: {exchanges}...")
    all_stocks = get_all_stocks(client, exchanges)
    real = {s['symbol']: s.get('listedDate', '') for s in all_stocks if is_real_stock(s['symbol'])}
    symbol_map = dict(sorted(real.items(), key=lambda x: x[1] or '9999'))
    print(f"[DailyBackfill] Found {len(symbol_map)} real stocks")

    if max_symbols > 0:
        symbol_map = dict(list(symbol_map.items())[:max_symbols])

    today_str = datetime.now(TZ_VN).strftime("%Y-%m-%d")
    count = 0
    total_rows = 0
    start_time = time.time()

    for sym, _ in symbol_map.items():
        count += 1
        elapsed = time.time() - start_time
        rate = count / elapsed if elapsed > 0 else 0
        remaining = len(symbol_map) - count
        eta = remaining / rate if rate > 0 else 0
        print(f"  [{count}/{len(symbol_map)}] {sym} [{rate:.1f}/s, ETA {eta:.0f}s]")

        result = fetch_today_ohlcv(client, sym)
        if not result or not result.get('t'):
            continue

        rows = []
        for i in range(len(result['t'])):
            candle_time = datetime.fromtimestamp(result['t'][i], tz=timezone.utc)
            rows.append((
                candle_time, sym,
                result.get('o', [0])[i],
                result.get('h', [0])[i],
                result.get('l', [0])[i],
                result.get('c', [0])[i],
                int(result.get('v', [0])[i]),
            ))

        if rows:
            conn = get_db_conn()
            cur = conn.cursor()
            upsert_today(cur, rows)
            conn.commit()
            cur.close()
            conn.close()
            total_rows += len(rows)
            if rows:
                print(f"    ✓ {len(rows)} rows")

        if progress_callback:
            progress_callback(sym, count, len(symbol_map))

    duration = time.time() - start_time
    print(f"[DailyBackfill] DONE: {count} symbols, {total_rows} rows in {duration:.0f}s")
    return {"total_symbols": count, "total_rows": total_rows, "duration_seconds": duration}
