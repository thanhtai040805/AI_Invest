"""Incremental corporate-action ingestion from vnstock's VCI company events."""
import logging
import math
import time
from datetime import date
from typing import Any, Optional

import psycopg2
from psycopg2.extras import execute_values

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)
UPSERT_BATCH_SIZE = 25
MIN_REQUEST_INTERVAL_SECONDS = 4.1  # ~15 requests/min; leaves room under the Guest tier's 20/min.


def _present(value: Any) -> Any:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value


def _action_row(symbol: str, event: dict[str, Any]) -> Optional[tuple]:
    title = str(_present(event.get("event_title_vi")) or "")
    title_lower = title.casefold()
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
    elif "cổ tức bằng cổ phiếu" in title_lower or "stock dividend" in title_lower:
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

    note = title or str(_present(event.get("event_title_en")) or "")
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


def refresh_incremental(
    symbols: Optional[list[str]] = None,
    *,
    partition_index: Optional[int] = None,
    partition_count: int = 5,
) -> dict:
    """Fetch latest VCI events and idempotently upsert actionable records."""
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
    if partition_index is not None:
        if partition_count < 1 or not 0 <= partition_index < partition_count:
            raise ValueError("Invalid corporate-action partition")
        symbols = symbols[partition_index::partition_count]
    try:
        from vnstock.explorer.vci.company import Company as VCICompany

        last_request_started = 0.0
        for idx, symbol in enumerate(symbols):
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
    except Exception:
        logger.exception("Corporate-actions refresh aborted; completed batches remain committed")
        raise

    requested = len(symbols or [])
    result = {
        "status": "rate_limited" if rate_limited else "failed" if aborted or requested and fetched_symbols == 0 else "partial" if errors else "success",
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
