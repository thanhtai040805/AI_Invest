from __future__ import annotations

import re
import unicodedata
from typing import Any

from sag_api.enums import FactType


def fold_text(value: str | None) -> str:
    """Normalize and remove Vietnamese diacritics for robust matching."""
    if not value:
        return ""
    normalized = unicodedata.normalize("NFKD", str(value).casefold())
    stripped = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return stripped.replace("đ", "d").strip()


def parse_vn_number(raw: str) -> float | None:
    """Parse Vietnamese number formatting (dots as thousands separators, commas as decimals, or parenthesis as negative)."""
    val = raw.strip()
    if not val or val == "-":
        return None
    is_negative = False
    if val.startswith("(") and val.endswith(")"):
        is_negative = True
        val = val[1:-1].strip()
    elif val.startswith("-"):
        is_negative = True
        val = val[1:].strip()

    # Decompose multi-line OCR glued dot-separated numbers (e.g. 2.863.1252.863.1251.204.866)
    if "." in val and re.search(r"(\.\d{3})(\d{1,3}\.)", val):
        cleaned = val
        while re.search(r"(\.\d{3})(\d{1,3}\.)", cleaned):
            cleaned = re.sub(r"(\.\d{3})(\d{1,3}\.)", r"\1 \2", cleaned)
        cleaned = re.sub(r"(\.\d{3})(\d{1,3})$", r"\1 \2", cleaned)
        sub_nums = [parse_vn_number(token) for token in cleaned.split()]
        valid_subs = [s for s in sub_nums if s is not None and abs(s) <= 1e16]
        if valid_subs:
            total = sum(valid_subs)
            return -total if is_negative else total

    if "," in val and "." in val:
        val = val.replace(".", "").replace(",", ".")
    elif val.count(",") == 1:
        val = val.replace(",", ".")
    elif val.count(",") > 1:
        if not all(len(part) == 3 for part in val.split(",")[1:]):
            return None
        val = val.replace(",", "")
    elif val.count(".") > 1 or (val.count(".") == 1 and len(val.rsplit(".", 1)[1]) == 3):
        val = val.replace(".", "")

    try:
        res = float(val)
        if abs(res) > 1e16:
            return None
        return -res if is_negative else res
    except ValueError:
        return None


def parse_vn_percentage(raw: str) -> float | None:
    """Parse a percentage cell without treating decimal dots as thousands separators."""
    val = raw.strip().replace("%", "").replace(" ", "")
    if not val or val == "-":
        return None
    if "," in val and "." in val:
        decimal = max(val.rfind(","), val.rfind("."))
        val = re.sub(r"[.,]", "", val[:decimal]) + "." + re.sub(r"[.,]", "", val[decimal + 1 :])
    else:
        val = val.replace(",", ".")
    try:
        result = float(val)
    except ValueError:
        return None
    return result if 0.0 <= result <= 100.0 else None


_UNIT_SCALE_PATTERNS: tuple[tuple[float, tuple[str, ...]], ...] = (
    (1_000_000.0, ("trieu vnd", "trieu dong", "million vnd", "million dong")),
    (1_000_000_000.0, ("ty vnd", "ty dong", "billion vnd", "billion dong")),
    (1_000.0, ("nghin vnd", "nghin dong", "ngan vnd", "ngan dong", "thousand vnd", "thousand dong")),
)


def _has_unit_pattern(text: str, pattern: str) -> bool:
    if pattern in {"ty vnd", "ty dong"}:
        text = re.sub(r"\bcong ty\b", "", text)
    return bool(
        re.search(rf"(?<!\w){re.escape(pattern)}(?!\w)", text)
        or re.search(rf"(?<=\d){re.escape(pattern)}(?!\w)", text)
    )


