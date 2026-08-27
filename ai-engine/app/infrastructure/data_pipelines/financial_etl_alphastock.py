"""Financial Statements ETL — AlphaStock API → financial_statements + financial_ratios tables.

Replaces cafef ETL. Fetches from api-ai.alphastock.vn using httpx.
Single endpoint returns BS/IS/CF/ratios for 24 quarters + annual.
"""
import logging
import math
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

import httpx
import psycopg2
from psycopg2.extras import Json

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))
BATCH_SIZE = 50
API_BASE = "https://api-ai.alphastock.vn"

EXCLUDED_SYMBOLS = {"KSS", "PCN", "TCD", "HHR", "CTC", "BCG", "B82", "VMD"}

SPECIAL_FY = {"SBT", "LSS"}

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
    """Convert '2026-Q1' → date(2026, 3, 31).

    For special FY symbols (SBT, LSS): fiscal year Jul-Jun.
    The period_label remains calendar-based (e.g. '2026-Q1' = Jan-Mar),
    period_end is still the last day of that calendar quarter.
    """
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


def _upsert(cur, symbol: str, period_end: date, stmt_type: str, freq: str, data: dict):
    published_date = period_end + timedelta(days=45 if freq == "quarterly" else 90)
    cur.execute(
        """INSERT INTO financial_statements
           (symbol, period_end, statement_type, frequency, data, source, published_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (symbol, period_end, statement_type, frequency)
           DO UPDATE SET data = EXCLUDED.data, fetched_at = NOW(), published_date = EXCLUDED.published_date""",
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
    "annual": "yearly",
}


def fetch_and_store_financials(symbol: str, cur) -> dict[str, Any]:
    """Fetch all financial statements for one symbol from AlphaStock and upsert into DB."""
    result = _get_workspace(symbol)
    if not result:
        return {"symbol": symbol, "rows": 0}

    total = 0
    statements = result.get("statements", {})

    for api_stmt, db_stmt in _STMT_MAP.items():
        section = statements.get(api_stmt, {})
        for api_period, db_freq in _PERIOD_TYPE_MAP.items():
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
                period_end = _period_label_to_date(period_label)
                if period_end is None:
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

                _upsert(cur, symbol, period_end, db_stmt, db_freq, result_data)
                total += 1

    try:
        _store_derived_ratios(symbol, cur)
    except Exception as e:
        logger.warning("Derived ratios failed for %s: %s", symbol, e)

    return {"symbol": symbol, "rows": total}


def _extract_value(fs_rows: list, keywords: list[str], default: Optional[float] = None) -> Optional[float]:
    for row in fs_rows:
        if isinstance(row, (tuple, list)) and len(row) >= 3:
            data = row[2]
        elif isinstance(row, (tuple, list)) and len(row) >= 1:
            data = row[0]
        else:
            data = row
        if isinstance(data, dict):
            for k, v in data.items():
                if any(kw.lower() in k.lower() for kw in keywords):
                    if isinstance(v, (int, float)):
                        return float(v)
        elif isinstance(data, str):
            import json
            try:
                d = json.loads(data)
                for k, v in d.items():
                    if any(kw.lower() in k.lower() for kw in keywords):
                        if isinstance(v, (int, float)):
                            return float(v)
            except (json.JSONDecodeError, TypeError):
                pass
    return default


_REVENUE_KEYS = [
    "doanh thu thuần", "doanh thu thuan", "3_doanh_thu_thuần",
    "doanh thu bán hàng", "thu nhập lãi thuần",
    "doanh thu phí bảo hiểm thuần",
]

_COGS_KEYS = ["giá vốn hàng bán", "gia von hang ban", "giá_vốn"]

_NI_KEYS = [
    "18_lợi_nhuận_sau_thuế",
    "lợi nhuận sau thuế thu nhập doanh nghiệp",
    "lợi nhuận sau thuế", "loi nhuan sau thue", "lợi_nhuận_sau_thuế",
    "29_lợi_nhuận_sau_thuế",
]

_ASSET_KEYS = [
    "tổng cộng tài sản", "tong cong tai san", "tổng_cộng_tài_sản",
    "tổng cộng tài sản (270=100+200)",
]

_LIAB_KEYS = ["tổng nợ phải trả", "tong no phai tra", "c_nợ_phải_trả", "C. NỢ PHẢI TRẢ", "nợ phải trả (300=210+330)"]

_EQUITY_KEYS = [
    "vốn chủ sở hữu", "von chu so huu", "i_vốn_chủ_sở_hữu",
    "vốn chủ sở hữu (400=410+430)", "b_vốn_chủ_sở_hữu",
]

_CFO_KEYS = [
    "lưu chuyển tiền thuần từ hoạt động kinh doanh",
    "luu chuyen tien thuan tu hoat dong kinh doanh",
    "lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh",
]

_CAPEX_KEYS = [
    "tiền chi để mua sắm, xây dựng tscđ",
    "tien chi de mua sam xay dung tscd",
    "tiền chi mua sắm, xây dựng tscđ",
    "tiền_mua_tài_sản_cố_định",
    "5_tiền_mua_tài_sản_cố_định",
]

