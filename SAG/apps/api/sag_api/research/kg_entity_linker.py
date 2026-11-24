"""Engine 1: Zero-Hardcode Document Entity-Linker & Bottom-Up Cross-Sector Roll-up.

Productionized from verified Stage 1 `block2_macro_kg_engine.py`.
- 0 LLM calls; deterministic entity registry built dynamically from Core DB `stocks`.
- Strict Point-in-Time gate: documents with `published_date > as_of_date` are never read.
- Zero hardcoded sector maps, ticker peer maps, or ecosystem maps.
"""

from __future__ import annotations

import hashlib
import math
import pathlib
import re
import unicodedata
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

import pandas as pd

from sag_api.research.input_inspector import normalize_ctg_font_quirks

_CORP_PREFIX_NON_BANK_RE = re.compile(
    r"^(?:tổng\s+công\s+ty\s+cổ\s+phần|tổng\s+ctcp|tổng\s+công\s+ty|công\s+ty\s+cổ\s+phần|"
    r"công\s+ty\s+tnhh|ctcp)\s+",
    re.IGNORECASE,
)

_GENERIC_ALIAS_STOP = frozenset(
    {
        "việt nam",
        "hà nội",
        "sài gòn",
        "miền nam",
        "miền trung",
        "miền bắc",
        "đầu tư",
        "xây dựng",
        "thương mại",
        "dịch vụ",
        "phát triển",
        "công nghiệp",
        "quốc tế",
        "tập đoàn",
        "chứng khoán",
        "ngân hàng",
        "bất động sản",
        "vận tải",
        "dầu khí",
        "điện lực",
        "hóa chất",
        "nông nghiệp",
        "thủy sản",
        "dệt may",
        "đông nam á",
        "tiên phong",
        "phương đông",
        "nam việt",
        "bảo minh",
        "xuất nhập khẩu việt nam",
    }
)

_GENERIC_ORG_WORDS = frozenset(
    {
        "công",
        "ty",
        "cổ",
        "phần",
        "ctcp",
        "tổng",
        "ngân",
        "hàng",
        "tmcp",
        "nhtmcp",
        "thương",
        "mại",
        "việt",
        "nam",
        "tập",
        "đoàn",
        "tnhh",
        "mtv",
        "và",
        "các",
        "đầu",
        "tư",
        "phát",
        "triển",
        "quốc",
        "tế",
        "dịch",
        "vụ",
        "sản",
        "xuất",
    }
)

# Linguistic cue patterns for matching macro risk notes to available indicator keys.
DEFAULT_MACRO_DISCLOSURE_PATTERNS: dict[str, re.Pattern[str]] = {
    "usd_vnd_exchange": re.compile(
        r"(?:rủi ro tỷ giá|độ nhạy.{0,25}tỷ giá|biến động tỷ giá|chênh lệch tỷ giá|"
        r"tỷ giá\s+usd|vay.{0,25}usd|ngoại tệ\s+usd)",
        re.IGNORECASE,
    ),
    "vninbr_interbank_rate": re.compile(
        r"(?:rủi ro lãi suất|độ nhạy.{0,25}lãi suất|biến động lãi suất|lãi suất thả nổi|"
        r"lãi suất liên ngân hàng|chi phí vốn|lãi suất huy động)",
        re.IGNORECASE,
    ),
    "vnintr_interest_rate": re.compile(
        r"(?:rủi ro lãi suất|độ nhạy.{0,25}lãi suất|biến động lãi suất|lãi suất thả nổi|lãi suất điều hành)",
        re.IGNORECASE,
    ),
    "oil_price_brent": re.compile(
        r"(?:giá dầu|dầu thô|nhiên liệu|brent|giá nhiên liệu|cước vận tải dầu)",
        re.IGNORECASE,
    ),
}

_MAX_VALID_TO = date(9999, 12, 31)

