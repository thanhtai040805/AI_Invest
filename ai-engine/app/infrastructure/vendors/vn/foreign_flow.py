"""Foreign Flow Pipeline — Vietstock API.
Pre-compute daily foreign trading flow for all HOSE symbols.
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
import pandas as pd
from bs4 import BeautifulSoup

from app.infrastructure.database.pg_pool import DB_URL
from app.application.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)

_REQUEST_DELAY = 0.3
TZ_VN = timezone(timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

def _parse_ms_date(val: str) -> Optional[date]:
    """Parse '/Date(1787677200000)/' to python date."""
    if not val or not val.startswith("/Date("):
        return None
    try:
        ts = int(val.replace("/Date(", "").replace(")/", ""))
        return datetime.fromtimestamp(ts / 1000.0).date()
    except Exception:
        return None

from concurrent.futures import ThreadPoolExecutor, as_completed

def get_vietstock_token(client: httpx.Client, symbol: str = "SSI") -> Optional[str]:
    """Get CSRF token from Vietstock page."""
    url = f"https://finance.vietstock.vn/{symbol}/thong-ke-giao-dich.htm?languageid=2"
    try:
        r = client.get(url, timeout=10)
        if r.status_code != 200:
            return None
        soup = BeautifulSoup(r.text, "html.parser")
        token_input = soup.find("input", {"name": "__RequestVerificationToken"})
        if token_input:
            return token_input.get("value")
    except Exception as e:
        logger.error(f"Failed to get Vietstock token for {symbol}: {e}")
    return None

def fetch_foreign_flow_for_symbol(
    symbol: str, 
    start_date: date, 
    end_date: date,
    token: Optional[str] = None,
    client: Optional[httpx.Client] = None
) -> list[dict]:
    """Fetch foreign flow for a specific symbol over a date range using Vietstock."""
    start_str = start_date.strftime("%Y-%m-%d")
    end_str = end_date.strftime("%Y-%m-%d")
    
    all_data = []
    page_index = 1
    page_size = 250
    url_api = "https://finance.vietstock.vn/data/gettradingresult"
    
    close_client = False
    if client is None:
        client = httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True)
        close_client = True

    try:
        if not token:
            token = get_vietstock_token(client, symbol)
            if not token:
                logger.warning(f"Could not get verification token for {symbol}")
                return []
                
        headers_post = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": "https://finance.vietstock.vn",
            "Referer": f"https://finance.vietstock.vn/{symbol}/thong-ke-giao-dich.htm?languageid=2"
        }
        
        while True:
            payload = {
                "Code": symbol,
                "OrderBy": "",
                "OrderDirection": "desc",
                "Page": str(page_index),
                "PageSize": str(page_size),
                "FromDate": start_str,
                "ToDate": end_str,
                "ThemeId": "1",
                "__RequestVerificationToken": token
            }
            
            try:
                resp = client.post(url_api, data=payload, headers=headers_post)
                if resp.status_code != 200:
                    logger.warning(f"Vietstock API {resp.status_code} for {symbol}")
                    break
                    
                data = resp.json()
                if not isinstance(data, dict) or "Data" not in data:
                    break
                    
                rows = data.get("Data", [])
                if not rows:
                    break
                    
                all_data.extend(rows)
                
                # Check pagination & safety brake (max 25 pages = 6,250 trading days ~ 25 years)
                if len(rows) < page_size or page_index >= 25:
                    break
                    
                page_index += 1
                time.sleep(0.1)
                
            except Exception as e:
                logger.error(f"Error fetching foreign flow for {symbol} on page {page_index}: {e}")
                break
    finally:
        if close_client:
            client.close()

    return all_data

def parse_rows(raw_rows: list[dict], symbol: str) -> list[tuple]:
    """Parse Vietstock API response into DB rows."""
    rows = []
    for s in raw_rows:
        dt = _parse_ms_date(s.get("TradingDate"))
        if not dt:
            continue
            
        rows.append((
            symbol, dt,
            int(s.get("TotalForeignBuyVol", 0) or 0),
            int(s.get("TotalForeignSellVol", 0) or 0),
            float(s.get("TotalForeignBuyVal", 0) or 0),
            float(s.get("TotalForeignSellVal", 0) or 0),
            int(s.get("ForeignDiffBuySellVol", 0) or 0),
            float(s.get("ForeignDiffBuySellVal", 0) or 0),
            int(s.get("RemainRoom", 0) or 0),
            int(s.get("TotalRoom", 0) or 0),
            float(s.get("OwnedRatio", 0) or 0),
        ))
    return rows

def _get_hose_symbols(storage: StoragePort) -> list[str]:
    """Get list of distinct HOSE symbols from ohlcv."""
    query = "SELECT DISTINCT symbol FROM ohlcv ORDER BY symbol;"
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        df = pd.read_sql(query, conn)
        conn.close()
        return df['symbol'].tolist()
    except Exception as e:
        logger.error(f"Failed to fetch symbols: {e}")
        return []

def refresh_incremental() -> dict:
    """Incremental: fetch latest available foreign flow data for last 7 days for all symbols."""
    end_date = datetime.now(TZ_VN).date()
    start_date = end_date - timedelta(days=7)
    
    storage = PostgresAdapter(DB_URL)
    symbols = _get_hose_symbols(storage)
    
    with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
        token = get_vietstock_token(client, "SSI")
        total_rows = 0
        for symbol in symbols:
            raw_data = fetch_foreign_flow_for_symbol(symbol, start_date, end_date, token=token, client=client)
            if not raw_data:
                continue
                
            rows = parse_rows(raw_data, symbol)
            if rows:
                _insert_rows(storage, rows)
                total_rows += len(rows)
                
            time.sleep(0.1)
        
    return {"rows": total_rows, "symbols_processed": len(symbols)}

def _get_completed_symbols(storage: StoragePort, min_rows: int = 1000) -> set[str]:
    """Get symbols that already have substantial history in foreign_flow."""
    query = f"SELECT symbol FROM foreign_flow GROUP BY symbol HAVING COUNT(*) >= {min_rows};"
    try:
        import psycopg2
        conn = psycopg2.connect(DB_URL)
        df = pd.read_sql(query, conn)
        conn.close()
        return set(df['symbol'].tolist())
    except Exception:
        return set()

def refresh_all(years: int = 12, max_workers: int = 3, skip_existing: bool = True) -> dict:
    """Backfill foreign flow for all trading days in the last N years for all symbols using multi-threading."""
    end_date = datetime.now(TZ_VN).date()
    start_date = end_date - timedelta(days=int(years * 365.25))
    
    storage = PostgresAdapter(DB_URL)
    all_symbols = _get_hose_symbols(storage)
    
    if skip_existing:
        completed_set = _get_completed_symbols(storage)
        symbols = [s for s in all_symbols if s not in completed_set]
        logger.info(f"Skipping {len(completed_set)} already completed symbols. Remaining to backfill: {len(symbols)}")
    else:
        symbols = all_symbols

    if not symbols:
        logger.info("All symbols already completed! No backfill needed.")
        return {"rows": 0, "symbols": 0, "years": years, "status": "all_skipped"}
        
    logger.info(f"Backfilling {len(symbols)} symbols with {max_workers} parallel workers...")
    
    total_rows = 0
    total_symbols = 0
    
    def _worker(symbol: str):
        with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            token = get_vietstock_token(client, symbol)
            if not token:
                return symbol, 0
            raw_data = fetch_foreign_flow_for_symbol(symbol, start_date, end_date, token=token, client=client)
            if not raw_data:
                return symbol, 0
            rows = parse_rows(raw_data, symbol)
            if rows:
                _insert_rows(storage, rows)
                return symbol, len(rows)
            return symbol, 0

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, sym): sym for sym in symbols}
        completed_count = 0
        for future in as_completed(futures):
            sym = futures[future]
            completed_count += 1
            try:
                symbol, count = future.result()
                if count > 0:
                    total_rows += count
                    total_symbols += 1
                    logger.info(f"[{completed_count}/{len(symbols)}] Backfilled {symbol}: {count} rows")
                else:
                    logger.info(f"[{completed_count}/{len(symbols)}] No data for {symbol}")
            except Exception as e:
                logger.error(f"Worker error for {sym}: {e}")

    logger.info("Foreign flow backfill done: %d symbols, %d rows", total_symbols, total_rows)
    return {"rows": total_rows, "symbols": total_symbols, "years": years}

def _insert_rows(storage: StoragePort, rows: list[tuple]):
    query = """
        INSERT INTO foreign_flow
        (symbol, trade_date, buy_volume, sell_volume, buy_value, sell_value,
         net_volume, net_value, room_remaining, room_limit, ownership_pct)
        VALUES %s
        ON CONFLICT (symbol, trade_date)
        DO UPDATE SET
            buy_volume     = EXCLUDED.buy_volume,
            sell_volume    = EXCLUDED.sell_volume,
            buy_value      = EXCLUDED.buy_value,
            sell_value     = EXCLUDED.sell_value,
            net_volume     = EXCLUDED.net_volume,
            net_value      = EXCLUDED.net_value,
            room_remaining = EXCLUDED.room_remaining,
            room_limit     = EXCLUDED.room_limit,
            ownership_pct  = EXCLUDED.ownership_pct,
            source         = 'vietstock'
    """
    try:
        storage.execute_values(query, rows, page_size=100)
    except Exception as e:
        logger.error(f"Failed to persist foreign flow: {e}")

