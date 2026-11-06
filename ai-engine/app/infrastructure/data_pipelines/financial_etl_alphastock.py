"""AlphaStock-first BCTC ETL; CaféF and VCI fill missing statement rows."""
import logging
import math
import re
from calendar import monthrange
from datetime import date, timedelta
from typing import Any, Optional

import httpx
import psycopg2
from psycopg2.extras import Json

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

BATCH_SIZE = 50
API_BASE = "https://api-ai.alphastock.vn"

EXCLUDED_SYMBOLS = {"KSS", "PCN", "TCD", "HHR", "CTC", "BCG", "B82", "VMD"}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://ai.alphastock.vn/",
    "Origin": "https://ai.alphastock.vn",
}

_CLIENT = httpx.Client(headers=_HEADERS, timeout=60)


def _get_workspace(symbol: str) -> Optional[dict]:
    """Fetch financial report workspace from AlphaStock API."""
    url = f"{API_BASE}/api/v1/financials/report-workspace"
    params = {"symbol": symbol, "quarter_limit": 40, "annual_limit": 15}
    try:
        r = _CLIENT.get(url, params=params)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        logger.warning("AlphaStock fetch failed for %s: %s", symbol, e)
        return None


def _period_label_to_date(label: str) -> Optional[date]:
    """Convert AlphaStock's period labels when a row lacks period_end_date."""
    if not label:
        return None
    m = re.match(r"(\d{4})-Q([1-4])", label)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return {1: date(year, 3, 31), 2: date(year, 6, 30), 3: date(year, 9, 30), 4: date(year, 12, 31)}[q]
    m = re.match(r"(\d{4})$", label)
    if m:
        return date(int(m.group(1)), 12, 31)
    return None


def _clean_nan(data: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in data.items()
    }


def _upsert(
    cur, symbol: str, period_end: date, stmt_type: str, freq: str,
    data: dict, published_date: Optional[date],
):
    cur.execute(
        """INSERT INTO financial_statements
           (symbol, period_end, statement_type, frequency, data, source, published_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (symbol, period_end, statement_type, frequency)
           DO UPDATE SET data = EXCLUDED.data, source = EXCLUDED.source, fetched_at = NOW(),
                         published_date = COALESCE(EXCLUDED.published_date, financial_statements.published_date)""",
        (symbol, period_end, stmt_type, freq, Json(_clean_nan(data)), "alphastock", published_date),
    )


_STMT_MAP = {
    "income_statement": "IS",
    "balance_sheet": "BS",
    "cash_flow": "CF",
    "ratio": "ratios",
}

_PERIOD_TYPE_MAP = {
    "quarter": "quarterly",
    "year": "yearly",
    "annual": "yearly",
}

def _asset_total_override(value: int) -> dict[str, int]:
    return {"tong_cong_tai_san": value, "t\u1ed5ng_c\u1ed9ng_t\u00e0i_s\u1ea3n": value,
            "T\u1ed4NG C\u1ed8NG T\u00c0I S\u1ea2N": value, "t\u1ed5ng c\u1ed9ng t\u00e0i s\u1ea3n": value}