def detect_unit_scale_context(text: str) -> tuple[float, bool]:
    """Return ``(scale, found)`` without letting narrative text set a table's unit.

    Financial reports often explain results in billion VND before presenting a
    statement in VND or thousand VND.  Only unit-labelled lines (or compact
    table headers) are allowed to override the default; this keeps prose from
    changing the scale of a later table.
    """
    folded_text = fold_text(text)
    if "\n" not in text:
        matches = [
            (folded_text.rfind(pattern), scale)
            for scale, patterns in _UNIT_SCALE_PATTERNS
            for pattern in patterns
            if _has_unit_pattern(folded_text, pattern)
        ]
        if matches:
            return max(matches)[1], True
        if "vnd" in folded_text or "dong" in folded_text:
            return 1.0, True

    lines = [fold_text(line) for line in text.splitlines() if fold_text(line)]
    markers = ("don vi", "dvt", "unit", "currency")
    explicit_candidates = [
        line for line in lines
        if any(marker in line for marker in markers)
        and ("vnd" in line or "dong" in line)
    ]
    short_unit_candidates = [
        line for line in lines
        if len(line) <= 40
        and any(
            _has_unit_pattern(line, pattern)
            for _, patterns in _UNIT_SCALE_PATTERNS
            for pattern in patterns
        )
    ]
    candidates = explicit_candidates + short_unit_candidates
    if not candidates:
        candidates = [
            line for line in lines
            if line.startswith("|") and ("vnd" in line or "dong" in line)
        ]
    if not candidates:
        return 1.0, False

    for line in reversed(candidates):
        for scale, patterns in _UNIT_SCALE_PATTERNS:
            if any(_has_unit_pattern(line, pattern) for pattern in patterns):
                return scale, True
        if "vnd" in line or "dong" in line:
            return 1.0, True

    return 1.0, False


def detect_unit_scale(text: str) -> float:
    """Detect the monetary scale factor from an explicitly labelled context."""
    return detect_unit_scale_context(text)[0]


def classify_temporal_column(header: str) -> str | None:
    """Classify a table header as representing current or previous reporting period.

    Applicable universally across Vietnamese financial statements:
    - Standard period keywords: 'cuối kỳ', 'cuối năm', 'kỳ này', 'năm nay' vs 'đầu năm', 'đầu kỳ', 'kỳ trước', 'năm trước'
    - Standard fiscal quarter/annual period-ends: 31/12, 30/06, 30/09, 31/03
    - Standard period beginnings: 01/01
    """
    hf = fold_text(header)
    if not hf:
        return None
    if any(tok in hf for tok in ("cuoi ky", "cuoi nam", "so cuoi ky", "so cuoi nam", "so cuoi", "ky nay", "nam nay", "current")):
        return "CURRENT_PERIOD"
    if any(tok in hf for tok in ("dau ky", "dau nam", "so dau ky", "so dau nam", "so dau", "ky truoc", "nam truoc", "previous")):
        return "PREVIOUS_PERIOD"
    # Ending dates for all 4 quarters & annual periods: 31/12, 30/06, 30/09, 31/03 (supports / . or thang)
    if re.search(r"\b(3[01]|30)\s*([/.]|thang)\s*(12|0?6|0?9|0?3)\b", hf):
        return "CURRENT_PERIOD"
    # Beginning dates of period: 01/01
    if re.search(r"\b0?1\s*([/.]|thang)\s*0?1\b", hf):
        return "PREVIOUS_PERIOD"
    return None


