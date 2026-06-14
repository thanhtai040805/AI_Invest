"""Foreign Flow Pipeline — CafeF Ajax API (GDNướcNgoài).
Pre-compute daily foreign trading flow for all HOSE symbols.
"""
import logging
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

from app.services.pg_pool import DB_URL
from app.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)

_REQUEST_DELAY = 0.15
API_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataGDNN/GDNuocNgoai.ashx"
TZ_VN = timezone(timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://cafef.vn/",
}


def _parse_response_date(val: str) -> Optional[date]:
    """Parse 'DD/MM/YYYY' from Data.Date field."""
    if not val:
        return None
    try:
        m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", val.strip())
        if m:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    except (ValueError, TypeError):
        pass
    return None


def fetch_foreign_flow(trade_date: date) -> tuple[Optional[date], list[dict]]:
    """Fetch foreign flow for HOSE stocks."""
    date_str = trade_date.strftime("%d/%m/%Y")
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        resp = client.get(API_URL, params={"TradeCenter": "HOSE", "Date": date_str})
        if resp.status_code != 200:
            logger.warning("CafeF foreign flow HTTP %d for %s", resp.status_code, date_str)
            return (None, [])
        data = resp.json()
        if not data.get("Success"):
            logger.warning("CafeF foreign flow failed for %s", date_str)
            return (None, [])

        raw_data = data.get("Data", {})
        actual_date = _parse_response_date(raw_data.get("Date"))
        stocks = raw_data.get("ListDataNN", [])

        if actual_date and actual_date != trade_date:
            logger.info("  Requested %s, API returned data for %s", trade_date, actual_date)
        return (actual_date, stocks)


def parse_rows(raw_stocks: list[dict], trade_date: date) -> list[tuple]:
    """Parse CafeF API response into DB rows."""
    rows = []
    for s in raw_stocks:
        sym = (s.get("Symbol") or "").strip().upper()
        if not sym:
            continue
        rows.append((
            sym, trade_date,
            int(s.get("BuyVolume", 0) or 0),
            int(s.get("SellVolume", 0) or 0),
            float(s.get("BuyValue", 0) or 0),
            float(s.get("SellValue", 0) or 0),
            int(s.get("NetVolume", 0) or 0),
            float(s.get("NetValue", 0) or 0),
            int(s.get("Room", 0) or 0),
            int(s.get("TotalRoom", 0) or 0),
            float(s.get("Percent", 0) or 0),
        ))
    return rows


def refresh_for_date(trade_date: date, storage: Optional[StoragePort] = None) -> dict:
    """Fetch foreign flow — uses actual trading date from API response."""
    if storage is None:
        storage = PostgresAdapter(DB_URL)

    actual_date, raw = fetch_foreign_flow(trade_date)
    logger.info("Foreign flow: %d stocks for trade_date=%s", len(raw), actual_date)
    
    rows = parse_rows(raw, actual_date)
    if not rows:
        return {"rows": 0, "trade_date": str(actual_date), "stocks": 0}

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
            source         = 'cafef'
    """
    try:
        storage.execute_values(query, rows, page_size=100)
        logger.info("Foreign flow: %d rows for %s", len(rows), actual_date)
        return {"rows": len(rows), "trade_date": str(actual_date), "stocks": len(rows)}
    except Exception as e:
        logger.error(f"Failed to persist foreign flow: {e}")
        return {"rows": 0, "error": str(e)}


def refresh_incremental() -> dict:
    """Incremental: fetch latest available foreign flow data."""
    today = datetime.now(TZ_VN).date()
    storage = PostgresAdapter(DB_URL)
    for i in range(7):
        d = today - timedelta(days=i)
        if d.weekday() < 5:
            result = refresh_for_date(d, storage=storage)
            if result["rows"] > 0:
                return result
    return {"rows": 0, "note": "No data found for last 7 trading days"}


def refresh_all(years: int = 3) -> dict:
    """Backfill foreign flow for all trading days in the last N years."""
    from app.services.daily_etl import is_trading_day
    storage = PostgresAdapter(DB_URL)

    end = datetime.now(TZ_VN).date()
    start = end - timedelta(days=int(years * 365.25) + 60)

    total_rows = 0
    total_days = 0
    d = start
    while d <= end:
        if is_trading_day(d):
            result = refresh_for_date(d, storage=storage)
            if result["rows"] > 0:
                total_rows += result["rows"]
                total_days += 1
            if total_days > 0 and total_days % 30 == 0:
                logger.info("  Progress: %d trading days, %d rows", total_days, total_rows)
            time.sleep(_REQUEST_DELAY)
        d += timedelta(days=1)

    logger.info("Foreign flow backfill done: %d trading days, %d rows", total_days, total_rows)
    return {"rows": total_rows, "trading_days": total_days, "years": years}
