r"""Vietnamese Bilingual Prose & 4-Pattern Table Cleaner (`vn-clean-v3`, 0 LLM).

Fixes all legacy `markdown_noise_cleaner.py` blindspots while guaranteeing 0% numeric or
entity loss:
1. Paired-Line Mirror Stripping: strips English translations immediately following Vietnamese
   lines across prose, headings (`#`), blockquotes (`>`), and bullets/numbered lists (`-`, `*`, `1.`),
   including short 2–7 word translations (`Net revenue`, `Profit after tax`, `2026 Business Plan`),
   strictly verifying `nums(EN) <= nums(VI)` and preserving introduced proper entity names
   (e.g. `BAF Vietnam Agriculture JSC`, `FPT Software`, `IFRS 16`, `EV/EBITDA`, `CASA`).
2. Inline Slash & Parenthetical Mirror Stripping: cleans `Doanh thu thuần / Net revenue`,
   `Đơn vị tính: Triệu đồng / Unit: Million VND`, and `Tài sản ngắn hạn (Current assets)`
   without touching `(USD)`, `(VND)`, `(HOSE: FPT)`, `EV/EBITDA`, `P/E`, `D/E`.
3. 4-Pattern Bilingual Table Cleaning (shared by HTML `<table>`, `pdfplumber` Lattice,
   Coordinate Reconstructor, and GFM Markdown tables):
   - Pattern 1: Stacked multi-line (`<br>` / `\n`) and inline `/` or `(EN)` bilingual cells & headers.
   - Pattern 2: Paired empty-numeric English subtitle rows underneath Vietnamese data rows.
   - Pattern 3: Parallel English translation label columns (`| Chỉ tiêu | Items | Mã số | ... |`).
   - Pattern 4: Duplicate full-English mirror tables matching a Vietnamese table's numeric fingerprint.
4. OCR Noise Hygiene: removes MinerU CDN `![image](...)` links, LaTeX OCR garbage (`\frac`, `\Omega`),
   SSC/HOSE boilerplate English cover headers, and fragmented audit stamp noise.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any

from sag_api.research.vn_language_gate import VN_UNACCENTED_TEXT_CUES

CLEANER_VERSION = "vn-clean-v3"

_VIET_CHAR_RE = re.compile(r"[ĂÂÊÔƠƯĐăâêôơưđÀ-ỹà-ỹ]")
_EN_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LATEX_JUNK_RE = re.compile(r"\\(?:frac|delta|partial|sum|theta|alpha|beta|omega)\b", re.IGNORECASE)
_PAGE_MARKER_LINE_RE = re.compile(r"^\s*<!--\s*PAGE\s+\d+\s*-->\s*$", re.IGNORECASE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_LIST_PREFIX_RE = re.compile(r"^(\s*(?:[-*•–]|\d{1,2}[.)]|[a-zA-Z][.)])\s+)(.+)$")
_HTML_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_HTML_CELL_RE = re.compile(
    r"(<t[dh]\b[^>]*>)(.*?)(</t[dh]>)",
    re.IGNORECASE | re.DOTALL,
)
_HTML_TR_RE = re.compile(r"<tr\b[^>]*>.*?</tr>", re.IGNORECASE | re.DOTALL)
_BR_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")

_STAMP_SYMBOLS_RE = re.compile(r"^[\d\s./\-+*★^\\_|~©®:]{2,}$")
_STAMP_NOISE_RE = re.compile(
    r"^\s*(?:C\.?T\.?T?\.?N\.?H\.?H|C\.?I\.?T|C\.?T\.?N|C\.?T\.?I\.?NG|"
    r"ERNST\s*&\s*YOUNG\s+VIETNAM|Z\.?H\.?H\.?\s*★?|N\.?H\.?H\s*★?)\s*$",
    re.IGNORECASE,
)

_SSC_ENGLISH_BOILERPLATE_RE = re.compile(
    r"^\s*(?:The\s+Socialist\s+Republic\s+of\s+Vietnam|Independence\s*[-–]\s*Freedom\s*[-–]\s*Happiness|"
    r"(?:The\s+)?State\s+Securities\s+Commission(?:\s+of\s+Vietnam)?|"
    r"(?:The\s+)?Ho\s+Chi\s+Minh\s+Stock\s+Exchange|(?:The\s+)?Ha\s*Noi\s+Stock\s+Exchange|"
    r"Vietnam\s+Stock\s+Exchange|"
    r"Name\s+of\s+(?:listed\s+)?organization\s*:|Company(?:'s)?\s+name\s*:|"
    r"Ticker\s+symbol\s*:|Securities\s+symbol\s*:|Stock\s+code\s*:|"
    r"Head\s+office\s+address\s*:|Address\s*:|Tel\.?\s*:|Fax\s*:|E-?mail\s*:|"
    r"Contents?\s+of\s+disclosure\s*:|Information\s+disclosure\b|"
    r"This\s+information\s+was\s+published\s+on\s+the\s+company|"
    r"We\s+hereby\s+certify\s+that\s+the\s+information\s+provided|"
    r"Legal\s+representative|Authorized\s+person\s+to\s+disclose\s+information)\b",
    re.IGNORECASE,
)

_ENGLISH_SECTION_HEADING_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:consolidated\s+financial\s+statements?|separate\s+financial\s+statements?|"
    r"interim\s+financial\s+statements?|statement\s+of\s+financial\s+position|"
    r"consolidated\s+balance\s+sheet|consolidated\s+income\s+statement|"
    r"consolidated\s+cash\s+flow\s+statement|notes\s+to\s+the\s+(?:consolidated\s+)?financial\s+statements?|"
    r"independent\s+auditors?'?\s+report|report\s+of\s+the\s+board\s+of\s+(?:management|directors|general\s+directors))\s*$",
    re.IGNORECASE,
)

_PROTECTED_INLINE_TOKEN_RE = re.compile(
    r"^(?:VND|VNĐ|USD|EUR|JPY|GBP|CNY|SGD|KRW|"
    r"EV/EBITDA|EBITDA|EBIT|P/E|P/B|D/E|ROE|ROA|ROIC|NIM|CASA|NPL|CIR|CAR|LDR|EPS|BVPS|CAGR|YoY|QoQ|MoM|"
    r"FVTPL|HTM|AFS|IFRS(?:\s*\d+)?|VAS(?:\s*\d+)?|IAS(?:\s*\d+)?|BASEL\s*[IIV]+|"
    r"HOSE|HNX|UPCOM|VN30|VNINDEX|ETF|ESOP|M&A|CAPEX|OPEX|FDI|ODA|GDP|CPI|PMI|"
    r"[A-Z]{2,5}:\s*[A-Z0-9]{2,5}|\d{1,3}[a-z]?|[ivxIVX]+|\*+)$",
    re.IGNORECASE,
)

_PROPER_NAME_INTRO_VN_RE = re.compile(
    r"(?:tên\s+(?:giao\s+dịch\s+)?(?:quốc\s+tế|tiếng\s+anh|viết\s+tắt)|english\s+name)",
    re.IGNORECASE,
)
_LEGAL_ENTITY_SUFFIX_RE = re.compile(
    r"\b(?:JSC|Co\.?,?\s*Ltd\.?|Company\s+Limited|Limited|Corp\.?|Inc\.?|LLC|Plc|Pte\.?\s*Ltd\.?|GmbH)\s*\.?$",
    re.IGNORECASE,
)

EN_TRANSLATION_LEXICON: frozenset[str] = frozenset(
    {
        "the", "and", "or", "of", "for", "from", "to", "in", "on", "at", "with", "as", "by",
        "under", "during", "after", "before", "into", "between", "within", "without", "through",
        "about", "over", "above", "below", "including", "excluding", "ended", "ending", "beginning",
        "closing", "opening", "prior", "current", "previous", "total", "net", "gross", "other",
        "others", "all", "each", "any", "per", "sub", "non", "is", "are", "was", "were", "be",
        "been", "being", "have", "has", "had", "shall", "will", "may", "can", "which", "that",
        "this", "these", "those", "their", "its", "our", "hereby", "thereof", "pursuant", "accordance",
        "consolidated", "separate", "interim", "audited", "unaudited", "reviewed", "quarterly",
        "annual", "semi-annual", "financial", "statement", "statements", "position", "balance",
        "sheet", "income", "operations", "operating", "comprehensive", "cash", "flow", "flows",
        "changes", "equity", "owners", "owner", "assets", "asset", "non-current", "short-term",
        "long-term", "liabilities", "liability", "receivables", "receivable", "payables", "payable",
        "inventories", "inventory", "borrowings", "borrowing", "loans", "loan", "debt", "debts",
        "bonds", "bond", "leases", "lease", "provisions", "provision", "allowance", "allowances",
        "depreciation", "amortisation", "amortization", "goodwill", "intangible", "tangible",
        "fixed", "construction", "progress", "property", "plant", "equipment", "investment",
        "investments", "securities", "trading", "held", "maturity", "available", "sale",
        "deposits", "deposit", "equivalents", "equivalent", "hand", "bank", "banks", "customers",
        "customer", "suppliers", "supplier", "related", "parties", "party", "transactions",
        "transaction", "balances", "subsidiaries", "subsidiary", "associates", "associate",
        "prepayments", "prepaid", "accrued", "expenses", "expense", "advances", "advance",
        "pledged", "mortgaged", "collateral", "contingent", "commitments", "commitment",
        "revenue", "revenues", "sales", "goods", "rendering", "services", "service", "deductions",
        "deduction", "cost", "sold", "profit", "profits", "loss", "losses", "margin", "selling",
        "general", "administrative", "finance", "corporate", "tax", "taxes", "deferred", "earnings",
        "share", "shares", "basic", "diluted", "dividend", "dividends", "payment", "payments",
        "distribution", "appropriation", "funds", "fund", "welfare", "bonus", "charter", "capital",
        "retained", "undistributed", "treasury", "surplus", "reserve", "reserves", "exchange",
        "differences", "difference", "foreign", "currency", "translation", "shareholders",
        "shareholder", "meeting", "meetings", "resolution", "resolutions", "proposal", "proposals",
        "report", "reports", "board", "directors", "director", "management", "executive", "officer",
        "chairman", "supervisory", "committee", "audit", "auditors", "auditor", "independent",
        "notes", "note", "item", "items", "code", "codes", "unit", "million", "billion", "thousand",
        "dong", "plan", "plans", "target", "targets", "budget", "business", "results", "result",
        "explanation", "variance", "fluctuation", "compared", "period", "year", "years", "quarter",
        "activities", "activity", "governance", "internal", "control", "risk", "risks", "credit",
        "liquidity", "market", "interest", "rate", "rates", "approved", "approving", "approval",
        "implement", "implementation", "issuance", "issue", "issuing", "listing", "listed",
        "participate", "workshops", "workshop", "training", "programs", "program", "senior",
        "leadership", "member", "companies", "actively", "continued",
    }
)

_SINGLE_WORD_EN_TABLE_LABELS: frozenset[str] = frozenset(
    {
        "assets",
        "liabilities",
        "equity",
        "revenue",
        "revenues",
        "inventories",
        "receivables",
        "payables",
        "provisions",
        "borrowings",
        "goodwill",
        "total",
        "items",
        "item",
        "code",
        "codes",
        "notes",
        "note",
        "description",
        "indicators",
        "indicator",
        "particulars",
        "unit",
        "closing",
        "opening",
        "audited",
        "unaudited",
        "reviewed",
    }
)

_NUM_EXTRACT_RE = re.compile(r"(?<!\w)[+\-]?\d+(?:[.,]\d+)*%?")


@dataclass(frozen=True, slots=True)
class BilingualCleanStats:
    lines_in: int = 0
    lines_out: int = 0
    paired_english_lines_removed: int = 0
    standalone_english_lines_removed: int = 0
    inline_english_fragments_removed: int = 0
    bilingual_table_cells_cleaned: int = 0
    bilingual_table_rows_removed: int = 0
    bilingual_table_cols_removed: int = 0
    english_mirror_tables_removed: int = 0
    images_removed: int = 0
    noise_lines_removed: int = 0


def has_vietnamese_cues(text: str) -> bool:
    """True when `text` contains Vietnamese diacritics or unambiguous Vietnamese filing terms."""
    if not text:
        return False
    nfc = unicodedata.normalize("NFC", text)
    if _VIET_CHAR_RE.search(nfc):
        return True
    low = nfc.lower()
    return any(kw in low for kw in VN_UNACCENTED_TEXT_CUES)


def _normalize_number_multiset(text: str) -> Counter[str]:
    """Normalize numbers across VN (`2.000,5`) and US (`2,000.5`) formats so `2.000` == `2,000`."""
    counts: Counter[str] = Counter()
    for m in _NUM_EXTRACT_RE.finditer(text or ""):
        tok = m.group(0).strip().lstrip("+").rstrip("%")
        if "." in tok and "," in tok:
            if tok.rfind(",") > tok.rfind("."):
                tok = tok.replace(".", "").replace(",", ".")
            else:
                tok = tok.replace(",", "")
        elif re.fullmatch(r"-?\d{1,3}(?:\.\d{3})+", tok):
            tok = tok.replace(".", "")
        elif re.fullmatch(r"-?\d{1,3}(?:,\d{3})+", tok):
            tok = tok.replace(",", "")
        else:
            tok = tok.replace(",", ".")
        if tok.endswith(".0"):
            tok = tok[:-2]
        if tok:
            counts[tok] += 1
    return counts


def _numbers_are_subset(en_text: str, vi_text: str) -> bool:
    """Return True iff every numeric token in `en_text` already appears in `vi_text`."""
    en_nums = _normalize_number_multiset(en_text)
    if not en_nums:
        return True
    vi_nums = _normalize_number_multiset(vi_text)
    for num_key, cnt in en_nums.items():
        if vi_nums.get(num_key, 0) < cnt:
            return False
    return True


def is_english_translation_phrase(
    text: str,
    *,
    vi_context: str = "",
    allow_single_table_word: bool = False,
) -> bool:
    """True when `text` is a redundant English translation phrase/clause (0 VN diacritics, safe to strip)."""
    s = unicodedata.normalize("NFC", (text or "").strip())
    if not s:
        return False
    # Strip leading numbering / list / Roman prefixes (`1.`, `I.`, `A.`, `-`, `*`)
    core = re.sub(r"^(?:[#>*•\-–]+\s*|(?:[IVX]{1,4}|\d{1,2}(?:\.\d{1,2})?|[a-zA-Z])[.)]\s+)+", "", s).strip()
    core = core.strip("():;,. ")
    if not core:
        return False
    if has_vietnamese_cues(core):
        return False
    if _PROTECTED_INLINE_TOKEN_RE.fullmatch(core):
        return False
    if _SSC_ENGLISH_BOILERPLATE_RE.match(s):
        return True

    # Protect introduced international legal names (e.g. `Công ty có tên quốc tế là BAF Vietnam Agriculture JSC`)
    if vi_context:
        if core.lower() in vi_context.lower() and len(core) >= 4:
            return False
        if _PROPER_NAME_INTRO_VN_RE.search(vi_context):
            return False

    words = [w.lower() for w in _EN_WORD_RE.findall(core)]
    if not words:
        return False

    if len(words) == 1:
        return allow_single_table_word and words[0] in _SINGLE_WORD_EN_TABLE_LABELS

    # Protect standalone corporate entity names (`FPT Software`, `ABC Vietnam Co., Ltd`, `BAF Vietnam Agriculture JSC`)
    # unless they use generic statement/governance translation grammar (`of`, `for`, `statement`, `report`, ...).
    lex_hits = sum(1 for w in words if w in EN_TRANSLATION_LEXICON)
    if _LEGAL_ENTITY_SUFFIX_RE.search(core) and not any(
        w in {"of", "for", "statement", "statements", "consolidated", "report", "resolution", "meeting", "commission"}
        for w in words
    ):
        return False

    if vi_context:
        if not _numbers_are_subset(core, vi_context):
            return False
        return lex_hits >= 1 and (lex_hits >= 2 or len(words) <= 6)

    # Standalone (no adjacent VI context): require stronger English prose/accounting evidence and 0 numbers
    if _normalize_number_multiset(core):
        return False
    return len(words) >= 6 and lex_hits >= 3


def is_english_translation_label(text: str, *, vi_neighbor: str = "") -> bool:
    """Specialized predicate for table cells, subtitle rows, and parallel `Items` columns."""
    s = unicodedata.normalize("NFC", (text or "").strip())
    if not s or s in {"-", "–", "—"}:
        return False
    if has_vietnamese_cues(s):
        return False
    if _PROTECTED_INLINE_TOKEN_RE.fullmatch(s):
        return False
    # Never treat pure numeric / accounting codes / dates as English labels
    if re.fullmatch(r"[\d.,()%+\-/\s]+", s):
        return False
    return is_english_translation_phrase(
        s,
        vi_context=vi_neighbor or "Chỉ tiêu kế toán",
        allow_single_table_word=True,
    )


def strip_inline_bilingual_fragments(line: str) -> tuple[str, int]:
    """Strip inline ` / English` or `(English translation)` suffixes from a Vietnamese line or heading."""
    if not line or not has_vietnamese_cues(line):
        return line, 0

    prefix_hashes = ""
    body = line
    m_h = _HEADING_RE.match(line.strip())
    if m_h:
        prefix_hashes = f"{m_h.group(1)} "
        body = m_h.group(2)

    removed = 0

    # 1. Slash-delimited inline translation (`Doanh thu thuần / Net revenue:` or `ĐVT: Tỷ đồng / Unit: Billion VND`)
    if " / " in body:
        parts = [p.strip() for p in body.split(" / ")]
        if len(parts) >= 2 and has_vietnamese_cues(parts[0]):
            kept = [parts[0]]
            for extra in parts[1:]:
                trailing_colon = ":" if extra.endswith(":") and not kept[0].endswith(":") else ""
                clean_extra = extra.rstrip(":").strip()
                if is_english_translation_phrase(
                    clean_extra,
                    vi_context=parts[0],
                    allow_single_table_word=True,
                ):
                    removed += 1
                    if trailing_colon and not kept[-1].endswith(":"):
                        kept[-1] = f"{kept[-1]}:"
                else:
                    kept.append(extra)
            if removed > 0:
                body = " / ".join(kept)

    # 2. Trailing parenthetical English translation: `Tài sản ngắn hạn (Current assets)`
    m_paren = re.search(r"\s*\(([^()]{3,120})\)\s*(:?)\s*$", body)
    if m_paren:
        inside = m_paren.group(1).strip()
        colon = m_paren.group(2)
        left_vi = body[: m_paren.start()].strip()
        if (
            has_vietnamese_cues(left_vi)
            and not _PROTECTED_INLINE_TOKEN_RE.fullmatch(inside)
            and is_english_translation_phrase(inside, vi_context=left_vi, allow_single_table_word=True)
        ):
            body = f"{left_vi}{colon}"
            removed += 1

    # 3. Trailing sentence-boundary English mirror on prose line
    if not prefix_hashes and removed == 0:
        clauses = re.split(r"(?<=[.!?;])\s+(?=[A-Z][a-z])", body)
        if len(clauses) >= 2 and has_vietnamese_cues(clauses[0]):
            kept_c = [clauses[0]]
            for cl in clauses[1:]:
                if is_english_translation_phrase(cl, vi_context=clauses[0]):
                    removed += 1
                else:
                    kept_c.append(cl)
            if removed > 0:
                body = " ".join(kept_c).strip()

    if removed == 0:
        return line, 0
    if prefix_hashes:
        return f"{prefix_hashes}{body.strip()}", removed
    leading_ws = line[: len(line) - len(line.lstrip())]
    return f"{leading_ws}{body.strip()}", removed


def clean_bilingual_table_cell(raw_cell: str) -> tuple[str, bool]:
    """Clean a single table header or body cell containing `\n` / `<br>` stacked or inline bilingual text.

    Preserves 100% of numbers, Vietnamese labels, note codes, and legitimate proper names.
    Returns `(cleaned_cell_single_line, was_modified)`.
    """
    if not raw_cell:
        return "", False
    norm = _BR_TAG_RE.sub("\n", str(raw_cell))
    raw_sublines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in norm.splitlines() if ln.strip()]
    if not raw_sublines:
        return "", False

    modified = False
    has_vi_or_period_anchor = any(
        has_vietnamese_cues(ln) or bool(re.search(r"\b(?:19|20)\d{2}\b|\d{1,2}[./\-]\d{1,2}", ln))
        for ln in raw_sublines
    )
    # Multi-line stacked cell (`Tiền mặt \n Cash on hand` or `2025 \n Closing balance`)
    if len(raw_sublines) >= 2 and has_vi_or_period_anchor:
        vi_joined = " ".join(
            ln
            for ln in raw_sublines
            if has_vietnamese_cues(ln) or bool(re.search(r"\b(?:19|20)\d{2}\b|\d{1,2}[./\-]\d{1,2}", ln))
        )
        kept_lines: list[str] = []
        for ln in raw_sublines:
            if has_vietnamese_cues(ln):
                cleaned_ln, n_in = strip_inline_bilingual_fragments(ln)
                if n_in > 0:
                    modified = True
                kept_lines.append(cleaned_ln)
            elif is_english_translation_phrase(ln, vi_context=vi_joined, allow_single_table_word=True):
                modified = True
            else:
                kept_lines.append(ln)
        if kept_lines:
            raw_sublines = kept_lines

    joined = " ".join(raw_sublines).strip()
    cleaned_inline, n_inline = strip_inline_bilingual_fragments(joined)
    if n_inline > 0:
        modified = True
        joined = cleaned_inline
    return re.sub(r"\s+", " ", joined).strip(), modified


def _is_code_or_note_column(header: str, col_values: list[str]) -> bool:
    h_low = (header or "").strip().lower()
    if any(k in h_low for k in ("mã số", "ma so", "thuyết minh", "thuyet minh", "ghi chú", "stt", "no.")):
        return True
    non_empty = [v.strip() for v in col_values if v.strip() not in ("", "-", "–", "—")]
    if not non_empty:
        return False
    code_hits = sum(
        1
        for v in non_empty
        if re.fullmatch(r"(?:\d{1,3}(?:\.\d{1,2})?|\d{1,2}[a-z]?|[IVX]{1,4}(?:\.\d{1,2})?)", v, re.IGNORECASE)
    )
    return (code_hits / len(non_empty)) >= 0.65


def clean_bilingual_2d_grid(
    headers: list[str],
    rows: list[list[str]],
) -> tuple[list[str], list[list[str]], dict[str, int]]:
    """Clean all 3 intra-table bilingual patterns on a 2D string grid `(headers, rows)`:
    1. Stacked / inline bilingual header & label cells (`clean_bilingual_table_cell`)
    2. Parallel English translation columns (`| Chỉ tiêu | Items | Mã số | ... |`)
    3. Paired empty-value English subtitle rows underneath Vietnamese data rows
    """
    stats = {"cells_cleaned": 0, "cols_removed": 0, "rows_removed": 0}
    if not headers or not rows:
        return headers, rows, stats

    n_cols = len(headers)
    norm_rows = [
        [r[c] if c < len(r) else "" for c in range(n_cols)]
        for r in rows
    ]

    # Step 1: Clean bilingual headers and non-numeric cells
    clean_headers: list[str] = []
    for h in headers:
        ch, mod = clean_bilingual_table_cell(h)
        if mod:
            stats["cells_cleaned"] += 1
        clean_headers.append(ch if ch else h)

    for r in norm_rows:
        for c_idx in range(min(2, n_cols)):
            cell_val = r[c_idx]
            if cell_val and has_vietnamese_cues(cell_val):
                cc, mod = clean_bilingual_table_cell(cell_val)
                if mod:
                    stats["cells_cleaned"] += 1
                    r[c_idx] = cc

    # Step 2: Detect and drop parallel English translation column(s) (`Items` / `Description` / `English`)
    if n_cols >= 3:
        col0_non_empty = [r[0].strip() for r in norm_rows if r[0].strip()]
        col0_vi_ratio = (
            sum(1 for v in col0_non_empty if has_vietnamese_cues(v)) / max(1, len(col0_non_empty))
        )
        drop_cols: set[int] = set()
        if col0_vi_ratio >= 0.35:
            for c_idx in range(1, n_cols - 1):
                col_vals = [r[c_idx].strip() for r in norm_rows]
                non_empty = [v for v in col_vals if v and v not in ("-", "–", "—")]
                if len(non_empty) < 2:
                    continue
                if _is_code_or_note_column(clean_headers[c_idx], col_vals):
                    continue
                num_like = sum(1 for v in non_empty if re.fullmatch(r"\(?[+\-]?[\d.,]+%?\)?", v))
                if (num_like / len(non_empty)) >= 0.10:
                    continue
                en_hits = sum(
                    1
                    for r_idx, r in enumerate(norm_rows)
                    if r[c_idx].strip()
                    and is_english_translation_label(r[c_idx].strip(), vi_neighbor=r[0])
                )
                if (en_hits / len(non_empty)) >= 0.70:
                    drop_cols.add(c_idx)

        if drop_cols:
            clean_headers = [h for i, h in enumerate(clean_headers) if i not in drop_cols]
            norm_rows = [[c for i, c in enumerate(r) if i not in drop_cols] for r in norm_rows]
            stats["cols_removed"] += len(drop_cols)
            n_cols = len(clean_headers)

    # Step 3: Drop paired empty-numeric English subtitle rows right under a Vietnamese row
    kept_rows: list[list[str]] = []
    for r in norm_rows:
        lbl = (r[0] or "").strip()
        rest_non_empty = [
            c.strip() for c in r[1:] if c and c.strip() not in ("", "-", "–", "—")
        ]
        if (
            not rest_non_empty
            and kept_rows
            and has_vietnamese_cues(kept_rows[-1][0])
            and is_english_translation_label(lbl, vi_neighbor=kept_rows[-1][0])
        ):
            stats["rows_removed"] += 1
            continue
        # Normalize any remaining raw newlines inside cells to single spaces
        kept_rows.append([re.sub(r"\s+", " ", c or "").strip() for c in r])

    return clean_headers, kept_rows, stats


def compute_table_numeric_fingerprint(rows: Sequence[Sequence[Any]]) -> tuple[Counter[str], float, float]:
    """Return `(accounting_number_multiset, vi_label_ratio, en_label_ratio)` for mirror-table deduplication."""
    fp: Counter[str] = Counter()
    vi_labels = 0
    en_labels = 0
    total_labels = 0

    for row in rows:
        if not row:
            continue
        first = row[0]
        lbl = str(first.get("raw_text") if isinstance(first, Mapping) else first or "").strip()
        if lbl and lbl not in ("-", "–", "—"):
            total_labels += 1
            if has_vietnamese_cues(lbl):
                vi_labels += 1
            elif is_english_translation_label(lbl):
                en_labels += 1
        for cell in row[1:]:
            if isinstance(cell, Mapping):
                nv = cell.get("numeric_value")
                raw = str(cell.get("raw_text") or "").strip()
            else:
                nv = None
                raw = str(cell or "").strip()
            if nv is not None:
                # Skip small 1–3 digit accounting codes (100, 110) and standalone years (1990..2035)
                if float(nv).is_integer() and (0 <= abs(int(nv)) <= 450 or 1990 <= int(nv) <= 2035):
                    continue
                fp[f"{round(float(nv), 2):.2f}"] += 1
            elif raw:
                for k, cnt in _normalize_number_multiset(raw).items():
                    try:
                        fv = float(k)
                    except ValueError:
                        continue
                    if fv.is_integer() and (0 <= abs(int(fv)) <= 450 or 1990 <= int(fv) <= 2035):
                        continue
                    fp[f"{round(fv, 2):.2f}"] += cnt

    denom = max(1, total_labels)
    return fp, (vi_labels / denom), (en_labels / denom)


def deduplicate_english_mirror_tables(
    tables: list[Any],
    *,
    max_page_distance: int = 2,
) -> tuple[list[Any], int]:
    """Drop full-English mirror tables whose accounting numbers duplicate an adjacent Vietnamese table."""
    if len(tables) < 2:
        return tables, 0

    meta: list[tuple[Counter[str], float, float, int, int]] = []
    for tbl in tables:
        if isinstance(tbl, Mapping):
            rows = tbl.get("rows") or []
            p_from = int(tbl.get("page_from") or 1)
            p_to = int(tbl.get("page_to") or p_from)
        else:
            rows = getattr(tbl, "rows", None) or []
            p_from = int(getattr(tbl, "page_from", 1) or 1)
            p_to = int(getattr(tbl, "page_to", p_from) or p_from)
        fp, vi_r, en_r = compute_table_numeric_fingerprint(rows)
        meta.append((fp, vi_r, en_r, p_from, p_to))

    drop_indices: set[int] = set()
    for idx_en, (fp_en, vi_en, en_ratio, pf_en, pt_en) in enumerate(meta):
        if vi_en >= 0.08 or en_ratio < 0.45 or sum(fp_en.values()) < 4:
            continue
        for idx_vi, (fp_vi, vi_ratio, _er_vi, pf_vi, pt_vi) in enumerate(meta):
            if idx_vi == idx_en or vi_ratio < 0.30 or sum(fp_vi.values()) < 4:
                continue
            page_gap = min(abs(pf_en - pt_vi), abs(pf_vi - pt_en), abs(pf_en - pf_vi))
            if page_gap > max_page_distance:
                continue
            shared = sum(min(cnt, fp_vi.get(k, 0)) for k, cnt in fp_en.items())
            smaller = min(sum(fp_en.values()), sum(fp_vi.values()))
            if smaller >= 4 and (shared / smaller) >= 0.80:
                drop_indices.add(idx_en)
                break

    if not drop_indices:
        return tables, 0
    return [t for i, t in enumerate(tables) if i not in drop_indices], len(drop_indices)


def _clean_html_table_in_place(html_table: str) -> tuple[str, int, int]:
    """Clean bilingual `<br>` cells, parallel `Items` columns, and subtitle `<tr>` rows in HTML `<table>`."""
    cells_cleaned = 0
    rows_removed = 0
    tr_matches = list(_HTML_TR_RE.finditer(html_table))
    if not tr_matches:
        return html_table, 0, 0

    # Detect parallel English translation column(s) when rows have uniform cell counts (no colspan/rowspan)
    has_span_attr = bool(re.search(r"\b(?:colspan|rowspan)\s*=", html_table, re.IGNORECASE))
    parsed_tr_cells: list[list[re.Match[str]]] = [
        list(_HTML_CELL_RE.finditer(tm.group(0))) for tm in tr_matches
    ]
    col_counts = {len(cm_list) for cm_list in parsed_tr_cells if cm_list}
    drop_html_cols: set[int] = set()
    if not has_span_attr and len(col_counts) == 1 and len(parsed_tr_cells) >= 2:
        n_cols = next(iter(col_counts))
        if n_cols >= 3:
            plain_matrix = [
                [
                    clean_bilingual_table_cell(_ANY_TAG_RE.sub("", _BR_TAG_RE.sub("\n", cm.group(2))))[0]
                    for cm in cm_list
                ]
                for cm_list in parsed_tr_cells
                if cm_list
            ]
            body_rows = plain_matrix[1:]
            col0_vi = sum(1 for r in body_rows if has_vietnamese_cues(r[0])) / max(1, len(body_rows))
            if col0_vi >= 0.35:
                for c_idx in range(1, n_cols - 1):
                    col_vals = [r[c_idx] for r in body_rows]
                    non_empty = [v for v in col_vals if v and v not in ("-", "–", "—")]
                    if len(non_empty) < 2 or _is_code_or_note_column(plain_matrix[0][c_idx], col_vals):
                        continue
                    num_like = sum(1 for v in non_empty if re.fullmatch(r"\(?[+\-]?[\d.,]+%?\)?", v))
                    if (num_like / len(non_empty)) >= 0.10:
                        continue
                    en_hits = sum(
                        1
                        for r in body_rows
                        if r[c_idx] and is_english_translation_label(r[c_idx], vi_neighbor=r[0])
                    )
                    if (en_hits / len(non_empty)) >= 0.70:
                        drop_html_cols.add(c_idx)

    kept_trs: list[str] = []
    prev_tr_had_vi = False

    for tr_idx, tr_match in enumerate(tr_matches):
        tr_html = tr_match.group(0)
        cell_matches = parsed_tr_cells[tr_idx]
        if not cell_matches:
            kept_trs.append(tr_html)
            continue

        plain_cells = [
            re.sub(r"\s+", " ", _ANY_TAG_RE.sub("", _BR_TAG_RE.sub("\n", cm.group(2)))).strip()
            for idx_c, cm in enumerate(cell_matches)
            if idx_c not in drop_html_cols
        ]
        rest_non_empty = [c for c in plain_cells[1:] if c and c not in ("-", "–", "—")]
        if (
            len(plain_cells) >= 2
            and not rest_non_empty
            and prev_tr_had_vi
            and is_english_translation_label(plain_cells[0])
        ):
            rows_removed += 1
            continue

        rebuilt_tr_parts: list[str] = []
        cursor = 0
        for idx_c, cm in enumerate(cell_matches):
            rebuilt_tr_parts.append(tr_html[cursor : cm.start()])
            cursor = cm.end()
            if idx_c in drop_html_cols:
                cells_cleaned += 1
                continue
            open_tag, inner, close_tag = cm.group(1), cm.group(2), cm.group(3)
            plain_with_nl = _ANY_TAG_RE.sub("", _BR_TAG_RE.sub("\n", inner))
            cleaned, mod = clean_bilingual_table_cell(plain_with_nl)
            if mod:
                cells_cleaned += 1
                rebuilt_tr_parts.append(f"{open_tag}{cleaned}{close_tag}")
            else:
                rebuilt_tr_parts.append(cm.group(0))
        rebuilt_tr_parts.append(tr_html[cursor:])
        new_tr = "".join(rebuilt_tr_parts)
        prev_tr_had_vi = any(has_vietnamese_cues(c) for c in plain_cells)
        kept_trs.append(new_tr)

    if cells_cleaned == 0 and rows_removed == 0 and not drop_html_cols:
        return html_table, 0, 0
    first_tr = tr_matches[0]
    last_tr = tr_matches[-1]
    rebuilt = html_table[: first_tr.start()] + "\n".join(kept_trs) + html_table[last_tr.end() :]
    return rebuilt, cells_cleaned, rows_removed


def _clean_gfm_markdown_table_block(table_lines: list[str]) -> tuple[list[str], dict[str, int]]:
    """Clean a GFM pipe table (`| ... |`) using `clean_bilingual_2d_grid`."""
    zero_stats = {"cells_cleaned": 0, "cols_removed": 0, "rows_removed": 0}
    if len(table_lines) < 2:
        return table_lines, zero_stats

    parsed_rows: list[list[str]] = []
    sep_idx: int | None = None
    for idx, ln in enumerate(table_lines):
        s = ln.strip()
        inner = s[1:-1] if s.startswith("|") and s.endswith("|") else s.strip("|")
        cells = [c.strip() for c in inner.split("|")]
        if idx == 1 and all(re.fullmatch(r":?-{2,}:?", c or "---") for c in cells):
            sep_idx = idx
            continue
        parsed_rows.append(cells)

    if len(parsed_rows) < 2 or sep_idx is None:
        return table_lines, zero_stats

    max_c = max(len(r) for r in parsed_rows)
    padded = [r + [""] * (max_c - len(r)) for r in parsed_rows]
    headers, body, st = clean_bilingual_2d_grid(padded[0], padded[1:])
    if st["cells_cleaned"] == 0 and st["cols_removed"] == 0 and st["rows_removed"] == 0:
        return table_lines, st

    out = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join(["---"] * len(headers)) + " |",
    ]
    for r in body:
        out.append("| " + " | ".join(r) + " |")
    return out, st


def clean_bilingual_markdown(markdown: str) -> tuple[str, BilingualCleanStats]:
    """Clean post-OCR / digital Vietnamese filing Markdown while preserving `<!-- PAGE N -->` anchors."""
    if not markdown:
        return "", BilingualCleanStats()

    stats = BilingualCleanStats()

    # 1. Clean HTML `<table>...</table>` blocks in place (preserving `<table>` for `table_engine.py`)
    if "<table" in markdown.lower():
        html_blocks: list[tuple[tuple[int, int], str, list[list[str]]]] = []
        cell_delta = 0
        row_delta = 0

        def _clean_html_match(m: re.Match[str]) -> str:
            nonlocal cell_delta, row_delta
            cleaned_tbl, cc, rr = _clean_html_table_in_place(m.group(0))
            cell_delta += cc
            row_delta += rr
            return cleaned_tbl

        markdown = _HTML_TABLE_BLOCK_RE.sub(_clean_html_match, markdown)
        stats = replace(
            stats,
            bilingual_table_cells_cleaned=stats.bilingual_table_cells_cleaned + cell_delta,
            bilingual_table_rows_removed=stats.bilingual_table_rows_removed + row_delta,
        )

        # Deduplicate back-to-back English mirror HTML tables inside the same document
        matches = list(_HTML_TABLE_BLOCK_RE.finditer(markdown))
        if len(matches) >= 2:
            for m in matches:
                tr_grid: list[list[str]] = []
                for tr_m in _HTML_TR_RE.finditer(m.group(0)):
                    cells = [
                        re.sub(r"\s+", " ", _ANY_TAG_RE.sub("", cm.group(2))).strip()
                        for cm in _HTML_CELL_RE.finditer(tr_m.group(0))
                    ]
                    if any(cells):
                        tr_grid.append(cells)
                html_blocks.append(((m.start(), m.end()), m.group(0), tr_grid))

            # Track approximate page number from preceding `<!-- PAGE N -->`
            tbl_dicts: list[dict[str, Any]] = []
            for (st_pos, _en_pos), _raw_h, grid in html_blocks:
                page_m = list(re.finditer(r"<!--\s*PAGE\s+(\d+)\s*-->", markdown[:st_pos], re.IGNORECASE))
                pno = int(page_m[-1].group(1)) if page_m else 1
                tbl_dicts.append({"rows": grid[1:] if len(grid) > 1 else grid, "page_from": pno, "page_to": pno})
            kept_dicts, n_mirror = deduplicate_english_mirror_tables(tbl_dicts)
            if n_mirror > 0:
                kept_ids = {id(d) for d in kept_dicts}
                rebuilt_parts: list[str] = []
                cursor = 0
                for idx_b, ((st_pos, en_pos), _raw_h, _grid) in enumerate(html_blocks):
                    rebuilt_parts.append(markdown[cursor:st_pos])
                    if id(tbl_dicts[idx_b]) in kept_ids:
                        rebuilt_parts.append(markdown[st_pos:en_pos])
                    cursor = en_pos
                rebuilt_parts.append(markdown[cursor:])
                markdown = "".join(rebuilt_parts)
                stats = replace(stats, english_mirror_tables_removed=stats.english_mirror_tables_removed + n_mirror)

    raw_lines = markdown.splitlines()
    stats = replace(stats, lines_in=len(raw_lines))

    # 2. Clean GFM pipe table blocks (`| ... |`) first so prose rules never break table structure
    stage_lines: list[str] = []
    idx = 0
    while idx < len(raw_lines):
        s = raw_lines[idx].strip()
        if s.startswith("|") and s.count("|") >= 2:
            tbl_buf: list[str] = []
            while idx < len(raw_lines) and raw_lines[idx].strip().startswith("|"):
                tbl_buf.append(raw_lines[idx])
                idx += 1
            cleaned_tbl_lines, gfm_st = _clean_gfm_markdown_table_block(tbl_buf)
            stage_lines.extend(cleaned_tbl_lines)
            stats = replace(
                stats,
                bilingual_table_cells_cleaned=stats.bilingual_table_cells_cleaned + gfm_st["cells_cleaned"],
                bilingual_table_cols_removed=stats.bilingual_table_cols_removed + gfm_st["cols_removed"],
                bilingual_table_rows_removed=stats.bilingual_table_rows_removed + gfm_st["rows_removed"],
            )
            continue
        stage_lines.append(raw_lines[idx])
        idx += 1

    # 3. Line-by-line & section-level bilingual / noise cleaning (preserving `<!-- PAGE N -->` anchors!)
    out: list[str] = []
    paired_removed = 0
    standalone_removed = 0
    inline_removed = 0
    images_removed = 0
    noise_removed = 0
    in_html_table = False
    english_section_level: int | None = None
    last_non_empty_vi_line = ""
    blank_gap_since_vi = 0

    for line in stage_lines:
        stripped = line.strip()

        # Always preserve `<!-- PAGE N -->` anchors untouched!
        if _PAGE_MARKER_LINE_RE.match(stripped):
            english_section_level = None
            out.append(stripped)
            continue

        # Pass through HTML `<table>...</table>` interiors untouched (already cleaned in step 1)
        if "<table" in stripped.lower():
            in_html_table = "</table" not in stripped.lower()
            out.append(line)
            continue
        if in_html_table:
            if "</table" in stripped.lower():
                in_html_table = False
            out.append(line)
            continue

        if not stripped:
            blank_gap_since_vi += 1
            out.append("")
            continue

        # Strip MinerU CDN / inline markdown images
        if _IMAGE_RE.search(line):
            img_hits = len(_IMAGE_RE.findall(line))
            images_removed += img_hits
            line = re.sub(r"[ \t]{2,}", " ", _IMAGE_RE.sub("", line)).strip()
            stripped = line.strip()
            if not stripped:
                continue

        # Strip pipe-table rows (`| ... |`) pass-through
        if stripped.startswith("|") and stripped.count("|") >= 2:
            out.append(line)
            last_non_empty_vi_line = stripped if has_vietnamese_cues(stripped) else ""
            blank_gap_since_vi = 0
            continue

        # Strip OCR LaTeX hallucinations & audit stamp fragments
        if _LATEX_JUNK_RE.search(stripped) and not has_vietnamese_cues(stripped):
            noise_removed += 1
            continue
        if _STAMP_SYMBOLS_RE.match(stripped) or _STAMP_NOISE_RE.match(stripped):
            noise_removed += 1
            continue

        heading_m = _HEADING_RE.match(stripped)
        if english_section_level is not None:
            if heading_m and len(heading_m.group(1)) <= english_section_level and has_vietnamese_cues(stripped):
                english_section_level = None
            elif has_vietnamese_cues(stripped):
                english_section_level = None
            else:
                standalone_removed += 1
                continue

        if _ENGLISH_SECTION_HEADING_RE.match(stripped) and last_non_empty_vi_line:
            english_section_level = len(heading_m.group(1)) if heading_m else 1
            standalone_removed += 1
            continue

        if _SSC_ENGLISH_BOILERPLATE_RE.match(stripped):
            standalone_removed += 1
            continue

        # Paired-Line Mirror Check: `Line i` was Vietnamese (adjacent or separated by <= 1 blank line)
        # Works for headings (`#`), bullets (`-`, `*`, `1.`), blockquotes (`>`), and short 2-7 word translations!
        if last_non_empty_vi_line and blank_gap_since_vi <= 1 and not has_vietnamese_cues(stripped):
            # If current line is a heading, require previous VI line to also be a heading (same or parent level)
            can_pair = True
            if heading_m:
                prev_h = _HEADING_RE.match(last_non_empty_vi_line)
                can_pair = prev_h is not None and len(prev_h.group(1)) == len(heading_m.group(1))
            if can_pair and is_english_translation_phrase(stripped, vi_context=last_non_empty_vi_line):
                paired_removed += 1
                # Reset so two consecutive lines aren't chained blindly unless another VI line appears
                last_non_empty_vi_line = ""
                continue

        # Standalone English paragraph check (unpaired >= 6 English words with 0 VN cues and 0 numbers)
        if not has_vietnamese_cues(stripped) and not heading_m:
            body_no_bullet = _LIST_PREFIX_RE.sub(r"\2", stripped)
            if is_english_translation_phrase(body_no_bullet, vi_context=""):
                standalone_removed += 1
                continue

        # Inline slash / parenthetical bilingual stripping on Vietnamese lines
        cleaned_line, n_in = strip_inline_bilingual_fragments(line)
        if n_in > 0:
            inline_removed += n_in
            line = cleaned_line
            stripped = line.strip()

        out.append(line)
        if has_vietnamese_cues(stripped):
            last_non_empty_vi_line = stripped
            blank_gap_since_vi = 0
        else:
            last_non_empty_vi_line = ""
            blank_gap_since_vi = 0

    # Collapse 3+ consecutive blank lines into at most 1 blank line
    collapsed: list[str] = []
    blank_run = 0
    for ln in out:
        if not ln.strip():
            blank_run += 1
            if blank_run <= 1 and collapsed:
                collapsed.append("")
        else:
            blank_run = 0
            collapsed.append(ln)
    while collapsed and not collapsed[-1].strip():
        collapsed.pop()

    final_md = "\n".join(collapsed)
    stats = replace(
        stats,
        lines_out=len(collapsed),
        paired_english_lines_removed=paired_removed,
        standalone_english_lines_removed=standalone_removed,
        inline_english_fragments_removed=inline_removed,
        images_removed=images_removed,
        noise_lines_removed=noise_removed,
    )
    return final_md, stats
