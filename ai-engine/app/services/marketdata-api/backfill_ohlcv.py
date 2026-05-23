#!/usr/bin/env python3
"""
Backfill historical OHLCV data from DNSE API into PostgreSQL.
Fetches OHLCV for ALL real stocks from HOSE/HNX/UPCoM.

Usage:
  python backfill_ohlcv.py                          # all exchanges (with rate limiting)
  python backfill_ohlcv.py --exchange STO           # HOSE only
  python backfill_ohlcv.py --exchange STX           # HNX only
  python backfill_ohlcv.py --exchange STO,STX       # HOSE + HNX
  python backfill_ohlcv.py --dry-run                # preview
  python backfill_ohlcv.py --fast                   # reduce sleep (still rate-limited)
  python backfill_ohlcv.py --no-rate-limit          # disable rate limiting (not recommended)
  python backfill_ohlcv.py --max 50                 # process only 50 symbols
  python backfill_ohlcv.py --skip-existing           # skip symbols already in DB

Rate Limiting:
  By default, respects 1000 requests/hour limit with sliding window tracking.
  The script will automatically sleep when approaching the limit.
"""
import os
import sys
import time
import json
import re
import argparse
from datetime import datetime, timedelta, timezone

_script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ai_root = os.path.dirname(os.path.dirname(_script_dir))
sys.path.insert(0, _ai_root)
sys.path.insert(0, _script_dir)
from dnse import DNSEClient
from app.config.settings import get_settings

DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
TZ_VN = timezone(timedelta(hours=7))
STEP_DAYS = 365
RATE_LIMIT_SLEEP = 0.15
RATE_LIMIT_REQUESTS_PER_HOUR = 1000

CW_PATTERN = re.compile(r'^C[A-Z]{2,4}\d{4,6}$')
ETF_PREFIXES = ('FUE', 'FU_', 'E1', 'KIS', 'SSI')


class RateLimiter:
    def __init__(self, max_requests_per_hour: int):
        self.max_requests = max_requests_per_hour
        self.requests = []
    
    def wait_if_needed(self):
        now = time.time()
        hour_ago = now - 3600
        
        # Remove requests older than 1 hour
        self.requests = [t for t in self.requests if t > hour_ago]
        
        # If we're at the limit, sleep until we can make another request
        if len(self.requests) >= self.max_requests:
            oldest = self.requests[0]
            sleep_time = oldest + 3600 - now
            if sleep_time > 0:
                print(f"  [Rate Limit] At {len(self.requests)}/{self.max_requests} requests, sleeping {sleep_time:.1f}s...")
                time.sleep(sleep_time)
                # Clean up old requests after sleeping
                now = time.time()
                hour_ago = now - 3600
                self.requests = [t for t in self.requests if t > hour_ago]
        
        # If we're close to the limit (80%), add small delay to spread requests
        elif len(self.requests) >= self.max_requests * 0.8:
            time.sleep(1)
    
    def record_request(self):
        self.requests.append(time.time())


def is_real_stock(sym: str) -> bool:
    if not sym:
        return False
    if CW_PATTERN.match(sym):
        return False
    if sym.startswith(ETF_PREFIXES):
        return False
    return True


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


def get_db_conn():
    import psycopg2
    return psycopg2.connect(DB_URL)


def ensure_table(cur):
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


def get_existing_dates(cur, symbol):
    cur.execute("SELECT time FROM ohlcv WHERE symbol = %s", (symbol,))
    return {row[0] for row in cur.fetchall()}


def upsert_ohlcv(cur, rows):
    if not rows:
        return
    cur.executemany("""
        INSERT INTO ohlcv (time, symbol, open, high, low, close, volume)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (time, symbol) DO NOTHING
    """, rows)


def fetch_ohlcv(client, symbol, from_ts, to_ts, rate_limiter=None):
    if rate_limiter:
        rate_limiter.wait_if_needed()
    
    for attempt in range(3):
        try:
            status, body = client.get_ohlc(
                bar_type="STOCK",
                query={
                    "symbol": symbol,
                    "resolution": "1D",
                    "from": from_ts,
                    "to": to_ts,
                },
                dry_run=False,
            )
            
            if rate_limiter:
                rate_limiter.record_request()
            
            if status == 429:
                print(f"  [429] Rate limited, sleeping 30s...")
                time.sleep(30)
                continue
            if status == 200 and body:
                if isinstance(body, str):
                    body = json.loads(body)
                if isinstance(body, dict) and body.get('t'):
                    return body
            return None
        except Exception as e:
            if attempt < 2:
                time.sleep(2)
                continue
            print(f"  [Error] {e}")
            return None