# Standard finance, currency, macro, and technical acronyms that are NEVER standalone stock tickers
NON_TICKER_ACRONYMS: frozenset[str] = frozenset(
    {
        "ROE", "ROA", "ROI", "NIM", "CIR", "CAR", "NPL", "LDR", "EPS", "VND", "USD", "EUR",
        "JPY", "CNY", "GBP", "AUD", "KRW", "SGD", "GDP", "CPI", "PMI", "FDI", "ODA", "PPP",
        "FED", "SBV", "IMF", "WTO", "FTA", "ESG", "GRI", "GHG", "ISO", "FSC", "GMP", "AGM",
        "CEO", "CFO", "COO", "CTO", "CIO", "BKS", "NAV", "IRR", "NPV", "DCF", "IPO", "KPI",
        "OKR", "VAS", "API", "SQL", "PDF", "OCR", "PIT", "BFF", "SSE", "LLM", "RAG", "RRF",
        "YOY", "QOQ", "YTD", "TTM", "AFS", "HTM", "BCT", "CTY", "TND", "VAT",
        "BPS", "DWT", "TEU", "MWP", "KWH", "MWH", "TON", "VNĐ", "HĐQ", "DTT", "VĐL",
        "NPT", "SME", "OEM", "ODM", "GPU", "CPU", "BPO", "ERP", "ICT", "KCN", "BDS", "BĐS",
        "LPG", "LNG", "HRC", "BOF", "EAF", "PPA", "EVN", "BOT", "EPC", "FOB", "CIF", "WTI",
        "LNST", "LNTT", "TNDN", "GTGT", "HĐQT", "NHNN", "CTCP", "TNHH", "TSCĐ", "TSNH",
        "TSDH", "VCSH", "KHDN", "KHCN", "OPEC", "SAAS", "FMCG", "BCTC", "BCTN", "HOSE",
        "HNX", "VN30", "MOAT", "CAGR", "CASA", "WACC", "ESOP", "CAPEX", "OPEX", "IFRS",
        "TT200", "EBITDA", "EBIT", "FVTPL", "P&L", "THE", "AND", "FOR", "NOT", "BUT", "ALL",
        "ANY", "NEW", "OLD", "TOP", "MAX", "MIN", "AVG", "SUM", "PCT", "NET", "TAX", "REV",
        "PAT", "PBT", "NWC", "FCF", "OCF", "D&A", "SGA", "COGS", "GPM", "NPM", "OPM", "ROIC",
        "ROCE", "PEG", "PER", "PBR", "PSR", "DYR", "CĐKT", "KQKD", "LCTT", "EVFTA",
        "DNSE", "VSD", "VSDC", "UBCK", "SSC", "BTC", "CPN", "ĐHĐCĐ", "BĐH", "TCTD", "CTCK",
        "MTV", "LLC", "LTD", "INC", "CORP", "PLC", "JSC", "GMBH", "CON",
    }
)
# Keep real Vietnam stock tickers out of NON_TICKER_ACRONYMS (e.g. VNM, VCS, TCT, TNC, EVS)
NON_TICKER_ACRONYMS = NON_TICKER_ACRONYMS - frozenset({"VNM", "VCS", "TCT", "TNC", "EVS"})

_EXPLICIT_PAREN_TICKER_RE = re.compile(
    r"(?i:công\s+ty|ctcp|tổng\s+công\s+ty|ngân\s+hàng|chứng\s+khoán|tập\s+đoàn|"
    r"nhà\s+máy|mã(?:\s+chứng\s+khoán|\s+cổ\s+phiếu|\s+ck)?|cổ\s+phiếu|đối\s+tác|"
    r"khách\s+hàng|nhà\s+cung\s+cấp|công\s+ty\s+con|liên\s+kết)"
    r"[^\n.;,()]{0,75}\(\s*(?:(?i:HOSE|HNX|UPCOM)\s*:\s*)?([A-Z]{3})\s*\)"
)
_EXCHANGE_PREFIX_TICKER_RE = re.compile(
    r"(?i:HOSE|HNX|UPCOM|mã\s+(?:cổ\s+phiếu|chứng\s+khoán|ck)|cổ\s+phiếu|ticker)\s*[:\-]?\s*([A-Z]{3})(?![A-Za-z0-9_])"
)
_CORP_DIRECT_TICKER_RE = re.compile(
    r"(?i:CTCP|Tổng\s+CTCP|Công\s+ty(?:\s+Cổ\s+phần|\s+TNHH)?|Ngân\s+hàng|Chứng\s+khoán|Tập\s+đoàn)"
    r"\s+([A-Z]{3})(?![A-Za-z0-9_])"
)
_UPPER_3LETTER_RE = re.compile(r"(?<![A-Za-z0-9_])([A-Z]{3})(?![A-Za-z0-9_])")


def compile_issuer_registry_from_rows(
    rows: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], list[re.Pattern[str]], re.Pattern[str], re.Pattern[str], set[str]]]:
    """Compile dynamic issuer matcher tuples from arbitrary `(symbol, name, sector)` rows (0% hardcoded)."""
    if not rows:
        return []
    df = pd.DataFrame(
        [
            {
                "symbol": str(r.get("symbol") or r.get("ticker") or "").strip().upper(),
                "name": str(r.get("name") or r.get("label") or "").strip(),
                "sector": str(r.get("sector") or "UNKNOWN"),
            }
            for r in rows
            if (r.get("symbol") or r.get("ticker"))
        ]
    )
    if df.empty:
        return []
    registry = build_dynamic_issuer_registry(df)
    return _compile_issuers(registry) if registry else []