_PE_KEYS = ["p/e", "chỉ số p/e", "chỉ_số_giá_thị_trường_trên_thu_nhập_p_e", "pe"]

_PB_KEYS = ["p/b", "chỉ số p/b", "chỉ_số_giá_thị_trường_trên_giá_trị_sổ_sách_p_b", "pb"]

_ROE_KEYS = ["roe"]
_ROA_KEYS = ["roa"]

_DE_KEYS = ["nợ trên vốn", "debt/equity", "d/e", "nợ vay trên vốn", "debt_equity"]

_CR_KEYS = ["thanh toán hiện hành", "current ratio", "current_ratio"]

_CASH_KEYS = ["tiền và các khoản tương đương tiền", "i_tiền_và_các_khoản", "tiền và tương đương tiền"]

_EBITDA_KEYS = ["ebitda", "ebit", "lợi nhuận thuần từ hoạt động kinh doanh"]

_EVEBITDA_KEYS = ["ev/ebitda", "giá trị doanh nghiệp trên lợi nhuận trước thuế, khấu hao và lãi vay"]

_GM_KEYS = ["lợi nhuận gộp", "gross margin", "gộp biên", "gross_margin"]
_NM_KEYS = ["lợi nhuận ròng", "net margin", "sinh lợi trên doanh thu", "net_margin"]


def _store_derived_ratios(symbol: str, cur) -> None:
    """Compute and store financial_ratios for ALL available periods."""
    cur.execute(
        """SELECT period_end, statement_type, data
           FROM financial_statements
           WHERE symbol = %s
           ORDER BY period_end DESC""",
        (symbol,),
    )
    stmt_rows = cur.fetchall()
    if not stmt_rows:
        return

    import json

    periods: dict[date, dict[str, dict]] = {}
    for pe, st, raw in stmt_rows:
        data = raw if isinstance(raw, dict) else (json.loads(raw) if isinstance(raw, str) else {})
        periods.setdefault(pe, {})[st] = data

    sorted_periods = sorted(periods.keys(), reverse=True)

    for pe in sorted_periods:
        bs_data = periods[pe].get("BS", {})
        inc_data = periods[pe].get("IS", {})
        cf_data = periods[pe].get("CF", {})
        rat_data = periods[pe].get("ratios", {})

        if not bs_data or not inc_data:
            continue

        vn_pe = _extract_value([rat_data], _PE_KEYS)
        vn_pb = _extract_value([rat_data], _PB_KEYS)
        vn_roe = _extract_value([rat_data], _ROE_KEYS)
        vn_roa = _extract_value([rat_data], _ROA_KEYS)
        vn_gm = _extract_value([rat_data], _GM_KEYS)
        vn_nm = _extract_value([rat_data], _NM_KEYS)

        assets = _extract_value([bs_data], _ASSET_KEYS)
        liab = _extract_value([bs_data], _LIAB_KEYS)
        equity = _extract_value([bs_data], _EQUITY_KEYS)
        if equity is None and assets is not None and liab is not None:
            equity = assets - liab

        revenue = _extract_value([inc_data], _REVENUE_KEYS)
        cogs = _extract_value([inc_data], _COGS_KEYS)
        ni = _extract_value([inc_data], _NI_KEYS)
        cfo = _extract_value([cf_data], _CFO_KEYS)
        capex_raw = _extract_value([cf_data], _CAPEX_KEYS)
        capex = abs(capex_raw) if capex_raw is not None else None

        gross_margin_fb = None
        if revenue is not None and cogs is not None and revenue != 0:
            gross_margin_fb = (revenue - cogs) / revenue
        net_margin_fb = None
        if ni is not None and revenue is not None and revenue != 0:
            net_margin_fb = ni / revenue

        # ROE/ROA fallback from BS/IS (AlphaStock API doesn't include them in ratios)
        roe_fb = None
        if ni is not None and equity is not None and equity != 0:
            roe_fb = ni / equity
        roa_fb = None
        if ni is not None and assets is not None and assets != 0:
            roa_fb = ni / assets

        final_roe = vn_roe if vn_roe is not None else roe_fb
        final_roa = vn_roa if vn_roa is not None else roa_fb

        final_gm = vn_gm if vn_gm is not None else gross_margin_fb
        final_nm = vn_nm if vn_nm is not None else net_margin_fb

        fcf = None
        if cfo is not None and capex is not None:
            fcf = cfo - capex

        mcap = None
        if vn_pe is not None and ni is not None:
            mcap = vn_pe * ni

        debt_equity = _extract_value([rat_data], _DE_KEYS)
        if debt_equity is None and liab is not None and equity is not None and equity != 0:
            debt_equity = liab / equity

        current_ratio = _extract_value([rat_data], _CR_KEYS)
        if current_ratio is None:
            ca = _extract_value([bs_data], ["tài sản ngắn hạn", "ngắn hạn", "a_tài_sản_ngắn_hạn"])
            cl = _extract_value([bs_data], ["nợ ngắn hạn", "i_nợ_ngắn_hạn"])
            if ca is not None and cl is not None and cl != 0:
                current_ratio = ca / cl

        fcf_yield = None
        if fcf is not None and mcap is not None and mcap != 0:
            fcf_yield = fcf / mcap

        ev_ebitda = _extract_value([rat_data], _EVEBITDA_KEYS)
        if ev_ebitda is None and mcap is not None and liab is not None:
            cash = _extract_value([bs_data], _CASH_KEYS)
            ev = mcap + liab - (cash or 0)
            ebitda = _extract_value([inc_data], _EBITDA_KEYS)
            if ebitda is not None and ebitda != 0:
                ev_ebitda = ev / ebitda

        tgt = pe - timedelta(days=365)
        prev_inc = None
        for other_pe in sorted_periods:
            if other_pe <= tgt:
                prev_inc = periods[other_pe].get("IS")
                break
        yoy_rev = None
        yoy_ni = None
        if prev_inc:
            rev_prev = _extract_value([prev_inc], _REVENUE_KEYS)
            ni_prev = _extract_value([prev_inc], _NI_KEYS)
            if revenue is not None and rev_prev is not None and rev_prev != 0:
                yoy_rev = (revenue - rev_prev) / abs(rev_prev)
            if ni is not None and ni_prev is not None and ni_prev != 0:
                yoy_ni = (ni - ni_prev) / abs(ni_prev)

        freq = "yearly" if pe.month == 12 and pe.day == 31 else "quarterly"
        published_date = pe + timedelta(days=90 if freq == "yearly" else 45)

        cur.execute(
            """INSERT INTO financial_ratios
               (symbol, ratio_date, pe, pb, roe, roa, debt_equity, current_ratio,
                gross_margin, net_margin, fcf_yield, ev_ebitda,
                yoy_revenue_growth, yoy_earnings_growth, published_date)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (symbol, ratio_date)
               DO UPDATE SET
                   pe = EXCLUDED.pe, pb = EXCLUDED.pb,
                   roe = EXCLUDED.roe, roa = EXCLUDED.roa,
                   debt_equity = EXCLUDED.debt_equity,
                   current_ratio = EXCLUDED.current_ratio,
                   gross_margin = EXCLUDED.gross_margin,
                   net_margin = EXCLUDED.net_margin,
                   fcf_yield = EXCLUDED.fcf_yield,
                   ev_ebitda = EXCLUDED.ev_ebitda,
                   yoy_revenue_growth = EXCLUDED.yoy_revenue_growth,
                   yoy_earnings_growth = EXCLUDED.yoy_earnings_growth,
                   published_date = EXCLUDED.published_date,
                   updated_at = NOW()""",
             (symbol, pe, vn_pe, vn_pb, final_roe, final_roa, debt_equity, current_ratio,
             final_gm, final_nm, fcf_yield, ev_ebitda, yoy_rev, yoy_ni, published_date),
        )


