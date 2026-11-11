"""Market-wide O(1) batch corporate-action ingestion from Vietcap IQ REST API (with safe fallback)."""
import logging
import math
import time
from datetime import date, datetime, timedelta
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import psycopg2
from psycopg2.extras import execute_values

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)
TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")
UPSERT_BATCH_SIZE = 100
MIN_REQUEST_INTERVAL_SECONDS = 4.1
VCI_EVENTS_URL = "https://iq.vietcap.com.vn/api/iq-insight-service/v1/events"
VCI_HEADERS = {
    "Referer": "https://trading.vietcap.com.vn/",
    "Origin": "https://trading.vietcap.com.vn",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json",
}


def _present(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _normalize_vci_event(item: dict[str, Any]) -> dict[str, Any]:
    """Normalize camelCase Vietcap IQ REST event payload into snake_case keys expected by _action_row."""
    return {
        "ticker": _present(item.get("ticker")) or _present(item.get("symbol")),
        "event_code": _present(item.get("eventCode")) or _present(item.get("event_code")),
        "event_title_vi": _present(item.get("eventTitleVi")) or _present(item.get("event_title_vi")) or _present(item.get("eventNameVi")),
        "event_title_en": _present(item.get("eventTitleEn")) or _present(item.get("event_title_en")) or _present(item.get("eventNameEn")),
        "exright_date": _present(item.get("exrightDate")) or _present(item.get("exright_date")) or _present(item.get("displayDate1")),
        "record_date": _present(item.get("recordDate")) or _present(item.get("record_date")),
        "value_per_share": _present(item.get("valuePerShare")) if item.get("valuePerShare") is not None else _present(item.get("value_per_share")),
        "exercise_ratio": _present(item.get("exerciseRatio")) if item.get("exerciseRatio") is not None else _present(item.get("exercise_ratio")),
    }


def _action_row(symbol: str, event: dict[str, Any]) -> Optional[tuple]:
    title = str(_present(event.get("event_title_vi")) or "")
    title_en = str(_present(event.get("event_title_en")) or "")
    title_lower = f"{title} {title_en}".casefold()
    code = str(_present(event.get("event_code")) or "").upper()
    action_date_value = _present(event.get("exright_date"))
    if not action_date_value:
        return None
    try:
        action_date = date.fromisoformat(str(action_date_value)[:10])
    except ValueError:
        return None

    if code == "DIV" or "cổ tức bằng tiền" in title_lower or "cash dividend" in title_lower:
        action_type = "DIVIDEND"
        value = _present(event.get("value_per_share"))
        ratio = None
        if value is None:
            return None
    elif (
        "cổ tức bằng cổ phiếu" in title_lower
        or "stock dividend" in title_lower
        or "cổ phiếu thưởng" in title_lower
        or "bonus issue" in title_lower
    ):
        action_type = "STOCK_DIVIDEND"
        value = None
        ratio = _present(event.get("exercise_ratio"))
        if ratio is None:
            return None
    elif "chia tách" in title_lower or "stock split" in title_lower:
        action_type = "SPLIT"
        value = None
        ratio = _present(event.get("exercise_ratio"))
        if ratio is None:
            return None
    elif "quyền mua" in title_lower or "rights issue" in title_lower:
        action_type = "RIGHTS"
        value = _present(event.get("value_per_share"))
        ratio = _present(event.get("exercise_ratio"))
    else:
        return None

    record_date_value = _present(event.get("record_date"))
    try:
        record_date = date.fromisoformat(str(record_date_value)[:10]) if record_date_value else None
    except ValueError:
        record_date = None

    note = title or title_en
    return symbol, action_date, action_type, value, ratio, "VND", record_date, note, "vnstock"


def _upsert_rows(rows: list[tuple]) -> None:
    conn = psycopg2.connect(DB_URL)
    try:
        with conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO corporate_actions
                    (symbol, action_date, action_type, value, ratio, currency, record_date, note, source)
                VALUES %s
                ON CONFLICT (symbol, action_date, action_type) DO UPDATE SET
                    value = EXCLUDED.value,
                    ratio = EXCLUDED.ratio,
                    currency = EXCLUDED.currency,
                    record_date = EXCLUDED.record_date,
                    note = EXCLUDED.note,
                    source = EXCLUDED.source
            """, rows, page_size=1000)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _fetch_market_events_batch(
    days_back: int = 120,
    days_ahead: int = 60,
    max_pages: int = 20,
) -> list[dict[str, Any]]:
    """Fetch market-wide corporate action events directly from Vietcap IQ REST API in O(1) paginated requests."""
    today = datetime.now(TZ_VN).date()
    from_date = (today - timedelta(days=days_back)).strftime("%Y%m%d")
    to_date = (today + timedelta(days=days_ahead)).strftime("%Y%m%d")
    normalized_events: list[dict[str, Any]] = []

    with httpx.Client(headers=VCI_HEADERS, timeout=20.0, follow_redirects=True) as client:
        page = 0
        total_pages = 1
        while page < total_pages and page < max_pages:
            resp = client.get(
                VCI_EVENTS_URL,
                params={
                    "fromDate": from_date,
                    "toDate": to_date,
                    "eventCode": "DIV,ISS",
                    "page": page,
                    "size": 100,
                },
            )
            resp.raise_for_status()
            payload = resp.json()
            data = payload.get("data") or {}
            content = data.get("content") if isinstance(data, dict) else []
            if not content:
                break
            for item in content:
                if isinstance(item, dict):
                    normalized_events.append(_normalize_vci_event(item))
            total_pages = int(data.get("totalPages") or 1)
            page += 1

    return normalized_events


def refresh_incremental(
    symbols: Optional[list[str]] = None,
    *,
    partition_index: Optional[int] = None,
    partition_count: int = 5,
    use_batch: bool = True,
) -> dict:
    """Fetch latest VCI events and idempotently upsert actionable records.

    Primary path: O(1) market-wide paginated REST fetch from Vietcap IQ (no vnstock dependency or rate-limit).
    Fallback path: Optional per-symbol vnstock VCI company events when installed.
    """
    fetched_symbols = 0
    event_count = 0
    rows: list[tuple] = []
    saved_actions = 0
    errors: list[str] = []
    rate_limited = False
    aborted = False
    if symbols is None:
        with psycopg2.connect(DB_URL) as conn, conn.cursor() as cur:
            cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE', 'HSX') ORDER BY symbol")
            symbols = [row[0] for row in cur.fetchall()]
    if partition_index is not None and not use_batch:
        if partition_count < 1 or not 0 <= partition_index < partition_count:
            raise ValueError("Invalid corporate-action partition")
        symbols = symbols[partition_index::partition_count]

    symbols_set = {s.upper().strip() for s in (symbols or []) if s}

    # ── Path 1: O(1) Market-Wide Batch API (Vietcap IQ) ──────────────────
    if use_batch:
        try:
            batch_events = _fetch_market_events_batch()
            for record in batch_events:
                ticker = str(record.get("ticker") or "").upper().strip()
                if not ticker or (symbols_set and ticker not in symbols_set):
                    continue
                event_count += 1
                if (row := _action_row(ticker, record)) is not None:
                    rows.append((*row[:-1], "vietcap_iq_batch"))
            if rows:
                deduped = list({(row[0], row[1], row[2]): row for row in rows}.values())
                _upsert_rows(deduped)
                saved_actions = len(deduped)
            result = {
                "status": "success",
                "mode": "vietcap_iq_batch",
                "symbols_requested": len(symbols_set),
                "symbols_fetched": len(symbols_set),
                "symbols_failed": 0,
                "symbols_not_attempted": 0,
                "events_received": event_count,
                "actions_upserted": saved_actions,
                "failed_symbols": [],
            }
            logger.info("Corporate actions refresh (batch O(1)): %s", result)
            return result
        except Exception as batch_exc:
            logger.warning(
                "Vietcap IQ batch corporate-actions fetch failed (%s); attempting optional fallback",
                batch_exc,
            )
            rows.clear()
            event_count = 0
            if partition_index is not None:
                if partition_count < 1 or not 0 <= partition_index < partition_count:
                    raise ValueError("Invalid corporate-action partition")
                symbols = (symbols or [])[partition_index::partition_count]

    # ── Path 2: Optional fallback per-symbol vnstock (if installed) ──────
    try:
        from vnstock.explorer.vci.company import Company as VCICompany
    except (ImportError, SystemExit) as imp_exc:
        logger.warning("Optional vnstock corporate-actions fallback unavailable: %s", imp_exc)
        requested = len(symbols or [])
        return {
            "status": "unavailable",
            "mode": "vnstock_unavailable",
            "symbols_requested": requested,
            "symbols_fetched": 0,
            "symbols_failed": 0,
            "symbols_not_attempted": requested,
            "events_received": 0,
            "actions_upserted": 0,
            "failed_symbols": [],
        }

    try:
        last_request_started = 0.0
        for idx, symbol in enumerate(symbols or []):
            symbol = symbol.upper().strip()
            delay = MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - last_request_started)
            if delay > 0:
                time.sleep(delay)
            last_request_started = time.monotonic()
            try:
                events = VCICompany(symbol=symbol, show_log=False).events()
                if events is None:
                    raise RuntimeError("vnstock returned no response")
                records = events.to_dict("records")
                if records and any(_present(record.get("error_code")) for record in records):
                    raise RuntimeError(str(records[0]))
                fetched_symbols += 1
                event_count += len(records)
                rows.extend(
                    row for record in records
                    if (row := _action_row(symbol, record)) is not None
                )
            except SystemExit as exc:
                errors.append(symbol)
                rate_limited = "rate limit" in str(exc).casefold()
                aborted = not rate_limited
                logger.error("Corporate-action refresh stopped by vnstock for %s: %s", symbol, exc)
                break
            except Exception as exc:
                errors.append(symbol)
                response = getattr(exc, "response", None)
                if getattr(response, "status_code", None) == 429 or "429" in str(exc) or "rate limit" in str(exc).casefold():
                    rate_limited = True
                    logger.error("Corporate-action refresh paused after provider rate limit on %s: %s", symbol, exc)
                    break
                logger.warning("Corporate-action fetch failed for %s: %s", symbol, exc)

            if (idx + 1) % UPSERT_BATCH_SIZE == 0 and rows:
                rows = list({(row[0], row[1], row[2]): row for row in rows}.values())
                _upsert_rows(rows)
                saved_actions += len(rows)
                logger.info("Corporate actions refresh progress: %d/%d symbols", idx + 1, len(symbols))
                rows.clear()

        if rows:
            rows = list({(row[0], row[1], row[2]): row for row in rows}.values())
            _upsert_rows(rows)
            saved_actions += len(rows)
    except (Exception, SystemExit):
        logger.exception("Corporate-actions refresh aborted; completed batches remain committed")
        raise

    requested = len(symbols or [])
    result = {
        "status": "rate_limited" if rate_limited else "failed" if aborted or requested and fetched_symbols == 0 else "partial" if errors else "success",
        "mode": "vnstock_fallback",
        "symbols_requested": requested,
        "symbols_fetched": fetched_symbols,
        "symbols_failed": len(errors),
        "symbols_not_attempted": max(0, requested - fetched_symbols - len(errors)),
        "events_received": event_count,
        "actions_upserted": saved_actions,
        "failed_symbols": errors,
        **({"partition": f"{partition_index + 1}/{partition_count}"} if partition_index is not None else {}),
    }
    logger.info("Corporate actions refresh: %s", result)
    return result