def extract_mentioned_tickers(
    text: str,
    *,
    own_ticker: str = "",
    known_tickers: Iterable[str] | None = None,
    compiled_issuers: Sequence[
        tuple[dict[str, Any], list[re.Pattern[str]], re.Pattern[str], re.Pattern[str], set[str]]
    ]
    | None = None,
    max_tickers: int = 24,
) -> list[str]:
    """Deterministically extract external HOSE-style tickers mentioned in a text chunk (0 LLM, 0% hardcoded).

    Combines:
    1. Dynamic issuer registry aliases & corporate parenthetical patterns (`compiled_issuers`).
    2. Explicit corporate/exchange ticker mentions (e.g. `Ngân hàng TMCP Ngoại thương (VCB)`, `HOSE: HPG`).
    3. Exact uppercase matches against dynamically discovered `known_tickers` (excluding self & non-ticker acronyms).
    """
    cleaned = normalize_ws(repair_pdf_font_quirks(text or ""))
    if not cleaned:
        return []
    own = (own_ticker or "").strip().upper()
    known = {str(t).strip().upper() for t in (known_tickers or ()) if str(t).strip()}
    found: set[str] = set()

    def _maybe_add(sym: str, *, from_registry_alias: bool = False, explicit_exchange: bool = False) -> None:
        s = (sym or "").strip().upper()
        if not s or len(s) != 3 or not s.isalpha() or not s.isascii():
            return
        if own and s == own:
            return
        if not from_registry_alias and not explicit_exchange and s in NON_TICKER_ACRONYMS:
            return
        found.add(s)

    # 1. Dynamic issuer registry (company full names, aliases, parenthetical & direct cues)
    if compiled_issuers:
        cleaned_low = cleaned.lower()
        for entry, alias_pats, paren_pat, direct_pat, name_words in compiled_issuers:
            sym = str(entry.get("symbol") or "").strip().upper()
            if not sym or sym == own:
                continue
            if not (
                (entry["aliases"] and any(al in cleaned_low for al in entry["aliases"]))
                or (sym in cleaned)
            ):
                continue
            matched = False
            for pat in alias_pats:
                if pat.search(cleaned):
                    matched = True
                    break
            if not matched and direct_pat.search(cleaned):
                matched = True
            if not matched:
                mm = paren_pat.search(cleaned)
                if mm:
                    pre_words = _distinctive_words(mm.group(0))
                    if not name_words or (pre_words & name_words):
                        matched = True
            if matched:
                _maybe_add(sym, from_registry_alias=True)

    # 2. Explicit corporate / exchange-qualified 3-letter ticker patterns (works even before target is ingested!)
    for m in _EXPLICIT_PAREN_TICKER_RE.finditer(cleaned):
        _maybe_add(m.group(1))
    for m in _EXCHANGE_PREFIX_TICKER_RE.finditer(cleaned):
        _maybe_add(m.group(1), explicit_exchange=True)
    for m in _CORP_DIRECT_TICKER_RE.finditer(cleaned):
        _maybe_add(m.group(1))

    # 3. Dynamically discovered known tickers present as uppercase standalone tokens
    if known:
        for tok in _UPPER_3LETTER_RE.findall(cleaned):
            if tok in known and tok not in NON_TICKER_ACRONYMS:
                _maybe_add(tok)

    return sorted(found)[: max(1, int(max_tickers))]


@dataclass(frozen=True, slots=True)
class DocumentPageInput:
    """Normalized page input for document entity linking."""

    ticker: str
    document_name: str
    document_version_id: str
    sub_doc_slot: str
    published_date: date
    page_no: int
    text: str


def repair_pdf_font_quirks(text: str) -> str:
    """Repair common Vietnamese PDF encoding artifacts (delegating to canonical `normalize_ctg_font_quirks`)."""
    return normalize_ctg_font_quirks(text)


