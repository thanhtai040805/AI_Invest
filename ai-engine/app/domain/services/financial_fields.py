"""Exact statement-label mapping shared by ratio ETL and financial consumers."""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from functools import lru_cache
from typing import Any, Iterable

logger = logging.getLogger(__name__)


@lru_cache(maxsize=8192)
def financial_label(key: str) -> str:
    text = unicodedata.normalize("NFD", str(key).lower().replace("đ", "d"))
    text = "".join(c for c in text if unicodedata.category(c) != "Mn")
    # Formula suffixes are labels, not different concepts; EPS/BVPS parentheses remain.
    text = re.sub(r"\((?:\d+|[ivxlcdm]+)(?:\s*[=+*/-]\s*(?:\d+|[ivxlcdm]+))+\)", "", text)
    text = re.sub(r"[^a-z0-9]+", " ", text).strip()
    if text in {"p e", "p b", "d e"}:
        return text
    text = re.sub(r"^(?:(?:\d+|[ivxlcdm]+|[abcd])\s+)+", "", text)
    text = re.sub(r"(?:\s+\d+){2,}$", "", text)
    # Slugged bank formulas have no parentheses left.
    return re.sub(r"\s+(?:xi xii|xiii xiv)$", "", text)


def financial_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def extract_financial_value(data: dict | None, aliases: Iterable[str]) -> float | None:
    """Aliases are ordered semantic fallbacks; dictionary order never decides a value.

    Numbered statement lines outrank convenience aliases that old producers overwrote.
    Conflicting equally authoritative lines remain unavailable instead of picking one.
    """
    if not isinstance(data, dict):
        return None
    by_label: dict[str, list[tuple[int, str, float]]] = {}
    for key, raw in data.items():
        value = financial_number(raw)
        if value is None:
            continue
        key = str(key)
        label = financial_label(key)
        heading = re.match(r"^(?:\d+(?:[._]\d+)*|[IVXLCDM]+|[ABCD])(?:[._\s-]+)", key, re.I)
        rank = 0 if heading else 1
        if key == "18_loi_nhuan_sau_thue":
            rank = 2  # Legacy CafeF shortcut could have been overwritten by parent-only NI.
        # D/B denotes total equity; I can be a subheading under that total.
        if label == "von chu so huu" and re.match(r"^[DB](?:[._\s-]+)", key, re.I):
            rank = -1
        by_label.setdefault(label, []).append((rank, key, value))
    for label in dict.fromkeys(financial_label(k) for k in aliases):
        matches = by_label.get(label, [])
        if not matches:
            continue
        best_rank = min(item[0] for item in matches)
        best = sorted(item for item in matches if item[0] == best_rank)
        first = best[0][2]
        if any(not math.isclose(first, item[2], rel_tol=1e-6, abs_tol=1e-9) for item in best[1:]):
            logger.warning("FINANCIAL_MAPPING_CONFLICT label=%s candidates=%s", label, [(k, v) for _, k, v in best])
            return None
        return first
    return None