# Issuer-filed corrections where the AlphaStock snapshot differs from the report.
_ISSUER_BS_OVERRIDES = {
    # 2023 issuer annual report's audited 2022 consolidated financial summary (VND million).
    ("PTC", date(2022, 12, 31)): {
        "t\u1ed5ng_c\u1ed9ng_t\u00e0i_s\u1ea3n": 1211656000000,
        "T\u1ed4NG C\u1ed8NG T\u00c0I S\u1ea2N": 1211656000000,
        "a_n\u1ee3_ph\u1ea3i_tr\u1ea3": 703779000000,
        "A. N\u1ee2 PH\u1ea2I TR\u1ea2": 703779000000,
        "b_v\u1ed1n_ch\u1ee7_s\u1edf_h\u1eefu": 331779000000,
        "i_v\u1ed1n_ch\u1ee7_s\u1edf_h\u1eefu": 331779000000,
        "B. V\u1ed0N CH\u1ee6 S\u1ede H\u1eeeU": 331779000000,
        "I. V\u1ed1n ch\u1ee7 s\u1edf h\u1eefu": 331779000000,
        "c_l\u1ee3i_\u00edch_c\u1ed5_\u0111\u00f4ng_thi\u1ec3u_s\u1ed1": 176098000000,
        "C. L\u1ee2I \u00cdCH C\u1ed4 \u0110\u00d4NG THI\u1ec2U S\u1ed0": 176098000000,
        "t\u1ed5ng_c\u1ed9ng_ngu\u1ed3n_v\u1ed1n": 1211656000000,
        "T\u1ed4NG C\u1ed8NG NGU\u1ed2N V\u1ed0N": 1211656000000,
    },
    ("AAT", date(2026, 6, 30)): {
        "tổng_cộng_tài_sản": 1484804218470,
        "TỔNG CỘNG TÀI SẢN": 1484804218470,
        "tổng_cộng_nguồn_vốn": 1484804218470,
        "TỔNG CỘNG NGUỒN VỐN": 1484804218470,
        "a_n\u1ee3_ph\u1ea3i_tr\u1ea3": 723652509769,
        "A. N\u1ee2 PH\u1ea2I TR\u1ea2": 723652509769,
    },
    ("NO1", date(2025, 3, 31)): {
        "tổng_cộng_tài_sản": 651957238665,
        "TỔNG CỘNG TÀI SẢN": 651957238665,
        "tổng_cộng_nguồn_vốn": 651957238665,
        "TỔNG CỘNG NGUỒN VỐN": 651957238665,
    },
    ("GDT", date(2024, 12, 31)): {
        "tổng_cộng_tài_sản": 527508533397,
        "TỔNG CỘNG TÀI SẢN": 527508533397,
    },
    ("AAT", date(2018, 12, 31)): _asset_total_override(598296754930),
    ("PGV", date(2026, 6, 30)): _asset_total_override(54795524975731),
    ("HAR", date(2017, 3, 31)): _asset_total_override(1227397106288),
    ("TCL", date(2020, 3, 31)): _asset_total_override(1110814516120),
    ("GIL", date(2025, 12, 31)): _asset_total_override(3808562796645),
    ("VAB", date(2020, 9, 30)): {
        **_asset_total_override(77465764267437),
        "tong_cong_nguon_von": 77465764267437,
        "tổng_cộng_nguồn_vốn": 77465764267437,
        "TỔNG CỘNG NGUỒN VỐN": 77465764267437,
        "vốn_và_các_quỹ": 5577730675166,
        "Vốn và các quỹ": 5577730675166,
    },

}

_ISSUER_BS_YEARLY_OVERRIDES = {("CRC", date(2025, 12, 31)): _asset_total_override(1389278620184)}


def _latest_completed_quarter(today: date) -> date:
    quarter = (today.month - 1) // 3
    year = today.year
    if quarter == 0:
        year -= 1
        quarter = 4
    month = quarter * 3
    return date(year, month, monthrange(year, month)[1])


def _as_date(value: Any) -> Optional[date]:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def fetch_and_store_financials(
    symbol: str, cur, *, backfill: bool = False, refresh_ratios: bool = True,
) -> dict[str, Any]:
    """Fetch all financial statements for one symbol from AlphaStock and upsert into DB."""
    result = _get_workspace(symbol)
    if not result:
        return {"symbol": symbol, "rows": 0}

    total = 0
    today = date.today()
    history_start = date(today.year - 15, today.month, min(today.day, monthrange(today.year - 15, today.month)[1]))
    statements = result.get("statements", {})

    for api_stmt, db_stmt in _STMT_MAP.items():
        section = statements.get(api_stmt, {})
        for api_period, db_freq in _PERIOD_TYPE_MAP.items():
            if api_period == "annual" and "year" in section:
                continue
            period_data = section.get(api_period, {})
            data_rows = period_data.get("data", [])
            if not data_rows:
                continue

            metric_order = period_data.get("metric_order", [])
            name_map = {}
            for mo in metric_order:
                code = mo.get("metric_code", "")
                name_vi = mo.get("metric_name_vi", "")
                if name_vi:
                    name_map[code] = name_vi

            for row in data_rows:
                period_label = row.get("period_label", "")
                period_end = _as_date(row.get("period_end_date")) or _period_label_to_date(period_label)
                if period_end is None or period_end < history_start:
                    continue

                metrics = row.get("metrics_json", {})
                if not metrics:
                    continue

                result_data = {}
                for code, val in metrics.items():
                    result_data[code] = val
                    name = name_map.get(code)
                    if name:
                        result_data[name] = val

                if db_stmt == "BS":
                    overrides = _ISSUER_BS_OVERRIDES if db_freq == "quarterly" else _ISSUER_BS_YEARLY_OVERRIDES
                    result_data.update(overrides.get((symbol, period_end), {}))

                # Schema compatibility: published_date records when we crawled this row.
                crawl_date = date.today()
                _upsert(
                    cur, symbol, period_end, db_stmt, db_freq, result_data,
                    crawl_date,
                )
                total += 1

    if refresh_ratios:
        try:
            _store_derived_ratios(symbol, cur)
        except Exception as e:
            logger.warning("Derived ratios failed for %s: %s", symbol, e)

    return {"symbol": symbol, "rows": total}


