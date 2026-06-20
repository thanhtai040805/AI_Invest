"""Insider Trades Pipeline — CafeF Ajax API.
Full refresh or incremental (idempotent ON CONFLICT DO NOTHING).
"""
import time
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

from app.infrastructure.database.pg_pool import DB_URL
from app.application.ports.storage import StoragePort
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger(__name__)

API_URL = "https://cafef.vn/du-lieu/Ajax/PageNew/DataHistory/GDCoDong.ashx"
RATE_LIMIT_DELAY = 0.3
BATCH_SIZE = 50
TZ_VN = timezone(timedelta(hours=7))

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://cafef.vn/",
}


def _parse_cafef_date(val):
    if not val or val == "null":
        return None
    try:
        ms = int(val.replace("/Date(", "").replace(")/", ""))
        return datetime.fromtimestamp(ms / 1000, tz=TZ_VN).date()
    except (ValueError, TypeError):
        return None


def _fetch_all_trades(symbol: str, page_size: int = 2000) -> list[dict]:
    all_trades = []
    page = 1
    with httpx.Client(headers=_HEADERS, timeout=15) as client:
        while True:
            try:
                resp = client.get(
                    API_URL,
                    params={
                        "Symbol": symbol,
                        "StartDate": "",
                        "EndDate": "",
                        "PageIndex": page,
                        "PageSize": page_size,
                    },
                )
                if resp.status_code != 200:
                    logger.warning("  HTTP %d for %s page %d", resp.status_code, symbol, page)
                    break
                data = resp.json()
                records = data.get("Data", {}).get("Data", [])
                if not records:
                    break
                all_trades.extend(records)
                total = data.get("Data", {}).get("TotalCount", 0)
                if len(all_trades) >= total:
                    break
                page += 1
                time.sleep(0.1)
            except Exception as e:
                logger.warning("  Error fetching %s page %d: %s", symbol, page, e)
                break
    return all_trades


def _parse_rows(symbol: str, raw: list[dict]) -> list[tuple]:
    rows = []
    for t in raw:
        trade_date = _parse_cafef_date(t.get("PublishedDate"))
        if not trade_date:
            continue
        real_buy = int(t.get("RealBuyVolume", 0) or 0)
        real_sell = int(t.get("RealSellVolume", 0) or 0)
        if real_buy > 0:
            trade_type = "BUY"
            quantity = real_buy
        elif real_sell > 0:
            trade_type = "SELL"
            quantity = real_sell
        else:
            continue

        rows.append((
            symbol,
            trade_date,
            (t.get("TransactionMan") or "").strip(),
            (t.get("TransactionManPosition") or "").strip(),
            (t.get("RelatedMan") or "").strip(),
            (t.get("RelatedManPosition") or "").strip(),
            trade_type,
            quantity,
            int(t.get("VolumeBeforeTransaction", 0) or 0),
            int(t.get("VolumeAfterTransaction", 0) or 0),
            float(t.get("TyLeSoHuu", 0) or 0),
            int(t.get("PlanBuyVolume", 0) or 0),
            int(t.get("PlanSellVolume", 0) or 0),
            _parse_cafef_date(t.get("PlanBeginDate")),
            _parse_cafef_date(t.get("PlanEndDate")),
            _parse_cafef_date(t.get("RealEndDate")),
        ))
    return rows


def _process_symbols(symbols: list[str], storage: StoragePort) -> dict:
    total_new = 0
    total_err = 0
    for idx, sym in enumerate(symbols):
        if idx > 0 and idx % BATCH_SIZE == 0:
            logger.info("  Progress: %d/%d, %d new rows", idx, len(symbols), total_new)
            time.sleep(1)
        try:
            raw = _fetch_all_trades(sym)
            if not raw:
                continue
            rows = _parse_rows(sym, raw)
            if not rows:
                continue
            
            storage.execute_values(
                """INSERT INTO insider_trades
                   (symbol, trade_date, trader_name, trader_position,
                    related_man, related_man_position,
                    trade_type, quantity,
                    before_volume, after_volume, ownership_pct,
                    plan_buy_volume, plan_sell_volume,
                    plan_begin_date, plan_end_date, real_end_date)
                   VALUES %s
                   ON CONFLICT (symbol, trade_date, trader_name, quantity, trade_type) DO NOTHING""",
                rows,
                page_size=100,
            )
            total_new += len(rows)
            time.sleep(RATE_LIMIT_DELAY)
        except Exception as e:
            logger.warning("Failed for %s: %s", sym, e)
            total_err += 1
            time.sleep(RATE_LIMIT_DELAY * 2)
    return {"new_rows": total_new, "errors": total_err, "symbols": len(symbols)}


def refresh_all(storage: Optional[StoragePort] = None) -> dict:
    """Full refresh: fetch all insider trades for all HOSE symbols."""
    if storage is None:
        storage = PostgresAdapter(DB_URL)
        
    try:
        rows = storage.fetch_all("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        symbols = [r[0] for r in rows]
        logger.info("Insider trades full refresh: %d symbols", len(symbols))
        result = _process_symbols(symbols, storage)
        logger.info("Insider trades done: %d new rows, %d errors", result["new_rows"], result["errors"])
        return result
    except Exception as e:
        logger.error(f"Error in insider trades refresh_all: {e}")
        return {"new_rows": 0, "errors": 1, "symbols": 0}


def refresh_incremental() -> dict:
    """Incremental: same as full (idempotent via ON CONFLICT).
    Insider trades are infrequent — full scan is the simplest correct approach.
    """
    return refresh_all()