def process_symbol(client, symbol: str, start_date: str, dry_run: bool, fast: bool, rate_limiter=None):
    sleep_time = 0.05 if fast else RATE_LIMIT_SLEEP

    conn = get_db_conn()
    cur = conn.cursor()
    ensure_table(cur)
    existing = get_existing_dates(cur, symbol)
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
    step = STEP_DAYS * 86400

    if start_ts >= end_ts:
        return 0

    total_new = 0
    current = start_ts

    while current < end_ts:
        chunk_end = min(current + step, end_ts)

        if dry_run:
            print(f"  [{symbol}] Would fetch: {datetime.fromtimestamp(current, TZ_VN).strftime('%Y-%m-%d')} -> {datetime.fromtimestamp(chunk_end, TZ_VN).strftime('%Y-%m-%d')}")
            current = chunk_end
            continue

        result = fetch_ohlcv(client, symbol, current, chunk_end, rate_limiter)
        time.sleep(sleep_time)

        if result and result.get('t'):
            rows = []
            for i in range(len(result['t'])):
                ts = datetime.fromtimestamp(result['t'][i], tz=timezone.utc)
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
                upsert_ohlcv(cur, rows)
                conn.commit()
                cur.close()
                conn.close()
                total_new += len(rows)

            if rows or result.get('t'):
                existing.update(
                    datetime.fromtimestamp(t, tz=timezone.utc)
                    for t in result['t']
                )

        current = chunk_end

    return total_new


def main():
    parser = argparse.ArgumentParser(description="Backfill OHLCV data from DNSE to PostgreSQL")
    parser.add_argument("--symbols", help="Comma-separated symbols (overrides --exchange)")
    parser.add_argument("--exchange", default="STO",
                        help="Exchange(s): STO (HOSE), STX (HNX), UPX (UPCoM), or comma-sep (default: STO)")
    parser.add_argument("--dry-run", action="store_true", help="Preview only")
    parser.add_argument("--fast", action="store_true", help="Reduce sleep for speed")
    parser.add_argument("--max", type=int, default=0, help="Max symbols to process (0 = all)")
    parser.add_argument("--skip-existing", action="store_true", help="Skip symbols already in DB")
    parser.add_argument("--no-rate-limit", action="store_true", help="Disable rate limiting (not recommended)")
    args = parser.parse_args()

    settings = get_settings()
    client = DNSEClient(
        api_key=settings.dnse_api_key,
        api_secret=settings.dnse_api_secret,
        base_url=settings.dnse_base_url,
    )
    
    rate_limiter = None
    if not args.no_rate_limit:
        rate_limiter = RateLimiter(RATE_LIMIT_REQUESTS_PER_HOUR)
        print(f"Rate limiting enabled: {RATE_LIMIT_REQUESTS_PER_HOUR} requests/hour")

    if args.symbols:
        symbols = [s.strip().upper() for s in args.symbols.split(",")]
        symbol_map = {s: "" for s in symbols}
    else:
        exchanges = [e.strip().upper() for e in args.exchange.split(",")]
        print(f"Fetching stocks from: {exchanges}...")
        all_stocks = get_all_stocks(client, exchanges)
        real = {s['symbol']: s.get('listedDate', '') for s in all_stocks if is_real_stock(s['symbol'])}
        symbol_map = dict(sorted(real.items(), key=lambda x: x[1] or '9999'))
        print(f"Found {len(symbol_map)} real stocks")

    if args.dry_run:
        print(f"DRY RUN — would process {len(symbol_map)} symbols\n")

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

        print(f"\n[{count}/{len(symbol_map)}] {sym} (listed={start_date}) "
              f"[{rate:.1f}/s, ETA {eta:.0f}s]")

        rows = process_symbol(client, sym, start_date, dry_run=args.dry_run, fast=args.fast, rate_limiter=rate_limiter)

        if rows:
            total_rows += rows
            print(f"  ✓ {rows} rows")

    print(f"\n=== DONE: {count} symbols, {total_rows} total rows in {time.time()-start_time:.0f}s ===")


if __name__ == "__main__":
    main()
