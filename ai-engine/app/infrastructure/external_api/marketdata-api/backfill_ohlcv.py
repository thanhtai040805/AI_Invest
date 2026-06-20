#!/usr/bin/env python3
"""
Historical OHLCV backfill — CLI only, for initial data setup.
For daily backfill, use the auto_run in app.infrastructure.data_pipelines.backfill_service.

Usage:
  python backfill_ohlcv.py --exchange STO
  python backfill_ohlcv.py --skip-existing
  python backfill_ohlcv.py --max 50
"""
import os
import sys
import time
import argparse

_script_dir = os.path.dirname(os.path.abspath(__file__))
_ai_root = os.path.dirname(os.path.dirname(_script_dir))
sys.path.insert(0, _ai_root)

from app.config.settings import get_settings

CW_PATTERN = __import__('re').compile(r'^C[A-Z]{2,4}\d{4,6}$')
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
    return psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest"))


def get_all_stocks(client, market_ids):
    import json
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


def fetch_ohlcv(client, symbol, from_ts, to_ts):
    import json
    import time as _time
    for attempt in range(3):
        try:
            status, body = client.get_ohlc(
                bar_type="STOCK",
                query={"symbol": symbol, "resolution": "1D", "from": from_ts, "to": to_ts},
                dry_run=False,
            )
            if status == 429:
                _time.sleep(30)
                continue
            if status == 200 and body:
                if isinstance(body, str):
                    body = json.loads(body)
                if isinstance(body, dict) and body.get('t'):
                    return body
            return None
        except Exception as e:
            if attempt < 2:
                _time.sleep(2)
                continue
            print(f"  [Error] {e}")
            return None


def process_symbol(client, symbol, start_date):
    from datetime import datetime, timedelta, timezone
    import time as _time
    TZ_VN = timezone(timedelta(hours=7))

    conn = get_db_conn()
    cur = conn.cursor()

    # Ensure table exists
    cur.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'ohlcv')")
    if not cur.fetchone()[0]:
        cur.execute("""
            CREATE TABLE ohlcv (
                time TIMESTAMPTZ NOT NULL,
                symbol TEXT NOT NULL,
                open DECIMAL(12,2) NOT NULL,
                high DECIMAL(12,2) NOT NULL,
                low DECIMAL(12,2) NOT NULL,
                close DECIMAL(12,2) NOT NULL,
                volume BIGINT NOT NULL,
                CONSTRAINT ohlcv_pkey PRIMARY KEY (time, symbol)
            )
        """)

    cur.execute("SELECT time FROM ohlcv WHERE symbol = %s", (symbol,))
    existing = {row[0] for row in cur.fetchall()}
    cur.close()
    conn.close()

    try:
        start_dt = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=TZ_VN)
    except (ValueError, TypeError):
        return 0

    now_vn = datetime.now(TZ_VN)
    yesterday = now_vn.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(seconds=1)
    start_ts = int(start_dt.timestamp())
    end_ts = int(yesterday.timestamp())

    if start_ts >= end_ts:
        return 0

    total_new = 0
    current = start_ts
    while current < end_ts:
        chunk_end = min(current + 365 * 86400, end_ts)
        result = fetch_ohlcv(client, symbol, current, chunk_end)
        _time.sleep(0.1)

        if result and result.get('t'):
            rows = []
            for i in range(len(result['t'])):
                ts = datetime.fromtimestamp(result['t'][i], tz=TZ_VN)
                if ts in existing:
                    continue
                rows.append((
                    ts, symbol,
                    result.get('o', [0] * len(result['t']))[i],
                    result.get('h', [0] * len(result['t']))[i],
                    result.get('l', [0] * len(result['t']))[i],
                    result.get('c', [0] * len(result['t']))[i],
                    int(result.get('v', [0] * len(result['t']))[i]),
                ))
            if rows:
                conn = get_db_conn()
                cur = conn.cursor()
                cur.executemany("""
                    INSERT INTO ohlcv (time, symbol, open, high, low, close, volume)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (time, symbol) DO NOTHING
                """, rows)
                conn.commit()
                cur.close()
                conn.close()
                total_new += len(rows)
            if rows or result.get('t'):
                existing.update(datetime.fromtimestamp(t, tz=timezone.utc) for t in result['t'])

        current = chunk_end
    return total_new


def main():
    parser = argparse.ArgumentParser(description="Backfill HISTORICAL OHLCV data (initial setup)")
    parser.add_argument("--exchange", default="STO",
                        help="Exchange(s): STO, STX, UPX, or comma-sep")
    parser.add_argument("--max", type=int, default=0, help="Max symbols")
    parser.add_argument("--skip-existing", action="store_true", help="Skip symbols already in DB")
    args = parser.parse_args()

    from app.infrastructure.external_api.dnse.api.client import DNSEClient
    settings = get_settings()
    client = DNSEClient(api_key=settings.dnse_api_key, api_secret=settings.dnse_api_secret,
                        base_url=settings.dnse_base_url)

    exchanges = [e.strip().upper() for e in args.exchange.split(",")]
    print(f"Fetching stocks from: {exchanges}...")
    all_stocks = get_all_stocks(client, exchanges)
    real = {s['symbol']: s.get('listedDate', '') for s in all_stocks if is_real_stock(s['symbol'])}
    symbol_map = dict(sorted(real.items(), key=lambda x: x[1] or '9999'))
    print(f"Found {len(symbol_map)} real stocks")

    if args.skip_existing:
        conn = get_db_conn()
        cur = conn.cursor()
        cur.execute("SELECT DISTINCT symbol FROM ohlcv")
        existing_syms = {r[0] for r in cur.fetchall()}
        cur.close()
        conn.close()
        symbol_map = {k: v for k, v in symbol_map.items() if k not in existing_syms}
        print(f"After skipping existing: {len(symbol_map)} symbols remaining")

    if args.max > 0:
        symbol_map = dict(list(symbol_map.items())[:args.max])

    count = 0
    total_rows = 0
    start_time = time.time()

    for sym, listed in symbol_map.items():
        start_date = listed or "2012-01-01"
        count += 1
        elapsed = time.time() - start_time
        rate = count / elapsed if elapsed > 0 else 0
        remaining = len(symbol_map) - count
        eta = remaining / rate if rate > 0 else 0
        print(f"\n[{count}/{len(symbol_map)}] {sym} (listed={start_date}) [{rate:.1f}/s, ETA {eta:.0f}s]")
        rows = process_symbol(client, sym, start_date)
        if rows:
            total_rows += rows
            print(f"  ✓ {rows} rows")

    print(f"\n=== DONE: {count} symbols, {total_rows} total rows in {time.time()-start_time:.0f}s ===")


if __name__ == "__main__":
    main()