# Standard Accounting Codes under Circular 200/2014/TT-BTC & Circular 99/2025/TT-BTC
# Format: (code, required_terms, FactType, semantic_key)
CORPORATE_ACCOUNTING_CODES: dict[str, tuple[FactType, str]] = {
    # Balance Sheet (Bảng cân đối kế toán)
    "110": (FactType.OTHER, "cash_and_equivalents"),
    "130": (FactType.RECEIVABLE_BALANCE, "total_receivables"),
    "210": (FactType.RECEIVABLE_BALANCE, "long_term_receivables"),
    "270": (FactType.OTHER, "total_assets"),
    "300": (FactType.PAYABLE_BALANCE, "total_payables"),
    "310": (FactType.PAYABLE_BALANCE, "short_term_payables"),
    "330": (FactType.PAYABLE_BALANCE, "long_term_payables"),
    "400": (FactType.EQUITY, "total_equity"),
    "410": (FactType.EQUITY, "total_equity"),
    "411": (FactType.EQUITY, "contributed_capital"),
    "412": (FactType.EQUITY, "share_premium"),
    "420": (FactType.EQUITY, "other_equity_reserves"),
    "421": (FactType.EQUITY, "retained_earnings"),
    # Income Statement (Báo cáo kết quả kinh doanh)
    "01": (FactType.REVENUE, "revenue"),
    "10": (FactType.REVENUE, "revenue"),
    "11": (FactType.OTHER, "cost_of_goods_sold"),
    "20": (FactType.PROFIT, "gross_profit"),
    "21": (FactType.REVENUE, "financial_income"),
    "22": (FactType.REVENUE, "financial_income"),
    "23": (FactType.OTHER, "borrowing_cost"),
    "50": (FactType.PROFIT, "profit_before_tax"),
    "60": (FactType.PROFIT, "profit"),
}

# Bank Financial Statement Taxonomy (Circular 49/2014/TT-NHNN & Decision 479/2004/QĐ-NHNN)
BANK_ACCOUNTING_TERMS: list[tuple[tuple[str, ...], FactType, str]] = [
    # Match the aggregate before the generic equity label.  Bank statements
    # commonly print this line as "total liabilities and equity".
    (("tong no phai tra va von chu so huu",), FactType.OTHER, "total_resources"),
    (("tong von chu so huu",), FactType.EQUITY, "total_equity"),
    (("von chu so huu", "tong cong"), FactType.EQUITY, "total_equity"),
    (("von chu so huu",), FactType.EQUITY, "total_equity"),
    (("tong cong tai san",), FactType.OTHER, "total_assets"),
    (("tong tai san co",), FactType.OTHER, "total_assets"),
    (("tong no phai tra",), FactType.PAYABLE_BALANCE, "total_payables"),
    (("tien mat, vang bac",), FactType.OTHER, "cash_and_equivalents"),
    (("tien gui tai ngan hang nha nuoc",), FactType.OTHER, "cash_and_equivalents"),
    (("tien mat", "tien gui"), FactType.OTHER, "cash_and_equivalents"),
    (("cho vay khach hang",), FactType.RECEIVABLE_BALANCE, "total_receivables"),
    (("cac khoan phai thu",), FactType.RECEIVABLE_BALANCE, "total_receivables"),
    (("thu nhap lai thuan",), FactType.REVENUE, "revenue"),
    (("thu nhap thuan tu hoat dong",), FactType.REVENUE, "revenue"),
    (("chi phi lai",), FactType.OTHER, "cost_of_goods_sold"),
    (("loi nhuan truoc thue",), FactType.PROFIT, "profit_before_tax"),
    (("loi nhuan sau thue",), FactType.PROFIT, "profit"),
    (("bao lanh",), FactType.OTHER, "bank_commitments"),
]