def refresh_all() -> dict:
    """Full refresh: delete old data, refetch all HOSE symbols from AlphaStock."""
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
                result = fetch_and_store_financials(sym, cur)
                total_rows += result["rows"]
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                logger.warning("Failed for %s: %s", sym, e)
                errors += 1
            if idx > 0 and idx % BATCH_SIZE == 0:
                conn.commit()
                logger.info("  Progress: %d/%d symbols, %d rows", idx, len(symbols), total_rows)
        conn.commit()

        logger.info("AlphaStock Financial ETL done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(symbols) - errors, "errors": errors}
    finally:
        cur.close()
        conn.close()


def refresh_incremental() -> dict:
    """Incremental: only fetch symbols missing recent financial data."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute(
            """SELECT DISTINCT fs.symbol
               FROM financial_statements fs
               WHERE fs.period_end >= %s
                 AND fs.statement_type = 'BS'""",
            (date.today() - timedelta(days=90),),
        )
        recent_symbols = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        all_symbols = [r[0] for r in cur.fetchall()]
        stale_symbols = [s for s in all_symbols if s not in recent_symbols and s not in EXCLUDED_SYMBOLS]
        logger.info("AlphaStock Financial ETL incremental: %d stale symbols", len(stale_symbols))

        if not stale_symbols:
            return {"rows": 0, "symbols": 0, "note": "all symbols up to date"}

        total_rows = 0
        errors = 0
        for idx, sym in enumerate(stale_symbols):
            sp_name = f"sp_inc_{idx}"
            cur.execute(f"SAVEPOINT {sp_name}")
            try:
                result = fetch_and_store_financials(sym, cur)
                total_rows += result["rows"]
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
            except Exception as e:
                cur.execute(f"ROLLBACK TO SAVEPOINT {sp_name}")
                cur.execute(f"RELEASE SAVEPOINT {sp_name}")
                logger.warning("Failed for %s: %s", sym, e)
                errors += 1
            if idx > 0 and idx % BATCH_SIZE == 0:
                conn.commit()
        conn.commit()

        logger.info("AlphaStock Financial ETL incremental done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(stale_symbols) - errors, "errors": errors}
    finally:
        cur.close()
        conn.close()