def normalize_ws(text: str) -> str:
    """Normalize NFC unicode and collapse whitespace for deterministic hashing & matching."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip()


def quote_hash(quote: str) -> str:
    """Compute SHA-256 hex digest of whitespace-normalized quote."""
    return hashlib.sha256(normalize_ws(quote).encode("utf-8")).hexdigest()


def _distinctive_words(txt: str) -> set[str]:
    toks = re.findall(r"[0-9a-zà-ỹ]{2,}", txt.lower())
    return {w for w in toks if w not in _GENERIC_ORG_WORDS}


def build_dynamic_issuer_registry(stocks_df: pd.DataFrame) -> list[dict[str, Any]]:
    """Build issuer aliases 100% dynamically from Core DB `stocks JOIN instrument_master`.

    Guards against false positives:
    - Never strips 'ngân hàng' from bank names (prevents slogan/word collisions).
    - Drops any stripped alias shared by >= 2 distinct tickers.
    - Requires stripped aliases to have >= 3 words and >= 14 chars.
    """
    if stocks_df.empty:
        return []

    raw_entries: list[dict[str, Any]] = []
    alias_owners: dict[str, set[str]] = defaultdict(set)

    for row in stocks_df.itertuples(index=False):
        sym = str(getattr(row, "symbol", "")).strip().upper()
        if not sym:
            continue
        full_name = normalize_ws(str(getattr(row, "name", "") or ""))
        sector = str(getattr(row, "sector", None) or "UNKNOWN")
        aliases: set[str] = set()
        if full_name and full_name.upper() != sym:
            fn_low = full_name.lower()
            aliases.add(fn_low)
            if fn_low.startswith("ctcp "):
                aliases.add("công ty cổ phần " + fn_low[5:])
            elif fn_low.startswith("tổng ctcp "):
                aliases.add("tổng công ty cổ phần " + fn_low[10:])
            elif fn_low.startswith("ngân hàng tmcp "):
                aliases.add("ngân hàng thương mại cổ phần " + fn_low[15:])

            if not fn_low.startswith("ngân hàng"):
                stripped = _CORP_PREFIX_NON_BANK_RE.sub("", fn_low).strip(" -–—")
                stripped = re.sub(r"\s*-\s*ctcp$", "", stripped, flags=re.IGNORECASE).strip()
                if (
                    len(stripped) >= 14
                    and len(stripped.split()) >= 3
                    and stripped not in _GENERIC_ALIAS_STOP
                ):
                    aliases.add(stripped)
        for al in aliases:
            alias_owners[al].add(sym)
        raw_entries.append({"symbol": sym, "name": full_name, "sector": sector, "aliases": aliases})

    entries: list[dict[str, Any]] = []
    for entry in raw_entries:
        unique_aliases = sorted(
            [al for al in entry["aliases"] if len(alias_owners[al]) == 1],
            key=len,
            reverse=True,
        )
        entries.append(
            {
                "symbol": entry["symbol"],
                "name": entry["name"],
                "sector": entry["sector"],
                "aliases": unique_aliases,
            }
        )
    return entries


def classify_micro_relation(
    sentence: str,
    page_context_low: str = "",
    match_span: tuple[int, int] | None = None,
    sticky_section: str = "",
) -> tuple[str, float] | None:
    """Deterministic relation classifier using entity-local context + sticky section state."""
    sl = sentence.lower()
    if match_span is not None:
        a, b = match_span
        local_before = sl[max(0, a - 140) : a]
        local_after = sl[b : min(len(sl), b + 140)]
        local_win = sl[max(0, a - 160) : min(len(sl), b + 160)]
    else:
        local_before, local_after, local_win = sl, sl, sl

    # 1. Explicit third-party customer / advance-from-buyer lines BEFORE related-party/creditor checks
    if any(
        k in local_win
        for k in (
            "người mua trả tiền trước",
            "phải thu khách hàng",
            "phải thu ngắn hạn của khách hàng",
            "khách hàng bên thứ ba",
        )
    ) or (
        "khách hàng bên thứ ba" in page_context_low
        and "bên liên quan" not in local_before[-80:]
        and "công ty con" not in local_after[:40]
    ):
        return ("CUSTOMER_OF", 0.88)

    # 2. Explicit subsidiary appositive right after entity (exclude '<Entity> và các công ty con')
    is_own_subsidiaries_phrase = bool(re.match(r"\s*và\s+các\s+công\s+ty\s+con", local_after))
    explicit_sub_appositive = (not is_own_subsidiaries_phrase) and (
        bool(re.match(r"\s*,\s*công\s+ty\s+con\b", local_after))
        or ("công ty con của" in local_after[:50])
    )
    if explicit_sub_appositive:
        return ("OWNS_SUBSIDIARY", 0.90)

    # 3. Related party / fellow group entity note
    in_related_party_section = (
        sticky_section == "RELATED_PARTY"
        or "nghiệp vụ và số dư với các bên liên quan" in page_context_low
        or "danh sách các bên liên quan" in page_context_low
        or ("bên liên quan" in local_before[-100:] and "bên thứ ba" not in local_before[-80:])
    )
    if (
        in_related_party_section
        and "tỷ lệ sở hữu của" not in local_win
        and "tỷ lệ phần sở hữu" not in local_win
    ):
        return ("RELATED_PARTY_GROUP", 0.85)

    # 4. Ownership tables/paragraphs (subsidiary vs associate)
    if (not is_own_subsidiaries_phrase) and any(
        k in local_win for k in ("tỷ lệ sở hữu", "tỷ lệ phần sở hữu", "tỷ lệ biểu quyết", "nắm giữ")
    ):
        pct_match = re.search(r"(\d{1,3}(?:[.,]\d{1,2})?)\s*%", local_win)
        if pct_match:
            val = float(pct_match.group(1).replace(",", "."))
            if 50.0 < val <= 100.0:
                return ("OWNS_SUBSIDIARY", 0.92)
            if 20.0 <= val <= 50.0:
                return ("ASSOCIATE_OF", 0.88)
    if (
        (not is_own_subsidiaries_phrase)
        and "công ty con" in local_win
        and "và các công ty con" not in local_win
    ):
        return ("OWNS_SUBSIDIARY", 0.85)
    if "công ty liên kết" in local_win and "công ty con" not in local_win:
        return ("ASSOCIATE_OF", 0.82)

    # 5. Creditor / bank guarantee / borrowing
    if any(
        k in local_win
        for k in (
            "vay ngắn hạn",
            "vay dài hạn",
            "hạn mức tín dụng",
            "chủ nợ",
            "bảo lãnh bởi",
            "khoản vay tại",
        )
    ):
        return ("CREDITOR_OF", 0.86)

    # 6. Value-chain customer / supplier
    if any(
        k in local_win
        for k in (
            "khách hàng",
            "vận chuyển cho",
            "vận chuyển an toàn",
            "cung cấp cho",
            "bao tiêu",
            "hợp đồng vận chuyển",
            "nhà máy lọc dầu",
            "tiêu thụ",
        )
    ):
        return ("CUSTOMER_OF", 0.84)
    if any(
        k in local_win
        for k in (
            "nhà cung cấp",
            "mua nguyên liệu",
            "đầu vào từ",
            "mua hàng từ",
            "thuê tàu của",
            "cung ứng",
        )
    ):
        return ("SUPPLIER_OF", 0.84)

    # 7. Explicit competitor
    if any(k in local_win for k in ("đối thủ cạnh tranh", "cạnh tranh trực tiếp", "cạnh tranh với")):
        return ("COMPETES_WITH", 0.80)

    return None


def _compile_issuers(
    registry: list[dict[str, Any]],
) -> list[tuple[dict[str, Any], list[re.Pattern[str]], re.Pattern[str], re.Pattern[str], set[str]]]:
    compiled = []
    for entry in registry:
        alias_pats = [
            re.compile(r"(?<!\w)" + re.escape(al) + r"(?!\w)", re.IGNORECASE)
            for al in entry["aliases"]
        ]
        sym = entry["symbol"]
        paren_pat = re.compile(
            rf"(?:công ty|ctcp|tổng công ty|ngân hàng|chứng khoán|tập đoàn|nhà máy|mã|cổ phiếu|đối tác)"
            rf"[^\n.;,]{{0,65}}\(\s*{re.escape(sym)}\s*\)",
            re.IGNORECASE,
        )
        direct_pat = re.compile(
            rf"(?:công ty|ctcp|tổng công ty|ngân hàng|mã|cổ phiếu|nhà máy|đối tác)\s+{re.escape(sym)}\b",
            re.IGNORECASE,
        )
        compiled.append((entry, alias_pats, paren_pat, direct_pat, _distinctive_words(entry["name"])))
    return compiled


def _infer_macro_disclosures_on_page(
    *,
    src_ticker: str,
    page_no: int,
    document_name: str,
    document_version_id: str,
    sub_doc_slot: str,
    doc_pub: date,
    text_norm: str,
    text_low: str,
    macro_patterns: Mapping[str, re.Pattern[str]],
    doc_macro_disclosures: dict[str, dict[str, Any]],
) -> None:
    is_risk_or_note_page = any(
        k in text_low
        for k in (
            "thuyết minh",
            "rủi ro",
            "độ nhạy",
            "ngoại tệ",
            "lãi suất",
            "chính sách quản trị rủi ro",
            "vay và nợ",
        )
    )
    is_cashflow_stmt = "lưu chuyển tiền tệ" in text_low and "điều chỉnh cho các khoản" in text_low
    if not is_risk_or_note_page or is_cashflow_stmt:
        return

    for ind_key, pat in macro_patterns.items():
        m = pat.search(text_norm)
        if not m:
            continue
        s_start = max(0, m.start() - 120)
        s_end = min(len(text_norm), m.end() + 220)
        snippet = text_norm[s_start:s_end].strip()
        has_quant_num = bool(
            re.search(r"\d+(?:[.,]\d+)?\s*%|tỷ đồng|triệu đồng|nghìn tỷ", snippet, re.IGNORECASE)
        )
        snip_low = snippet.lower()
        snip_no_neutral_hdr = re.sub(r"\(lãi\)/lỗ|lãi/\(lỗ\)", "", snip_low)
        if any(
            k in snip_no_neutral_hdr
            for k in (
                "tăng lợi nhuận",
                "hưởng lợi",
                "tích cực",
                "doanh thu bằng usd",
                "thu nhập bằng usd",
            )
        ):
            stated_sign = 1
        elif any(
            k in snip_no_neutral_hdr
            for k in (
                "giảm lợi nhuận",
                "chi phí tăng",
                "rủi ro tăng chi phí",
                "lỗ chênh lệch tỷ giá",
                "bất lợi",
                "áp lực chi phí",
                "ảnh hưởng tiêu cực",
                "vay bằng usd",
                "nợ vay bằng ngoại tệ",
            )
        ):
            stated_sign = -1
        else:
            stated_sign = 0

        prev = doc_macro_disclosures[src_ticker].get(ind_key)
        score = (2 if has_quant_num else 1) + (2 if stated_sign != 0 else 0)
        if prev is None or score > prev["priority"]:
            doc_macro_disclosures[src_ticker][ind_key] = {
                "ticker": src_ticker,
                "indicator_name": ind_key,
                "document": document_name,
                "document_version_id": document_version_id,
                "sub_doc_slot": sub_doc_slot,
                "page_no": page_no,
                "stated_sign": stated_sign,
                "has_quant_sensitivity": has_quant_num,
                "quote": snippet,
                "quote_hash": quote_hash(snippet),
                "priority": score,
                "effective_from": str(doc_pub),
            }


def rollup_sector_edges(
    micro_edges: Sequence[Mapping[str, Any]],
    stocks_df: pd.DataFrame,
    as_of_date: date,
) -> list[dict[str, Any]]:
    """Bottom-Up Cross-Sector Roll-up from PIT-valid micro company edges."""
    if stocks_df.empty or not micro_edges:
        return []
    sector_sizes = (
        stocks_df.dropna(subset=["sector"]).groupby("sector")["symbol"].nunique().to_dict()
    )
    rollup_buckets: dict[tuple[str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for edge in micro_edges:
        eff_raw = edge.get("effective_from")
        if eff_raw:
            eff_d = date.fromisoformat(str(eff_raw)[:10]) if not isinstance(eff_raw, date) else eff_raw
            if eff_d > as_of_date:
                continue
        su = str(edge.get("source_sector") or "UNKNOWN")
        sv = str(edge.get("target_sector") or "UNKNOWN")
        if su == "UNKNOWN" or sv == "UNKNOWN" or su == sv:
            continue
        rel = str(edge.get("relation_type") or "")
        rollup_buckets[(su, sv, rel)].append(edge)

    sector_rollup_edges: list[dict[str, Any]] = []
    for (su, sv, rel), edges in rollup_buckets.items():
        n_u = max(1, int(sector_sizes.get(su, 1)))
        n_v = max(1, int(sector_sizes.get(sv, 1)))
        w_doc = sum(float(e.get("weight", 0.0)) for e in edges) / math.sqrt(n_u * n_v)
        src_id = f"SECTOR:{su}"
        dst_id = f"SECTOR:{sv}"
        rollup_rel = f"ROLLUP_{rel}"
        sector_rollup_edges.append(
            {
                "edge_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{src_id}:{dst_id}:{rollup_rel}:{as_of_date}")),
                "src_node_id": src_id,
                "dst_node_id": dst_id,
                "source_node_id": src_id,
                "target_node_id": dst_id,
                "relation_type": rollup_rel,
                "layer": "VALUE_CHAIN",
                "direction": "DIRECTED",
                "micro_link_count": len(edges),
                "meets_multi_link_threshold": len(edges) >= 2,
                "weight": round(w_doc, 4),
                "verification_tier": "DOC_ONLY",
                "supporting_micro_edges": [
                    (
                        f"{e.get('src_node_id') or e.get('source_node_id')}->"
                        f"{e.get('dst_node_id') or e.get('target_node_id')} "
                        f"(P{e['provenance_json'][0]['page_no']})"
                    )
                    for e in edges[:5]
                ],
                "effective_from": as_of_date.isoformat(),
                "valid_to": _MAX_VALID_TO.isoformat(),
            }
        )
    sector_rollup_edges.sort(key=lambda x: (-x["micro_link_count"], -x["weight"]))
    return sector_rollup_edges


def extract_document_micro_edges_from_pages(
    stocks_df: pd.DataFrame,
    pages: Iterable[DocumentPageInput],
    as_of_date: date,
    *,
    available_macro_indicators: Iterable[str] | None = None,
    macro_patterns: Mapping[str, re.Pattern[str]] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Extract PIT-gated company-to-company edges, sector roll-ups, and macro risk disclosures.

    Strictly drops any page/document where `published_date > as_of_date`.
    """
    registry = build_dynamic_issuer_registry(stocks_df)
    if not registry:
        return [], [], {}

    sector_map = dict(zip(stocks_df["symbol"], stocks_df["sector"], strict=True))
    compiled_issuers = _compile_issuers(registry)

    base_patterns = dict(macro_patterns or DEFAULT_MACRO_DISCLOSURE_PATTERNS)
    if available_macro_indicators is not None:
        allowed_inds = {str(ind) for ind in available_macro_indicators}
        active_patterns = {k: v for k, v in base_patterns.items() if k in allowed_inds}
    else:
        active_patterns = base_patterns

    micro_edges_map: dict[tuple[str, str, str], dict[str, Any]] = {}
    doc_macro_disclosures: dict[str, dict[str, Any]] = defaultdict(dict)

    # Group pages per (ticker, document_version_id) in chronological page order for sticky RELATED_PARTY tracking
    grouped_docs: dict[tuple[str, str], list[DocumentPageInput]] = defaultdict(list)
    for page in pages:
        if page.published_date > as_of_date:
            # Strict Point-in-Time gate: NEVER read documents published after as_of_date!
            continue
        grouped_docs[(page.ticker.strip().upper(), page.document_version_id)].append(page)

    for (src_ticker, _ver_id), doc_pages in grouped_docs.items():
        doc_pages.sort(key=lambda p: p.page_no)
        sticky_section = ""
        sticky_pages_left = 0

        for page in doc_pages:
            raw_text = repair_pdf_font_quirks(page.text)
            if len(raw_text) < 50:
                continue
            text_norm = normalize_ws(raw_text)
            text_low = text_norm.lower()

            if (
                "nghiệp vụ và số dư với các bên liên quan" in text_low
                or "danh sách các bên liên quan" in text_low
            ):
                sticky_section = "RELATED_PARTY"
                sticky_pages_left = 3
            elif sticky_section == "RELATED_PARTY":
                is_note_cont = (
                    sticky_pages_left > 0
                    and ("(tiếp theo)" in text_low or "(continued)" in text_low)
                    and (
                        "thuyết minh" in text_low
                        or "b 09" in text_low
                        or "b09" in text_low
                        or "bên liên quan" in text_low
                    )
                )
                if is_note_cont and not any(
                    k in text_low
                    for k in ("sự kiện phát sinh sau", "báo cáo bộ phận", "cam kết thuê", "nợ tiềm tàng")
                ):
                    sticky_pages_left -= 1
                else:
                    sticky_section = ""
                    sticky_pages_left = 0

            _infer_macro_disclosures_on_page(
                src_ticker=src_ticker,
                page_no=page.page_no,
                document_name=page.document_name,
                document_version_id=page.document_version_id,
                sub_doc_slot=page.sub_doc_slot,
                doc_pub=page.published_date,
                text_norm=text_norm,
                text_low=text_low,
                macro_patterns=active_patterns,
                doc_macro_disclosures=doc_macro_disclosures,
            )

            if (
                not any(
                    k in text_low
                    for k in (
                        "công ty con",
                        "liên kết",
                        "khách hàng",
                        "nhà cung cấp",
                        "ngân hàng",
                        "vay",
                        "đối tác",
                        "hợp đồng",
                        "vận chuyển",
                        "đối thủ",
                        "cạnh tranh",
                        "bên liên quan",
                    )
                )
                and sticky_section != "RELATED_PARTY"
            ):
                continue

            segments = re.split(r"(?:\n\s*\n|(?<=[.;])\s+)", raw_text)
            for sent in segments:
                sent_clean = normalize_ws(sent)
                if len(sent_clean) < 25 or len(sent_clean) > 600:
                    continue
                sent_low = sent_clean.lower()
                for entry, alias_pats, paren_pat, direct_pat, name_words in compiled_issuers:
                    tgt_sym = entry["symbol"]
                    if tgt_sym == src_ticker:
                        continue
                    if not (
                        (entry["aliases"] and any(al in sent_low for al in entry["aliases"]))
                        or (tgt_sym in sent_clean)
                    ):
                        continue
                    first_match = None
                    for pat in alias_pats:
                        mm = pat.search(sent_clean)
                        if mm:
                            first_match = mm
                            break
                    if first_match is None:
                        mm = direct_pat.search(sent_clean)
                        if mm:
                            first_match = mm
                    if first_match is None:
                        mm = paren_pat.search(sent_clean)
                        if mm:
                            pre_words = _distinctive_words(mm.group(0))
                            if not name_words or (pre_words & name_words):
                                first_match = mm
                    if first_match is None:
                        continue

                    rel_info = classify_micro_relation(
                        sent_clean,
                        page_context_low=text_low,
                        match_span=first_match.span(),
                        sticky_section=sticky_section,
                    )
                    if rel_info is None:
                        continue
                    rel_type, conf = rel_info
                    key = (src_ticker, tgt_sym, rel_type)
                    if key not in micro_edges_map or conf > micro_edges_map[key]["weight"]:
                        m_a, m_b = first_match.span()
                        q_start = max(0, m_a - 110)
                        q_end = min(len(sent_clean), max(q_start + 280, m_b + 150))
                        centered_quote = sent_clean[q_start:q_end].strip()
                        src_node = f"TICKER:{src_ticker}"
                        dst_node = f"TICKER:{tgt_sym}"
                        micro_edges_map[key] = {
                            "edge_id": str(
                                uuid.uuid5(uuid.NAMESPACE_DNS, f"{src_ticker}:{tgt_sym}:{rel_type}")
                            ),
                            "src_node_id": src_node,
                            "dst_node_id": dst_node,
                            "source_node_id": src_node,
                            "target_node_id": dst_node,
                            "source_sector": str(sector_map.get(src_ticker) or "UNKNOWN"),
                            "target_sector": entry["sector"],
                            "target_name": entry["name"],
                            "relation_type": rel_type,
                            "layer": "VALUE_CHAIN",
                            "direction": "DIRECTED",
                            "weight": conf,
                            "verification_tier": "DOC_ONLY",
                            "evidence_type": "DOC_EXTRACTED",
                            "provenance_json": [
                                {
                                    "document": page.document_name,
                                    "document_version_id": page.document_version_id,
                                    "sub_doc_slot": page.sub_doc_slot,
                                    "page_no": page.page_no,
                                    "published_date": page.published_date.isoformat(),
                                    "quote": centered_quote,
                                    "quote_hash": quote_hash(centered_quote),
                                }
                            ],
                            "effective_from": page.published_date.isoformat(),
                            "valid_to": _MAX_VALID_TO.isoformat(),
                        }

    owned_pairs = {(src, tgt) for (src, tgt, rel) in micro_edges_map if rel == "OWNS_SUBSIDIARY"}
    filtered_edges = [
        edge
        for (src, tgt, rel), edge in micro_edges_map.items()
        if not ((src, tgt) in owned_pairs and rel in ("RELATED_PARTY_GROUP", "ASSOCIATE_OF"))
    ]
    micro_edges = sorted(
        filtered_edges,
        key=lambda x: (x["src_node_id"], -x["weight"], x["dst_node_id"]),
    )
    sector_rollup_edges = rollup_sector_edges(micro_edges, stocks_df, as_of_date)
    return micro_edges, sector_rollup_edges, dict(doc_macro_disclosures)