# General Corporate Semantic Line Item Mapping (when code is omitted or unparsed)
CORPORATE_ACCOUNTING_TERMS: list[tuple[tuple[str, ...], FactType, str]] = [
    # A small number of issuers publish an English statement body even when
    # the surrounding document is Vietnamese. Keep these beside the shared
    # corporate taxonomy so the statement parser does not silently drop them.
    (("total assets",), FactType.OTHER, "total_assets"),
    (("total liabilities",), FactType.PAYABLE_BALANCE, "total_payables"),
    (("total equity",), FactType.EQUITY, "total_equity"),
    (("revenue",), FactType.REVENUE, "revenue"),
    (("net revenue",), FactType.REVENUE, "revenue"),
    (("cost of goods sold",), FactType.OTHER, "cost_of_goods_sold"),
    (("gross profit",), FactType.PROFIT, "gross_profit"),
    (("profit before tax",), FactType.PROFIT, "profit_before_tax"),
    (("profit after tax",), FactType.PROFIT, "profit"),
    (("net profit",), FactType.PROFIT, "profit"),
    (("cash and cash equivalents",), FactType.OTHER, "cash_and_equivalents"),
    (("trade receivables",), FactType.RECEIVABLE_BALANCE, "total_receivables"),
    (("trade payables",), FactType.PAYABLE_BALANCE, "total_payables"),
    (("bao lanh",), FactType.GUARANTEE_BALANCE, "guarantee_balance"),
    (("tong cong tai san",), FactType.OTHER, "total_assets"),
    (("tong tai san",), FactType.OTHER, "total_assets"),
    (("tong von chu so huu",), FactType.EQUITY, "total_equity"),
    (("von chu so huu",), FactType.EQUITY, "total_equity"),
    (("tong von chu so huu",), FactType.EQUITY, "equity"),
    (("tien va cac khoan tuong duong tien",), FactType.OTHER, "cash_and_equivalents"),
    (("phai thu ngan han", "tong"), FactType.RECEIVABLE_BALANCE, "total_receivables"),
    (("cac khoan phai thu",), FactType.RECEIVABLE_BALANCE, "total_receivables"),
    (("cac khoan phai thu ngan han",), FactType.RECEIVABLE_BALANCE, "total_receivables"),
    (("tong cong no phai tra",), FactType.PAYABLE_BALANCE, "total_payables"),
    (("no phai tra",), FactType.PAYABLE_BALANCE, "total_payables"),
    (("doanh thu thuan ve ban hang",), FactType.REVENUE, "revenue"),
    (("doanh thu thuan",), FactType.REVENUE, "revenue"),
    (("doanh thu ban hang",), FactType.REVENUE, "revenue"),
    (("gia von hang ban",), FactType.OTHER, "cost_of_goods_sold"),
    (("gia von",), FactType.OTHER, "cost_of_goods_sold"),
    (("doanh thu hoat dong tai chinh",), FactType.REVENUE, "financial_income"),
    (("chi phi tai chinh",), FactType.OTHER, "borrowing_cost"),
    (("chi phi lai vay",), FactType.OTHER, "borrowing_cost"),
    (("loi nhuan sau thue",), FactType.PROFIT, "profit"),
    (("loi nhuan thuan",), FactType.PROFIT, "profit"),
]


def classify_statement_line(
    row_label: str,
    accounting_code: str | None = None,
    is_bank: bool = False,
) -> tuple[FactType, str] | None:
    """Classify a statement table row into a standard FactType and semantic_key."""
    folded = fold_text(row_label)
    # 1. First priority: Check exact standard accounting code (if available and non-bank)
    if accounting_code and not is_bank:
        clean_code = accounting_code.strip()
        if clean_code == "270" and not ("tong" in folded and "tai san" in folded):
            clean_code = ""
        if clean_code in CORPORATE_ACCOUNTING_CODES:
            return CORPORATE_ACCOUNTING_CODES[clean_code]

    # 2. Check sector-specific term dictionary
    if "bao lanh" in folded and "thu phi" in folded:
        return None
    term_dict = BANK_ACCOUNTING_TERMS if is_bank else CORPORATE_ACCOUNTING_TERMS
    for terms, fact_type, semantic_key in term_dict:
        if all(term in folded for term in terms):
            return fact_type, semantic_key

    # Fallback to general terms if bank term didn't match
    if is_bank:
        for terms, fact_type, semantic_key in CORPORATE_ACCOUNTING_TERMS:
            if all(term in folded for term in terms):
                return fact_type, semantic_key

    return None
