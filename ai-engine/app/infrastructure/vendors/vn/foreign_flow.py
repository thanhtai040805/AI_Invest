"""Foreign Flow Pipeline — Vietstock API.
Pre-compute daily foreign trading flow for all HOSE symbols.
"""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
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
        return datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc).astimezone(TZ_VN).date()
    except Exception:
        return None

def _optional_decimal(value) -> Optional[Decimal]:
    if value is None or str(value).strip() == "":
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, TypeError, ValueError):
        return None

def _optional_int(value) -> Optional[int]:
    result = _optional_decimal(value)
    if result is None or result != result.to_integral_value():
        return None
    return int(result)

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
    seen_pages = set()
    page_index = 1
    page_size = 250
    max_pages = 25
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
                "PageIndex": str(page_index),
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

                page_dates = tuple(row.get("TradingDate") for row in rows)
                if page_dates in seen_pages:
                    logger.warning("Repeated Vietstock page %d for %s; stopping", page_index, symbol)
                    break
                seen_pages.add(page_dates)
                    
                all_data.extend(rows)

                try:
                    total_rows = int(data.get("Rows"))
                except (TypeError, ValueError):
                    total_rows = None

                # Vietstock reports total matches in `Rows`; some responses omit it.
                if total_rows is not None and len(all_data) >= total_rows:
                    break
                if total_rows is None and len(rows) < page_size:
                    break
                if page_index >= max_pages:
                    logger.warning("Vietstock page limit (%d) reached for %s", max_pages, symbol)
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
    """Parse Vietstock rows, excluding dates when the exchange is closed."""
    rows = []
    weekend_rows = 0
    inconsistent_net_volume = 0
    inconsistent_net_value = 0
    for s in raw_rows:
        dt = _parse_ms_date(s.get("TradingDate"))
        if not dt:
            continue
        if dt.weekday() >= 5:
            weekend_rows += 1
            continue
            
        buy_volume = _optional_int(s.get("TotalForeignBuyVol"))
        sell_volume = _optional_int(s.get("TotalForeignSellVol"))
        buy_value = _optional_decimal(s.get("TotalForeignBuyVal"))
        sell_value = _optional_decimal(s.get("TotalForeignSellVal"))
        net_volume = _optional_int(s.get("ForeignDiffBuySellVol"))
        net_value = _optional_decimal(s.get("ForeignDiffBuySellVal"))
        room_remaining = _optional_int(s.get("RemainRoom"))
        room_limit = _optional_int(s.get("TotalRoom"))
        ownership_pct = _optional_decimal(s.get("OwnedRatio"))
        if buy_volume is not None and sell_volume is not None and net_volume is not None and net_volume != buy_volume - sell_volume:
            net_volume = None
            inconsistent_net_volume += 1
        if buy_value is not None and sell_value is not None and net_value is not None and net_value != buy_value - sell_value:
            net_value = None
            inconsistent_net_value += 1

        rows.append((
            symbol, dt,
            buy_volume,
            sell_volume,
            float(buy_value) if buy_value is not None else None,
            float(sell_value) if sell_value is not None else None,
            net_volume,
            float(net_value) if net_value is not None else None,
            room_remaining,
            room_limit,
            float(ownership_pct) if ownership_pct is not None else None,
        ))
    if weekend_rows:
        logger.warning("Skipped %d weekend foreign-flow rows for %s", weekend_rows, symbol)
    if inconsistent_net_volume or inconsistent_net_value:
        logger.warning(
            "Nullified inconsistent Vietstock net fields for %s: volume=%d value=%d",
            symbol, inconsistent_net_volume, inconsistent_net_value,
        )
    return rows

def _get_hose_symbols(storage: StoragePort) -> list[str]:
    """Get current HOSE-listed symbols from the instrument master."""
    query = "SELECT symbol FROM stocks WHERE exchange = 'HOSE' ORDER BY symbol;"
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

def refresh_available_history(max_workers: int = 3) -> dict:
    """Reconcile Vietstock's available history against its current session dates.

    Vietstock currently returns about one year of daily rows. Replace that
    source's rows per symbol in a transaction so legacy date-parsing errors and
    missed sessions are repaired without touching older CafeF-only history.
    """
    end_date = datetime.now(TZ_VN).date()
    requested_start = end_date - timedelta(days=365)
    storage = PostgresAdapter(DB_URL)
    symbols = _get_hose_symbols(storage)
    if not symbols:
        return {"rows": 0, "symbols": 0, "errors": 0, "status": "no_hose_symbols"}

    def _worker(symbol: str):
        with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            token = get_vietstock_token(client, symbol)
            if not token:
                raise RuntimeError(f"No Vietstock token for {symbol}")
            raw = fetch_foreign_flow_for_symbol(
                symbol, requested_start, end_date, token=token, client=client,
            )
        rows = parse_rows(raw, symbol)
        if not rows:
            raise RuntimeError(f"Vietstock returned no parseable history for {symbol}")
        dates = [row[1] for row in rows]
        if len(dates) != len(set(dates)):
            raise RuntimeError(f"Vietstock returned duplicate dates for {symbol}")
        if any(dt.weekday() >= 5 for dt in dates):
            raise RuntimeError(f"Vietstock returned weekend dates for {symbol}")

        # A prior UTC/local-time parse can leave the first row one day early.
        # Clear only Vietstock data from that boundary, then atomically replace
        # it with correctly parsed rows. CafeF history outside collisions stays.
        replace_from = min(dates) - timedelta(days=1)
        with storage.transaction() as cur:
            cur.execute(
                "DELETE FROM foreign_flow WHERE symbol = %s AND source = 'vietstock' "
                "AND trade_date >= %s AND trade_date <= %s",
                (symbol, replace_from, end_date),
            )
            _insert_rows(storage, rows)
        return len(rows), min(dates), max(dates)

    total_rows = 0
    refreshed = 0
    errors = []
    oldest_date = None
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_worker, symbol): symbol for symbol in symbols}
        for future in as_completed(futures):
            symbol = futures[future]
            try:
                count, first_date, last_date = future.result()
                total_rows += count
                refreshed += 1
                oldest_date = min(oldest_date, first_date) if oldest_date else first_date
            except Exception as exc:
                errors.append(symbol)
                logger.error("Vietstock history reconciliation failed for %s: %s", symbol, exc)

    return {
        "rows": total_rows,
        "symbols": refreshed,
        "errors": len(errors),
        "error_symbols": sorted(errors),
        "requested_start": requested_start.isoformat(),
        "available_start": oldest_date.isoformat() if oldest_date else None,
        "end_date": end_date.isoformat(),
    }

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
         net_volume, net_value, room_remaining, room_limit, ownership_pct, source)
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
            source         = EXCLUDED.source
    """
    try:
        storage.execute_values(query, [(*row, "vietstock") for row in rows], page_size=100)
    except Exception as e:
        logger.error("Failed to persist foreign flow: %s", e)
        raise