def extract_document_micro_edges_from_pdfs(
    stocks_df: pd.DataFrame,
    pdf_paths: Mapping[str, pathlib.Path],
    as_of_date: date,
    *,
    pdf_published_dates: Mapping[str, date],
    sub_doc_slot: str = "S09_FIN_INVEST_PROV",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Convenience adapter to extract micro edges directly from PDF files via PyMuPDF."""
    import fitz  # lazy import so text-only callers do not require PyMuPDF

    pages: list[DocumentPageInput] = []
    for ticker, pdf_path in pdf_paths.items():
        if not pdf_path.exists():
            continue
        pub_date = pdf_published_dates.get(ticker)
        if pub_date is None or pub_date > as_of_date:
            continue
        doc = fitz.open(str(pdf_path))
        try:
            ver_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"{ticker}:{pdf_path.name}:{pub_date}"))
            for page_idx, page in enumerate(doc, 1):
                rect = page.rect
                if rect.width > rect.height * 1.25:
                    mid = rect.width / 2.0
                    words = page.get_text("words")
                    crossing = sum(1 for w in words if w[0] < mid - 4 and w[2] > mid + 4)
                    if crossing <= 2 and len(words) > 20:
                        halves = [
                            page.get_text("text", clip=fitz.Rect(0, 0, mid, rect.height)) or "",
                            page.get_text("text", clip=fitz.Rect(mid, 0, rect.width, rect.height)) or "",
                        ]
                    else:
                        halves = [page.get_text("text") or ""]
                else:
                    halves = [page.get_text("text") or ""]
                for half_text in halves:
                    pages.append(
                        DocumentPageInput(
                            ticker=ticker,
                            document_name=pdf_path.name,
                            document_version_id=ver_id,
                            sub_doc_slot=sub_doc_slot,
                            published_date=pub_date,
                            page_no=page_idx,
                            text=half_text,
                        )
                    )
        finally:
            doc.close()

    return extract_document_micro_edges_from_pages(stocks_df, pages, as_of_date)
