"""Financial Statements ETL — CafeF REST API → financial_statements + financial_ratios tables.

High-performance, async-capable, direct JSON API integration replacing AlphaStock.
No Chromium or headless browser required.
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
API_BASE = "https://apiweb.cafef.vn/api"

EXCLUDED_SYMBOLS = {"KSS", "PCN", "TCD", "HHR", "CTC", "BCG", "B82", "VMD"}

SPECIAL_FY = {"SBT", "LSS"}

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://cafef.vn/",
    "Origin": "https://cafef.vn",
}

_CLIENT = httpx.Client(headers=_HEADERS, timeout=30)

# Reverse-engineered CafeF mapping for manufacturing/commercial KQKD:
# In <= 2025, raw data uses old codes shifted by 1 relative to the 2026 template.
KQKD_NEW_TO_OLD_CODE_MAP = {
    "22": "21",  # Doanh thu hoạt động tài chính
    "23": "22",  # Chi phí tài chính
    "24": "23",  # Trong đó: Chi phí lãi vay
    "27": "24",  # Lãi/lỗ trong công ty liên doanh, liên kết
}


def _clean_nan(data: dict[str, Any]) -> dict[str, Any]:
    return {
        k: (None if isinstance(v, float) and (math.isnan(v) or math.isinf(v)) else v)
        for k, v in data.items()
    }


def _period_label_to_date(label: str) -> Optional[date]:
    """Convert 'Q2-2026' or '2026-Q2' → date(2026, 6, 30), '2025' → date(2025, 12, 31)."""
    if not label:
        return None
    # 'Q2-2026' format
    m = re.match(r"Q([1-4])-(\d{4})", label)
    if m:
        q, year = int(m.group(1)), int(m.group(2))
        return {1: date(year, 3, 31), 2: date(year, 6, 30), 3: date(year, 9, 30), 4: date(year, 12, 31)}[q]
    # '2026-Q2' format
    m = re.match(r"(\d{4})-Q([1-4])", label)
    if m:
        year, q = int(m.group(1)), int(m.group(2))
        return {1: date(year, 3, 31), 2: date(year, 6, 30), 3: date(year, 9, 30), 4: date(year, 12, 31)}[q]
    # '2025' format
    m = re.match(r"(\d{4})$", label)
    if m:
        return date(int(m.group(1)), 12, 31)
    return None


def _slugify(text: str) -> str:
    """Normalize Vietnamese text to slugified identifier for resilient key matching."""
    t = text.lower()
    t = re.sub(r"[àáạảãâầấậẩẫăằắặẳẵ]", "a", t)
    t = re.sub(r"[èéẹẻẽêềếệểễ]", "e", t)
    t = re.sub(r"[ìíịỉĩ]", "i", t)
    t = re.sub(r"[òóọỏõôồốộổỗơờớợởỡ]", "o", t)
    t = re.sub(r"[ùúụủũưừứựửữ]", "u", t)
    t = re.sub(r"[ỳýỵỷỹ]", "y", t)
    t = re.sub(r"[đ]", "d", t)
    t = re.sub(r"[^a-z0-9]+", "_", t)
    return t.strip("_")


def _upsert(cur, symbol: str, period_end: date, stmt_type: str, freq: str, data: dict):
    published_date = period_end + timedelta(days=45 if freq == "quarterly" else 90)
    cur.execute(
        """INSERT INTO financial_statements
           (symbol, period_end, statement_type, frequency, data, source, published_date)
           VALUES (%s, %s, %s, %s, %s, %s, %s)
           ON CONFLICT (symbol, period_end, statement_type, frequency)
           DO UPDATE SET data = EXCLUDED.data, source = EXCLUDED.source, fetched_at = NOW(), published_date = EXCLUDED.published_date""",
        (symbol, period_end, stmt_type, freq, Json(_clean_nan(data)), "cafef", published_date),
    )


def _fetch_cafef_endpoint(url: str, params: dict) -> Optional[dict]:
    try:
        r = _CLIENT.get(url, params=params)
        r.raise_for_status()
        res = r.json()
        if res.get("isSuccess"):
            return res.get("value")
        return None
    except Exception as e:
        logger.warning("CafeF API request failed (%s): %s", url, e)
        return None


def _add_key_variants(target: dict[str, Any], name: str, val: Any) -> None:
    if not name:
        return

    def _set(k: str, v: Any):
        if v is not None or k not in target:
            target[k] = v

    _set(name, val)
    _set(name.lower(), val)
    _set(_slugify(name), val)

    # Strip prefixes like "1. ", "10. ", "A. ", "IV. ", "- ", "D "
    stripped = re.sub(r"^(\d+|[IVXLCDMivxlcdm]+|[A-Za-z])[\.\s]\s*|^-\s*", "", name).strip()
    if stripped and stripped != name:
        _set(stripped, val)
        _set(stripped.lower(), val)
        _set(_slugify(stripped), val)


def _parse_cdkt(val: dict, is_bank: bool) -> dict[str, dict[str, Any]]:
    """Parse Balance Sheet (CDKT) periods."""
    t_map = {}
    for t in val.get("templace", []):
        for d in t.get("data", []):
            code_str = str(d.get("code"))
            t_map[code_str] = d.get("name", "")

    periods: dict[str, dict[str, Any]] = {}
    for sec in val.get("data", []):
        for p in sec.get("data", []):
            t_label = p.get("time")
            if not t_label:
                continue
            if t_label not in periods:
                periods[t_label] = {}
            for item in p.get("data", []):
                code = str(item.get("code"))
                val_num = item.get("value")
                name = t_map.get(code)
                periods[t_label][code] = val_num
                if name:
                    _add_key_variants(periods[t_label], name, val_num)
                    # Add standard dotted aliases if missing (e.g. 'D VỐN CHỦ SỞ HỮU' -> 'D. VỐN CHỦ SỞ HỮU')
                    if name.startswith("D VỐN") or name.startswith("D. VỐN"):
                        periods[t_label]["D. VỐN CHỦ SỞ HỮU"] = val_num
                        periods[t_label]["D VỐN CHỦ SỞ HỮU"] = val_num
                        periods[t_label]["vốn chủ sở hữu"] = val_num
                        periods[t_label]["von_chu_so_huu"] = val_num
                    elif name.startswith("C NỢ") or name.startswith("C. NỢ"):
                        periods[t_label]["C. NỢ PHẢI TRẢ"] = val_num
                        periods[t_label]["C NỢ PHẢI TRẢ"] = val_num
                        periods[t_label]["nợ phải trả"] = val_num
                        periods[t_label]["no_phai_tra"] = val_num
                    elif "tổng cộng tài sản" in name.lower() or "tổng tài sản" in name.lower():
                        periods[t_label]["TỔNG CỘNG TÀI SẢN"] = val_num
                        periods[t_label]["tổng cộng tài sản"] = val_num
                        periods[t_label]["tong_cong_tai_san"] = val_num
    return periods


def _parse_kqkd(val: dict, is_bank: bool) -> dict[str, dict[str, Any]]:
    """Parse Income Statement (KQKD) periods with reverse-engineered shift mapping."""
    t_map = {}
    for t in val.get("templace", []):
        code_str = str(t.get("code") or t.get("number"))
        t_map[code_str] = t.get("name", "")

    periods: dict[str, dict[str, Any]] = {}
    for p in val.get("data", []):
        t_label = p.get("time")
        if not t_label:
            continue
        year = p.get("year") or 2026
        period_data = p.get("data", [])
        if t_label not in periods:
            periods[t_label] = {}

        for code_str, name in t_map.items():
            val_num = None
            if is_bank:
                it = next((x for x in period_data if str(x.get("code")) == code_str), None)
                val_num = it.get("value") if it else None
            else:
                if year <= 2025:
                    if code_str == "21":
                        val_num = None
                    elif code_str in KQKD_NEW_TO_OLD_CODE_MAP:
                        old_code = KQKD_NEW_TO_OLD_CODE_MAP[code_str]
                        it = next((x for x in period_data if str(x.get("code")) == old_code), None)
                        val_num = it.get("value") if it else None
                    else:
                        it = next((x for x in period_data if str(x.get("code")) == code_str), None)
                        val_num = it.get("value") if it else None
                else:
                    it = next((x for x in period_data if str(x.get("code")) == code_str), None)
                    val_num = it.get("value") if it else None

            periods[t_label][code_str] = val_num
            if name:
                _add_key_variants(periods[t_label], name, val_num)
                # Normalize common aliases
                if "chi phí lãi vay" in name.lower():
                    periods[t_label]["Trong đó: Chi phí đi vay"] = val_num
                    periods[t_label]["trong_do_chi_phi_lai_vay"] = val_num
                    periods[t_label]["chi phí lãi vay"] = val_num
                elif "doanh thu thuần" in name.lower():
                    periods[t_label]["doanh thu thuần"] = val_num
                    periods[t_label]["3_doanh_thu_thuan"] = val_num
                    periods[t_label]["3_doanh_thu_thuan_ve_ban_hang_va_cung_cap_dich_vu"] = val_num
                elif "giá vốn hàng bán" in name.lower():
                    periods[t_label]["giá vốn hàng bán"] = val_num
                    periods[t_label]["4_gia_von_hang_ban"] = val_num
                elif "lợi nhuận sau thuế thu nhập doanh nghiệp" in name.lower() or "lợi nhuận sau thuế" in name.lower():
                    periods[t_label]["lợi nhuận sau thuế"] = val_num
                    periods[t_label]["18_loi_nhuan_sau_thue"] = val_num
    return periods


def _parse_lctt(val: dict, is_bank: bool) -> dict[str, dict[str, Any]]:
    """Parse Cash Flow Statement (LCTT) periods."""
    t_map = {}
    for t in val.get("templace", []):
        for d in t.get("data", []):
            code_str = str(d.get("code"))
            t_map[code_str] = d.get("name", "")

    periods: dict[str, dict[str, Any]] = {}
    for sec in val.get("data", []):
        for p in sec.get("data", []):
            t_label = p.get("time")
            if not t_label:
                continue
            if t_label not in periods:
                periods[t_label] = {}
            for item in p.get("data", []):
                code = str(item.get("code"))
                val_num = item.get("value")
                name = t_map.get(code)
                periods[t_label][code] = val_num
                if name:
                    _add_key_variants(periods[t_label], name, val_num)
                    if "lưu chuyển tiền thuần từ hoạt động kinh doanh" in name.lower():
                        periods[t_label]["Lưu chuyển tiền thuần từ hoạt động kinh doanh"] = val_num
                        periods[t_label]["luu_chuyen_tien_thuan_tu_hoat_dong_kinh_doanh"] = val_num
                        periods[t_label]["tiền thuần từ hđkd"] = val_num
    return periods


def _parse_ratios(val: dict) -> dict[str, dict[str, Any]]:
    """Parse Financial Indicators / Ratios from CafeF."""
    t_map = {str(t.get("code")): t.get("name", "") for t in val.get("templace", [])}
    periods: dict[str, dict[str, Any]] = {}
    for p in val.get("data", []):
        t_label = p.get("time")
        if not t_label:
            continue
        if t_label not in periods:
            periods[t_label] = {}
        for item in p.get("data", []):
            code = str(item.get("code"))
            val_num = item.get("value")
            name = t_map.get(code)
            periods[t_label][code] = val_num
            if name:
                periods[t_label][name] = val_num
                periods[t_label][name.lower()] = val_num
                periods[t_label][_slugify(name)] = val_num
    return periods


def fetch_and_store_financials(symbol: str, cur, max_quarters: int = 80) -> dict[str, Any]:
    """Fetch all financial statements for one symbol from CafeF and upsert into DB."""
    total_upserted = 0
    clean_sym = symbol.strip().upper()

    # Determine is_bank
    cur.execute("SELECT industry, sector FROM stocks WHERE symbol = %s", (clean_sym,))
    stock_row = cur.fetchone()
    is_bank = False
    if stock_row:
        ind, sec = (stock_row[0] or "").lower(), (stock_row[1] or "").lower()
        if "ngân hàng" in ind or "bank" in ind or "ngân hàng" in sec or "financial" in sec:
            is_bank = True

    # 1. Fetch Quarterly data
    cdkt_q = _fetch_cafef_endpoint(f"{API_BASE}/v2/BCTC/GetReportCDKT", {"symbol": clean_sym, "pageIndex": 1, "pageSize": max_quarters, "reportType": "ALL", "TypeTime": "QUY"})
    kqkd_q = _fetch_cafef_endpoint(f"{API_BASE}/v1/BCTC/GetReportDetail", {"symbol": clean_sym, "pageIndex": 1, "pageSize": max_quarters, "reportType": "KQKD", "TypeTime": "QUY"})
    lctt_q = _fetch_cafef_endpoint(f"{API_BASE}/v1/BCTC/GetReportLCTT", {"symbol": clean_sym, "pageIndex": 1, "pageSize": max_quarters, "reportType": "ALL", "TypeTime": "QUY"})
    ratios_q = _fetch_cafef_endpoint(f"{API_BASE}/v2/BCTC/FinancialIndicators", {"symbol": clean_sym, "pageIndex": 1, "pageSize": max_quarters})

    # 2. Fetch Annual data (TypeTime=NAM)
    cdkt_a = _fetch_cafef_endpoint(f"{API_BASE}/v2/BCTC/GetReportCDKT", {"symbol": clean_sym, "pageIndex": 1, "pageSize": 20, "reportType": "ALL", "TypeTime": "NAM"})
    kqkd_a = _fetch_cafef_endpoint(f"{API_BASE}/v1/BCTC/GetReportDetail", {"symbol": clean_sym, "pageIndex": 1, "pageSize": 20, "reportType": "KQKD", "TypeTime": "NAM"})
    lctt_a = _fetch_cafef_endpoint(f"{API_BASE}/v1/BCTC/GetReportLCTT", {"symbol": clean_sym, "pageIndex": 1, "pageSize": 20, "reportType": "ALL", "TypeTime": "NAM"})

    runs = [
        ("quarterly", cdkt_q, kqkd_q, lctt_q, ratios_q),
        ("yearly", cdkt_a, kqkd_a, lctt_a, None),
    ]

    for freq, raw_bs, raw_is, raw_cf, raw_ratios in runs:
        parsed_bs = _parse_cdkt(raw_bs, is_bank) if raw_bs else {}
        parsed_is = _parse_kqkd(raw_is, is_bank) if raw_is else {}
        parsed_cf = _parse_lctt(raw_cf, is_bank) if raw_cf else {}
        parsed_r = _parse_ratios(raw_ratios) if raw_ratios else {}

        all_period_labels = set(parsed_bs.keys()) | set(parsed_is.keys()) | set(parsed_cf.keys()) | set(parsed_r.keys())
        for pl in all_period_labels:
            pe = _period_label_to_date(pl)
            if not pe:
                continue

            if pl in parsed_bs and parsed_bs[pl]:
                _upsert(cur, clean_sym, pe, "BS", freq, parsed_bs[pl])
                total_upserted += 1
            if pl in parsed_is and parsed_is[pl]:
                _upsert(cur, clean_sym, pe, "IS", freq, parsed_is[pl])
                total_upserted += 1
            if pl in parsed_cf and parsed_cf[pl]:
                _upsert(cur, clean_sym, pe, "CF", freq, parsed_cf[pl])
                total_upserted += 1
            if pl in parsed_r and parsed_r[pl]:
                _upsert(cur, clean_sym, pe, "ratios", freq, parsed_r[pl])
                total_upserted += 1

    try:
        _store_derived_ratios(clean_sym, cur)
    except Exception as e:
        logger.warning("Derived ratios failed for %s: %s", clean_sym, e)

    return {"symbol": clean_sym, "rows": total_upserted}


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

_COGS_KEYS = ["giá vốn hàng bán", "gia von hang ban", "giá_vốn", "4_giá_vốn_hàng_bán"]

_NI_KEYS = [
    "18_lợi_nhuận_sau_thuế",
    "lợi nhuận sau thuế thu nhập doanh nghiệp",
    "lợi nhuận sau thuế", "loi nhuan sau thue", "lợi_nhuận_sau_thuế",
    "29_lợi_nhuận_sau_thuế", "xiii_lợi_nhuận_sau_thuế",
]

_ASSET_KEYS = [
    "tổng cộng tài sản", "tong cong tai san", "tổng_cộng_tài_sản",
    "tổng cộng tài sản (270=100+200)", "tổng tài sản",
]

_LIAB_KEYS = ["tổng nợ phải trả", "tong no phai tra", "c_nợ_phải_trả", "C. NỢ PHẢI TRẢ", "nợ phải trả", "C NỢ PHẢI TRẢ"]

_EQUITY_KEYS = [
    "vốn chủ sở hữu", "von chu so huu", "i_vốn_chủ_sở_hữu",
    "vốn chủ sở hữu (400=410+430)", "b_vốn_chủ_sở_hữu", "d vốn chủ sở hữu", "D. VỐN CHỦ SỞ HỮU",
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
    "mua sắm tscđ",
]

_PE_KEYS = ["p/e", "chỉ số p/e", "chỉ_số_giá_thị_trường_trên_thu_nhập_p_e", "pe"]
_PB_KEYS = ["p/b", "chỉ số p/b", "chỉ_số_giá_thị_trường_trên_giá_trị_sổ_sách_p_b", "pb"]
_ROE_KEYS = ["roe", "tỷ suất lợi nhuận trên vốn chủ sở hữu"]
_ROA_KEYS = ["roa", "tỷ suất sinh lợi trên tổng tài sản"]

_DE_KEYS = ["nợ trên vốn", "debt/equity", "d/e", "nợ vay trên vốn", "debt_equity"]
_CR_KEYS = ["thanh toán hiện hành", "current ratio", "current_ratio"]
_CASH_KEYS = ["tiền và các khoản tương đương tiền", "i_tiền_và_các_khoản", "tiền và tương đương tiền", "1. tiền"]
_EBITDA_KEYS = ["ebitda", "ebit", "lợi nhuận thuần từ hoạt động kinh doanh"]
_EVEBITDA_KEYS = ["ev/ebitda", "giá trị doanh nghiệp trên lợi nhuận trước thuế, khấu hao và lãi vay"]
_GM_KEYS = ["lợi nhuận gộp", "gross margin", "gộp biên", "gross_margin", "tỷ suất lợi nhuận gộp biên"]
_NM_KEYS = ["lợi nhuận ròng", "net margin", "sinh lợi trên doanh thu", "net_margin", "tỷ suất sinh lợi trên doanh thu thuần"]


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
            ttm_ni = ni * 4 if abs(ni) < 5e11 else ni
            mcap = vn_pe * ttm_ni

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
            fcf_yield = (fcf * 4) / mcap

        ev_ebitda = _extract_value([rat_data], _EVEBITDA_KEYS)
        if ev_ebitda is None and mcap is not None:
            interest_debt = _extract_value([bs_data], [
                "vay_và_nợ_thuê_tài_chính", "Vay và nợ thuê tài chính", "vay_nợ_tài_chính"
            ])
            debt_for_ev = interest_debt if interest_debt is not None else liab
            if debt_for_ev is not None:
                cash = _extract_value([bs_data], _CASH_KEYS) or 0
                st_inv = _extract_value([bs_data], ["đầu_tư_tài_chính_ngắn_hạn", "Đầu tư tài chính ngắn hạn"]) or 0
                ev = mcap + debt_for_ev - (cash + st_inv)
                ebitda = _extract_value([inc_data], _EBITDA_KEYS)
                if ebitda is not None and ebitda != 0:
                    ttm_ebitda = ebitda * 4 if abs(ebitda) < 5e11 else ebitda
                    ev_ebitda = ev / ttm_ebitda

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


def refresh_all(limit: Optional[int] = None) -> dict:
    """Full refresh: refetch all symbols from CafeF."""
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    try:
        cur.execute("SELECT symbol FROM stocks WHERE exchange IN ('HOSE','HSX') ORDER BY symbol")
        symbols = [r[0] for r in cur.fetchall()]
        symbols = [s for s in symbols if s not in EXCLUDED_SYMBOLS]
        if limit:
            symbols = symbols[:limit]

        logger.info("CafeF Financial ETL: %d symbols (%d excluded)", len(symbols), len(EXCLUDED_SYMBOLS))

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

        logger.info("CafeF Financial ETL done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(symbols) - errors, "errors": errors}
    finally:
        cur.close()
        conn.close()


def refresh_incremental() -> dict:
    """Incremental: only fetch symbols missing recent financial data (within last 90 days)."""
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
        logger.info("CafeF Financial ETL incremental: %d stale symbols", len(stale_symbols))

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

        logger.info("CafeF Financial ETL incremental done: %d rows, %d errors", total_rows, errors)
        return {"rows": total_rows, "symbols": len(stale_symbols) - errors, "errors": errors}
    finally:
        cur.close()
        conn.close()