def _store_derived_ratios(symbol: str, cur) -> None:
    # Share the frequency-aware calculation used by the existing pipeline.
    from app.infrastructure.data_pipelines.financial_etl_cafef import _store_derived_ratios as store
    store(symbol, cur)


def _has_complete_latest_quarter(cur, symbol: str) -> bool:
    cur.execute(
        """SELECT statement_type, data FROM financial_statements
           WHERE symbol = %s AND period_end = %s AND frequency = 'quarterly'
             AND source = 'alphastock' AND statement_type IN ('BS', 'IS', 'CF')""",
        (symbol, _latest_completed_quarter(date.today())),
    )
    usable = {
        statement_type for statement_type, data in cur.fetchall()
        if isinstance(data, dict) and sum(value is not None for value in data.values()) >= 8
    }
    return usable == {"BS", "IS", "CF"}


def _fill_missing_with_cafef(
    symbol: str, cur, *, refresh_ratios: bool = True, backfill: bool = False,
    include_ratios: bool = True,
) -> int:
    from app.infrastructure.data_pipelines.financial_etl_cafef import fetch_and_store_financials

    result = fetch_and_store_financials(
        symbol, cur, fill_only=True, refresh_ratios=refresh_ratios,
        backfill=backfill, include_ratios=include_ratios,
    )
    return int(result.get("rows", 0))


def refresh_all() -> dict:
    """Refresh AlphaStock as primary and fill missing statements from fallback providers."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        symbols = [s for s in symbols if s not in EXCLUDED_SYMBOLS]
        logger.info("AlphaStock Financial ETL: %d symbols (%d excluded)",
                     len(symbols), len(EXCLUDED_SYMBOLS))

        total_rows = 0
        errors = 0
        for idx, sym in enumerate(symbols):
            sp_name = f"sp_{idx}"
            cur.execute(f"SAVEPOINT {sp_name}")
            try:
                result = fetch_and_store_financials(
                    sym, cur, backfill=True, refresh_ratios=False
                )
                total_rows += result["rows"]
                total_rows += _fill_missing_with_cafef(
                    sym, cur, refresh_ratios=False, backfill=True, include_ratios=False
                )
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                logger.warning("Failed for %s: %s", sym, e)
                errors += 1
            if idx > 0 and idx % BATCH_SIZE == 0:
                conn.commit()
                logger.info("  Progress: %d/%d symbols, %d rows", idx, len(symbols), total_rows)
        from app.infrastructure.data_pipelines.financial_etl_cafef import _fetch_vci_missing_quarter
        vci_fallback = _fetch_vci_missing_quarter(cur)
        conn.commit()

        logger.info("AlphaStock Financial ETL done: %d rows, %d errors", total_rows, errors)
        return {
            "rows": total_rows,
            "symbols": len(symbols) - errors,
            "errors": errors,
            "vci_fallback": vci_fallback,
        }
    finally:
        cur.close()
        conn.close()


def refresh_incremental(*, backfill: bool = False) -> dict:
    """Incremental: only fetch symbols missing recent financial data."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT DISTINCT fs.symbol
               FROM financial_statements fs
               WHERE fs.period_end >= %s AND fs.frequency = 'quarterly'
                 AND fs.statement_type = 'BS' AND fs.source = 'alphastock'""",
            (date.today() - timedelta(days=90),),
        )
        recent_symbols = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        all_symbols = [r[0] for r in cur.fetchall()]
        stale_symbols = [s for s in all_symbols if s not in recent_symbols and s not in EXCLUDED_SYMBOLS]
        logger.info("AlphaStock Financial ETL incremental: %d stale symbols", len(stale_symbols))

        total_rows = 0
        errors = 0
        cafef_fallback_symbols = 0
        for idx, sym in enumerate(stale_symbols):
            sp_name = f"sp_inc_{idx}"
            cur.execute(f"SAVEPOINT {sp_name}")
            try:
                result = fetch_and_store_financials(sym, cur, backfill=backfill)
                total_rows += result["rows"]
                if not _has_complete_latest_quarter(cur, sym):
                    total_rows += _fill_missing_with_cafef(sym, cur)
                    cafef_fallback_symbols += 1
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                logger.warning("Failed for %s: %s", sym, e)
                errors += 1
            if idx > 0 and idx % BATCH_SIZE == 0:
                conn.commit()
        conn.commit()

        vci_fallback = None
        if not backfill:
            from app.infrastructure.data_pipelines.financial_etl_cafef import _fetch_vci_missing_quarter
            vci_fallback = _fetch_vci_missing_quarter(cur)
            conn.commit()

        logger.info("AlphaStock Financial ETL incremental done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(stale_symbols) - errors, "errors": errors,
                "cafef_fallback_symbols": cafef_fallback_symbols, "vci_fallback": vci_fallback}
    finally:
        cur.close()
        conn.close()