REVENUE_KEYS = [
    "doanh thu thuần về bán hàng và cung cấp dịch vụ", "doanh thu thuần", "net_sales",
    "thu nhập lãi thuần", "doanh thu phí bảo hiểm thuần", "doanh thu thuần hoạt động kinh doanh bảo hiểm",
    "cộng doanh thu hoạt động (01->11)", "doanh thu hoạt động", "doanh thu bán hàng và cung cấp dịch vụ",
]
COGS_KEYS = ["giá vốn hàng bán", "cost_of_sales"]
NI_KEYS = [
    "lợi nhuận sau thuế thu nhập doanh nghiệp", "lợi nhuận kế toán sau thuế TNDN", "lợi nhuận kế toán sau thuế",
    "lợi nhuận sau thuế (XI-XII)", "lợi nhuận sau thuế", "net_income",
]
ASSET_KEYS = ["tổng cộng tài sản", "tổng tài sản", "total_assets"]
LIAB_KEYS = ["tổng nợ phải trả", "nợ phải trả", "total_liabilities", "liabilities"]
EQUITY_KEYS = ["vốn chủ sở hữu", "vốn và các quỹ", "total_equity", "equity"]
CFO_KEYS = [
    "lưu chuyển tiền thuần từ hoạt động kinh doanh", "lưu chuyển tiền tệ ròng từ các hoạt động sản xuất kinh doanh",
    "lưu chuyển thuần từ hoạt động kinh doanh", "lưu chuyển tiền từ hoạt động kinh doanh",
    "net_cash_inflows_outflows_from_operating_activities",
]
CAPEX_KEYS = [
    "tiền chi để mua sắm, xây dựng TSCĐ và các tài sản dài hạn khác",
    "tiền chi để mua sắm, xây dựng tài sản cố định và các tài sản dài hạn khác",
    "tiền chi mua sắm, xây dựng TSCĐ", "tiền chi để mua sắm, xây dựng TSCĐ",
    "tiền mua tài sản cố định", "mua sắm tài sản cố định", "mua sắm TSCĐ", "capital_expenditures",
]
PE_KEYS = ["chỉ số giá thị trường trên thu nhập (P/E)", "chỉ số P/E", "P/E", "pe"]
PB_KEYS = ["chỉ số giá thị trường trên giá trị sổ sách (P/B)", "chỉ số P/B", "P/B", "pb"]
ROE_KEYS = ["ROE", "tỷ suất lợi nhuận trên vốn chủ sở hữu"]
ROA_KEYS = ["ROA", "tỷ suất sinh lợi trên tổng tài sản"]
DE_KEYS = ["nợ trên vốn", "debt/equity", "D/E", "nợ vay trên vốn", "debt_equity"]
CR_KEYS = ["khả năng thanh toán hiện hành", "thanh toán hiện hành", "current_ratio"]
CA_KEYS = ["tài sản ngắn hạn", "current_assets"]
CL_KEYS = ["nợ ngắn hạn", "current_liabilities"]
CASH_KEYS = ["tiền và các khoản tương đương tiền", "tiền và tương đương tiền", "cash_and_cash_equivalents"]
SHORT_INVESTMENT_KEYS = ["đầu tư tài chính ngắn hạn", "short_term_investments"]
EBITDA_KEYS = ["EBITDA", "lợi nhuận trước thuế, khấu hao và lãi vay"]
EVEBITDA_KEYS = ["giá trị doanh nghiệp trên lợi nhuận trước thuế, khấu hao và lãi vay (EV/EBITDA)", "EV/EBITDA"]
GM_KEYS = ["gross_margin", "tỷ suất lợi nhuận gộp biên", "tỷ suất lợi nhuận gộp", "biên lợi nhuận gộp"]
GROSS_PROFIT_KEYS = ["lợi nhuận gộp về bán hàng và cung cấp dịch vụ", "lợi nhuận gộp của hoạt động kinh doanh", "gross_profit", "lợi nhuận gộp"]
NM_KEYS = ["net_margin", "tỷ suất sinh lợi trên doanh thu thuần", "sinh lợi trên doanh thu", "biên lợi nhuận ròng"]
EPS_KEYS = ["thu nhập trên mỗi cổ phần của 4 quý gần nhất (EPS)", "eps"]
BVPS_KEYS = ["giá trị sổ sách của cổ phiếu (BVPS)", "bvps"]
BORROWING_KEYS = ["vay và nợ thuê tài chính", "vay nợ tài chính", "interest_bearing_debt"]
SHORT_BORROWING_KEYS = ["vay và nợ thuê tài chính ngắn hạn", "vay ngắn hạn"]
LONG_BORROWING_KEYS = ["vay và nợ thuê tài chính dài hạn", "vay dài hạn"]


def interest_bearing_debt(data: dict) -> float | None:
    total = extract_financial_value(data, BORROWING_KEYS)
    if total is not None:
        return total
    short = extract_financial_value(data, SHORT_BORROWING_KEYS)
    long = extract_financial_value(data, LONG_BORROWING_KEYS)
    # Missing one maturity is not evidence that its balance is zero.
    return short + long if short is not None and long is not None else None


def apply_financial_overrides(data: dict, overrides: dict) -> dict:
    """Keep existing verified overrides consistent across aliases of the same line."""
    if not overrides:
        return data
    def identity(key):
        label = financial_label(key)
        if label in {financial_label(k) for k in ASSET_KEYS}:
            return "total_assets"
        if label in {financial_label(k) for k in LIAB_KEYS}:
            return "total_liabilities"
        if label == "von chu so huu":
            return "equity_subheading" if re.match(r"^I(?:[._\s-]+)", key, re.I) else "equity_total"
        return label
    values = {}
    for key, value in overrides.items():
        name = identity(key)
        if name in values and values[name] != value:
            raise ValueError(f"Conflicting financial overrides for {name}")
        values[name] = value
    result = {k: values.get(identity(k), v) for k, v in data.items()}
    result.update(overrides)
    return result
