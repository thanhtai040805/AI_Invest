"""Dual-Representation Financial Table Engine (0 LLM).

Productionizes Stage 1 `block1_input_and_table_engine.py` & `PROJECT.md` Contracts 1 & 4:
- `pdfplumber` lattice + coordinate-aware reconstructor fallback (never drops right numeric column)
- MinerU HTML `<table>` 2D Grid Matrix Unmerge (`rowspan`/`colspan`, `<br/>`, hierarchical `Parent :: Child`)
- Cross-page `(Tiếp theo)` / `(Continued)` table stitcher with header/carryover stripping & row alignment
- Statistical VN/US locale detector & multi-line unit/currency binding (`tỷ đồng`, `triệu đồng`, `USD`, etc.)
- Arithmetic & Hierarchical Sanity Gate (`<= 0.5%` tolerance; fail-closed `TABLE_UNVERIFIED`)
- Dual Representation:
  - Representation A: Full 2D `sag_extracted_tables` persistence & Contract 4 `HydratedTable` JSON
  - Representation B: `Row-Linearized Search Cards` with `table_id`, `sub_doc_slot`, `fiscal_year`, `fiscal_quarter`
- Async repository abstraction (`TableRepositoryProtocol`, `SqlTableRepository`, `InMemoryTableRepository`) with PIT.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.research.input_inspector import (
    normalize_ctg_font_quirks,
    normalize_ws,
    split_virtual_pages,
)
from sag_api.research.vn_bilingual_cleaner import (
    clean_bilingual_2d_grid,
    clean_bilingual_table_cell,
    deduplicate_english_mirror_tables,
    has_vietnamese_cues,
    is_english_translation_label,
)

VerificationStatus = Literal["VERIFIED", "PARTIAL", "TABLE_UNVERIFIED"]

_NUM_TOKEN_RE = re.compile(r"^\(?-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,4})?\)?%?$")
_TABLE_COL_HEADER_RE = re.compile(
    r"^(?:chỉ\s+tiêu|mã\s+số|thuyết\s+minh|ghi\s+chú|stt|đơn\s+vị(?:\s+tính)?|"
    r"số\s+cuối\s+(?:năm|kỳ)|số\s+đầu\s+(?:năm|kỳ)|năm\s+nay|năm\s+trước|kỳ\s+này|kỳ\s+trước|"
    r"mẫu\s+số|bảng\s+cân\s+đối|báo\s+cáo\s+kết\s+quả|báo\s+cáo\s+lưu\s+chuyển|"
    r"31[./]12[./]20\d\d|20\d\d(?:\s+20\d\d)*)\b",
    re.IGNORECASE,
)
_VN_WRAP_CONNECTORS = frozenset(
    {
        "tại",
        "của",
        "và",
        "từ",
        "đến",
        "trong",
        "trước",
        "sau",
        "về",
        "cho",
        "theo",
        "bằng",
        "với",
        "trên",
        "dưới",
        "các",
        "những",
    }
)
_RIGHT_NOTE_REF_RE = re.compile(r"^\d{1,2}(?:\([a-z]\))?$", re.IGNORECASE)
_FOOTER_OR_SIG_RE = re.compile(
    r"(?:báo cáo thường niên|mẫu số|trang\s+\d+|người lập|người được|người đại diện|"
    r"tổng giám đốc|kế toán trưởng|ngày\s+\d+\s+tháng|bản lĩnh|kiến tạo|thuyết minh báo cáo|"
    r"phần\s+\d+|đơn vị\s*:?)",
    re.IGNORECASE,
)


def detect_table_locale(raw_cells: list[str]) -> Literal["VN", "US"]:
    """Table-level statistical locale detector: 'VN' (dot thousands) vs 'US' (comma thousands)."""
    vn_votes = 0
    us_votes = 0
    for c in raw_cells:
        s = (c or "").strip().strip("()%-")
        if re.match(r"^\d{1,3}(\.\d{3})+(,\d{1,4})?$", s):
            vn_votes += 2
        elif re.match(r"^\d{1,3}(,\d{3})+(\.\d{1,4})?$", s):
            us_votes += 2
        elif re.match(r"^\d+,\d{1,2}$", s):
            vn_votes += 1
        elif re.match(r"^\d+\.\d{1,2}$", s):
            us_votes += 1
    return "US" if us_votes > vn_votes else "VN"


def parse_vn_number(raw: str | None, locale: str = "VN") -> float | None:
    """Parse Vietnamese/accounting formatted numbers: (56.500) -> -56500.0; +12,5% -> 12.5; '-' -> None."""
    if raw is None:
        return None
    s = normalize_ws(str(raw))
    if not s or s in {"-", "–", "—", "n/a", "N/A", "nil", "."}:
        return None
    is_pct = s.endswith("%")
    if is_pct:
        s = s[:-1].strip()
    is_neg = False
    if s.startswith("(") and s.endswith(")"):
        is_neg = True
        s = s[1:-1].strip()
    elif s.startswith("-"):
        is_neg = True
        s = s[1:].strip()
    elif s.startswith("+"):
        s = s[1:].strip()
    s = s.replace(" ", "")
    if not s or not re.match(r"^[\d.,]+$", s):
        return None

    if locale == "VN":
        if "." in s and "," in s:
            s = s.replace(".", "").replace(",", ".")
        elif re.match(r"^\d{1,3}(\.\d{3})+$", s):
            s = s.replace(".", "")
        elif "," in s:
            if (not is_pct) and (not s.startswith("0,")) and re.match(r"^\d{1,3}(,\d{3}){2,}$", s):
                s = s.replace(",", "")
            else:
                s = s.replace(",", ".")
    else:
        if ("," in s and "." in s) or (
            re.match(r"^\d{1,3}(,\d{3})+$", s) and (not is_pct) and (not s.startswith("0,"))
        ):
            s = s.replace(",", "")
        elif "," in s and "." not in s:
            s = s.replace(",", ".")

    try:
        val = float(s)
    except ValueError:
        return None
    return -val if is_neg else val


_EXPLICIT_UNIT_LINE_RE = re.compile(
    r"(?:đơn\s*vị(?:\s*tính)?|đvt|unit)\s*[:\-]?\s*([^\n|]{1,48})"
    r"|\(\s*((?:tỷ|tỉ|triệu|nghìn|ngàn|million|thousand|billion)?\s*(?:đồng|vnd|vnđ|usd))\s*\)",
    re.IGNORECASE,
)
_NON_MONETARY_TABLE_CUE_RE = re.compile(
    r"(?:số\s+lượng\s+cổ\s+phiếu|số\s+cổ\s+phần|cổ\s+phiếu\s+đang\s+lưu\s+hành|"
    r"tỷ\s+lệ\s+sở\s+hữu|tỷ\s+lệ\s+biểu\s+quyết|cơ\s+cấu\s+cổ\s+đông|"
    r"số\s+lượng\s+nhân\s+sự|nhân\s+viên|lao\s+động|cổ\s+phần\s*\()",
    re.IGNORECASE,
)


def _match_unit_from_fragment(low: str) -> tuple[str, float, str] | None:
    if re.search(r"triệu\s*usd|million\s*usd", low):
        return ("USD", 1e6, "triệu USD")
    if re.search(r"nghìn\s*usd|thousand\s*usd", low):
        return ("USD", 1e3, "nghìn USD")
    if re.search(r"(?:^|[:\-(\s])usd\b", low):
        return ("USD", 1.0, "USD")
    if re.search(r"(?:tỷ|tỉ)\s*(?:đồng|vnd|vnđ)", low):
        return ("VND", 1e9, "tỷ đồng")
    if re.search(r"triệu\s*(?:đồng|vnd|vnđ)", low):
        return ("VND", 1e6, "triệu đồng")
    if re.search(r"(?:nghìn|ngàn)\s*(?:đồng|vnd|vnđ)", low):
        return ("VND", 1e3, "nghìn đồng")
    if re.search(r"(?:đồng|vnd|vnđ)", low):
        return ("VND", 1.0, "VND")
    return None


def detect_explicit_unit_declaration(context_text: str) -> tuple[str, float, str]:
    """Detect ONLY explicit formal unit declarations (`Đơn vị tính: ...`, `ĐVT: ...`, `(tỷ đồng)`).

    Never matches narrative prose mentions such as '1.300 triệu USD doanh thu CNTT nước ngoài'.
    """
    cleaned = normalize_ctg_font_quirks(context_text)
    for m in _EXPLICIT_UNIT_LINE_RE.finditer(cleaned):
        frag = (m.group(1) or m.group(2) or "").lower().strip()
        if not frag:
            continue
        parsed = _match_unit_from_fragment(frag)
        if parsed is not None:
            return parsed
    return ("VND", 1.0, "unspecified")


def _table_is_non_monetary(tbl: Mapping[str, Any]) -> bool:
    """Detect share-count / ownership-% / headcount tables so monetary units never bleed onto them."""
    parts: list[str] = []
    for h in tbl.get("headers") or ():
        if isinstance(h, list):
            parts.extend(str(c or "") for c in h)
        else:
            parts.append(str(h or ""))
    for row in (tbl.get("rows") or ())[:6]:
        if isinstance(row, list) and row:
            first = row[0]
            parts.append(str(first.get("raw_text") if isinstance(first, Mapping) else first))
        elif isinstance(row, Mapping):
            parts.append(str(row.get("label") or ""))
    blob = " ".join(parts)
    if not blob.strip():
        return False
    # If the table itself explicitly declares a currency unit in its headers, it IS monetary
    if detect_explicit_unit_declaration(blob)[2] != "unspecified":
        return False
    return bool(_NON_MONETARY_TABLE_CUE_RE.search(blob))


_NARRATIVE_AMOUNT_UNIT_RE = re.compile(
    r"\d[\d.,]*[ \t]*(?:tỷ|tỉ|triệu|nghìn|ngàn|million|thousand|billion)[ \t]+(?:đồng|vnd|vnđ|usd)\b",
    re.IGNORECASE,
)


def detect_unit_and_currency(context_text: str) -> tuple[str, float, str]:
    """Scan lines above table / top-right header for (currency, unit_multiplier, unit_raw)."""
    explicit = detect_explicit_unit_declaration(context_text)
    if explicit[2] != "unspecified":
        return explicit

    cleaned = normalize_ctg_font_quirks(context_text)
    # Strip numeric narrative amounts (e.g. '1.300 triệu USD', '500 tỷ đồng') so prose figures
    # never masquerade as a table unit declaration.
    low = _NARRATIVE_AMOUNT_UNIT_RE.sub(" ", cleaned.lower())
    if re.search(r"triệu\s*usd|million\s*usd", low):
        return ("USD", 1e6, "triệu USD")
    if re.search(r"nghìn\s*usd|thousand\s*usd", low):
        return ("USD", 1e3, "nghìn USD")
    if re.search(r"đơn\s*vị(?:\s*tính)?\s*[:\-]?\s*usd\b|\(\s*usd\s*\)", low):
        return ("USD", 1.0, "USD")

    if re.search(r"(?:tỷ|tỉ)\s*(?:đồng|vnd|vnđ)", low):
        return ("VND", 1e9, "tỷ đồng")
    if re.search(r"triệu\s*(?:đồng|vnd|vnđ)", low):
        return ("VND", 1e6, "triệu đồng")
    if re.search(r"(?:nghìn|ngàn)\s*(?:đồng|vnd|vnđ)", low):
        return ("VND", 1e3, "nghìn đồng")
    if re.search(
        r"(?:(?:đơn\s*vị(?:\s*tính)?|đvt)\s*[:\-]?\s*(?:đồng|vnd|vnđ)|"
        r"\(đơn\s*vị\s*:\s*(?:đồng|vnd|vnđ)\)|^\s*(?:vnd|vnđ)\s*$|\bvnd\s+vnd\b)",
        low,
        flags=re.MULTILINE,
    ):
        return ("VND", 1.0, "VND")
    return ("VND", 1.0, "unspecified")


def _safe_span(raw_val: str | None) -> int:
    try:
        v = int(str(raw_val or "1").strip())
        return max(1, min(v, 50))
    except (ValueError, TypeError):
        return 1


def unmerge_html_table(html_str: str) -> tuple[list[str], list[list[str]]]:
    """Full 2D Grid Matrix Unmerge for MinerU HTML <table> with rowspan/colspan -> hierarchical col_path."""

    class _Parser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.rows: list[list[tuple[str, int, int, bool]]] = []
            self._cur_row: list[tuple[str, int, int, bool]] | None = None
            self._in_cell = False
            self._cell_buf: list[str] = []
            self._rs = 1
            self._cs = 1
            self._is_th = False
            self._in_thead = False

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            ad = dict(attrs)
            t = tag.lower()
            if t == "thead":
                self._in_thead = True
            elif t == "tr":
                self._cur_row = []
            elif t in ("td", "th") and self._cur_row is not None:
                self._in_cell = True
                self._cell_buf = []
                self._rs = _safe_span(ad.get("rowspan"))
                self._cs = _safe_span(ad.get("colspan"))
                self._is_th = (t == "th") or self._in_thead
            elif t == "br" and self._in_cell:
                self._cell_buf.append("\n")

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() == "br" and self._in_cell:
                self._cell_buf.append("\n")

        def handle_data(self, data: str) -> None:
            if self._in_cell:
                self._cell_buf.append(data)

        def handle_endtag(self, tag: str) -> None:
            t = tag.lower()
            if t == "thead":
                self._in_thead = False
            elif t in ("td", "th") and self._in_cell and self._cur_row is not None:
                raw_cell = normalize_ctg_font_quirks("".join(self._cell_buf))
                cleaned_cell, _ = clean_bilingual_table_cell(raw_cell)
                self._cur_row.append(
                    (normalize_ws(cleaned_cell), self._rs, self._cs, self._is_th)
                )
                self._in_cell = False
            elif t == "tr" and self._cur_row is not None:
                if self._cur_row:
                    self.rows.append(self._cur_row)
                self._cur_row = None

    p = _Parser()
    p.feed(html_str)
    if not p.rows:
        return [], []

    grid: list[list[str | None]] = []
    header_row_flags: list[bool] = []
    row_max_rowspan: list[int] = []
    row_has_colspan: list[bool] = []
    for r_idx, raw_row in enumerate(p.rows):
        while len(grid) <= r_idx:
            grid.append([])
        c_idx = 0
        row_is_hdr = all(is_th for _, _, _, is_th in raw_row)
        header_row_flags.append(row_is_hdr)
        row_max_rowspan.append(max((rs for _, rs, _, _ in raw_row), default=1))
        row_has_colspan.append(any(cs > 1 for _, _, cs, _ in raw_row))
        for cell_text, rs, cs, _ in raw_row:
            while c_idx < len(grid[r_idx]) and grid[r_idx][c_idx] is not None:
                c_idx += 1
            for dr in range(rs):
                while len(grid) <= r_idx + dr:
                    grid.append([])
                while len(grid[r_idx + dr]) < c_idx + cs:
                    grid[r_idx + dr].append(None)
                for dc in range(cs):
                    grid[r_idx + dr][c_idx + dc] = cell_text
            c_idx += cs

    max_cols = max(len(r) for r in grid)
    norm_grid = [
        [(r[c] if c < len(r) and r[c] is not None else "") for c in range(max_cols)]
        for r in grid
    ]

    n_hdr = 0
    for flg in header_row_flags:
        if flg:
            n_hdr += 1
        else:
            break
    if n_hdr == 0:
        n_hdr = 1
        span_end = row_max_rowspan[0] if row_max_rowspan else 1
        r_cursor = 0
        while r_cursor < min(len(norm_grid) - 1, 4):
            if r_cursor < len(row_max_rowspan):
                span_end = max(span_end, r_cursor + row_max_rowspan[r_cursor])
            if r_cursor + 1 < span_end:
                n_hdr = max(n_hdr, r_cursor + 2)
                r_cursor += 1
                continue
            if r_cursor == 0 and row_has_colspan and row_has_colspan[0] and len(norm_grid) >= 2:
                next_row = norm_grid[1]
                non_year_nums = sum(
                    1
                    for cell in next_row[1:]
                    if _NUM_TOKEN_RE.match(cell.strip())
                    and not (cell.strip().isdigit() and 1990 <= int(cell.strip()) <= 2035)
                )
                if non_year_nums == 0:
                    n_hdr = max(n_hdr, 2)
            break
        n_hdr = min(n_hdr, max(1, len(norm_grid) - 1)) if len(norm_grid) > 1 else 1

    headers: list[str] = []
    for c in range(max_cols):
        parts: list[str] = []
        for r in range(n_hdr):
            val = norm_grid[r][c].strip()
            if val and (not parts or parts[-1] != val):
                parts.append(val)
        headers.append(" :: ".join(parts) if parts else f"col_{c}")

    body = [r for r in norm_grid[n_hdr:] if any(cell.strip() for cell in r)]
    headers, body, _ = clean_bilingual_2d_grid(headers, body)
    return headers, body


def _is_section_or_table_header(text_val: str) -> bool:
    cleaned = re.sub(r"\((?:tiếp\s+theo|continued)\)", "", text_val, flags=re.IGNORECASE).strip()
    if not cleaned:
        return True
    if _TABLE_COL_HEADER_RE.match(cleaned):
        return True
    if re.match(r"^[A-Z]\.\s+[A-ZÀ-Ỹ]", cleaned):
        return True
    if re.match(r"^[IVX]+\.\s+[A-ZÀ-Ỹ]", cleaned):
        rest_letters = [ch for ch in cleaned.split(".", 1)[1] if ch.isalpha()]
        if len(rest_letters) >= 4 and all(ch.isupper() for ch in rest_letters):
            return True
    if not re.match(r"^\d{2,4}\b", cleaned):
        alpha_words = [w for w in re.findall(r"[^\W\d_]+", cleaned, re.UNICODE) if len(w) >= 2]
        letters = [ch for ch in cleaned if ch.isalpha()]
        if len(alpha_words) >= 2 and len(letters) >= 5 and all(ch.isupper() for ch in letters):
            return True
    return False


def join_wrapped_prefix_and_label(
    pending_prefix_lines: list[tuple[str, float, float]],
    line_top: float,
    label_str: str,
    label_x0: float,
    max_gap: float = 15.0,
    label_words: list[tuple[str, float, float]] | None = None,
) -> str:
    """Join tightly contiguous preceding wrapped label lines onto a data row."""
    close_reversed: list[tuple[str, float, float]] = []
    anchor_top = line_top
    for txt, pt, px0 in reversed(pending_prefix_lines):
        if _is_section_or_table_header(txt):
            break
        gap = anchor_top - pt
        if 0.0 < gap <= max_gap:
            close_reversed.append((txt, pt, px0))
            anchor_top = pt
        else:
            break
    if not close_reversed:
        return label_str
    close_prefixes = list(reversed(close_reversed))
    prefix_text = " ".join(txt for txt, _, _ in close_prefixes).strip()
    if not label_str:
        return prefix_text
    if re.match(r"^[-•–]\s+", label_str):
        return label_str

    first_prefix_x0 = close_prefixes[0][2]
    last_prefix_gap = line_top - close_prefixes[-1][1]
    code_match = re.match(r"^([IVX]+\.?|\d+(?:\.\d+)*\.?)(?:\s+(.*))?$", label_str)
    if code_match and label_x0 < first_prefix_x0 - 4.0:
        if re.match(r"^(?:[IVX]+\.?|\d+(?:\.\d+)*\.?)\s+", prefix_text):
            return label_str
        code_tok = code_match.group(1)
        rest_tok = (code_match.group(2) or "").strip()
        if not rest_tok or _RIGHT_NOTE_REF_RE.fullmatch(rest_tok):
            return (
                f"{code_tok} {prefix_text} {rest_tok}".strip()
                if rest_tok
                else f"{code_tok} {prefix_text}".strip()
            )
        last_prefix_word = (
            prefix_text.split()[-1].rstrip(",/-").lower() if prefix_text.split() else ""
        )
        prefix_continues = (
            prefix_text.endswith((",", "/", "-")) or last_prefix_word in _VN_WRAP_CONNECTORS
        )
        if not rest_tok[0].isupper() or last_prefix_gap <= 10.5 or prefix_continues:
            return f"{code_tok} {prefix_text} {rest_tok}".strip()
        return label_str

    has_code_col_gap = (
        bool(label_words)
        and len(label_words) >= 2
        and bool(re.fullmatch(r"[IVX]+\.?|\d{1,3}\.?", label_words[0][0]))
        and (label_words[1][1] - label_words[0][2]) >= 8.0
    )
    if has_code_col_gap or re.match(r"^(?:[IVX]+\.?|\d+\.\d*|\d{3})\s+[A-ZÀ-Ỹ]", label_str):
        return label_str

    return f"{prefix_text} {label_str}".strip()


def extract_numeric_table_from_region(
    sub_page: Any,
    y_min: float | None = None,
    y_max: float | None = None,
    expected_col_headers: list[str] | None = None,
    col_x_centers: list[float] | None = None,
) -> dict[str, Any]:
    """Coordinate-aware financial table extractor (0 LLM)."""
    words = sub_page.extract_words(keep_blank_chars=False, use_text_flow=False)
    if y_min is not None:
        words = [w for w in words if w["top"] >= y_min]
    if y_max is not None:
        words = [w for w in words if w["bottom"] <= y_max]

    words.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
    lines: list[list[dict[str, Any]]] = []
    for wd in words:
        if not lines or abs(wd["top"] - lines[-1][0]["top"]) > 4.0:
            lines.append([wd])
        else:
            lines[-1].append(wd)
    for ln in lines:
        ln.sort(key=lambda w: w["x0"])

    def is_num_tok(txt: str) -> bool:
        t = txt.strip()
        if t in {"-", "–", "—"}:
            return True
        if len(t) <= 4 and t.isascii() and t.isdigit() and int(t) in range(1990, 2035):
            return False
        return (
            bool(_NUM_TOKEN_RE.match(t))
            and any(ch.isdigit() for ch in t)
            and ("." in t or "," in t or "(" in t or len(t) >= 3 or t == "0")
        )

    if col_x_centers is None:
        num_x1s: list[float] = []
        for ln in lines:
            ntoks = [
                w
                for w in ln
                if is_num_tok(w["text"])
                and w["x0"] > sub_page.bbox[0] + (sub_page.width * 0.35)
            ]
            if len(ntoks) >= 2:
                for w in ntoks:
                    num_x1s.append(float(w["x1"]))
        num_x1s.sort()
        clusters: list[list[float]] = []
        for x in num_x1s:
            if not clusters or (x - clusters[-1][-1]) > 22.0:
                clusters.append([x])
            else:
                clusters[-1].append(x)
        col_x_centers = [round(sum(c) / len(c), 1) for c in clusters if len(c) >= 2]

    n_num_cols = len(col_x_centers)
    col_left_bound = (
        (min(col_x_centers) - 55.0)
        if col_x_centers
        else (sub_page.bbox[0] + sub_page.width * 0.55)
    )

    parsed_rows: list[dict[str, Any]] = []
    context_lines: list[str] = []
    pending_prefix_lines: list[tuple[str, float, float]] = []
    seen_first_data_row = False
    first_data_row_top: float | None = None
    stopped_at_footer = False
    col_word_x0s: list[list[float]] = [[] for _ in range(n_num_cols)]
    col_word_x1s: list[list[float]] = [[] for _ in range(n_num_cols)]

    for ln in lines:
        line_top = round(float(ln[0]["top"]), 1)
        line_text = normalize_ctg_font_quirks(" ".join(w["text"] for w in ln))
        num_slots: list[str | None] = [None] * n_num_cols
        num_word_cols: list[tuple[int, float, float]] = []
        label_word_objs: list[dict[str, Any]] = []

        for w in ln:
            txt = w["text"].strip()
            if (
                n_num_cols > 0
                and w["x1"] >= col_left_bound
                and (is_num_tok(txt) or txt in {"-", "–", "—", "0"})
            ):
                dists = [abs(float(w["x1"]) - cx) for cx in col_x_centers]
                best_c = int(min(range(n_num_cols), key=lambda k: dists[k]))
                if dists[best_c] <= 38.0:
                    num_slots[best_c] = (
                        txt if num_slots[best_c] is None else f"{num_slots[best_c]} {txt}"
                    )
                    num_word_cols.append((best_c, float(w["x0"]), float(w["x1"])))
                    continue
            label_word_objs.append(w)

        non_null_nums = sum(1 for v in num_slots if v is not None)
        label_str = normalize_ctg_font_quirks(
            " ".join(w["text"].strip() for w in label_word_objs)
        ).strip()
        label_x0 = min((float(w["x0"]) for w in label_word_objs), default=float(ln[0]["x0"]))
        label_word_spans = [
            (normalize_ctg_font_quirks(w["text"].strip()), float(w["x0"]), float(w["x1"]))
            for w in label_word_objs
        ]
        is_hdr_date = bool(
            re.match(
                r"^(?:31[./]12[./]20\d\d|20\d\d|năm\s+nay|năm\s+trước|số\s+cuối\s+năm)",
                label_str.lower(),
            )
        )
        has_alpha_label = any(c.isalpha() for c in label_str)
        is_data_row = (
            not stopped_at_footer
            and not is_hdr_date
            and (
                non_null_nums >= max(1, min(2, n_num_cols))
                or (
                    non_null_nums >= 1
                    and has_alpha_label
                    and not _FOOTER_OR_SIG_RE.search(label_str)
                )
            )
        )

        if is_data_row:
            if not seen_first_data_row:
                seen_first_data_row = True
                first_data_row_top = line_top
            for c_i, wx0, wx1 in num_word_cols:
                col_word_x0s[c_i].append(wx0)
                col_word_x1s[c_i].append(wx1)
            trailing_note: str | None = None
            if label_word_spans:
                last_txt, last_x0, _ = label_word_spans[-1]
                if _RIGHT_NOTE_REF_RE.fullmatch(last_txt) and (
                    (len(label_word_spans) >= 2 and (last_x0 - label_word_spans[-2][2]) >= 25.0)
                    or (
                        len(label_word_spans) == 1
                        and pending_prefix_lines
                        and last_x0 > pending_prefix_lines[-1][2] + 80.0
                    )
                ):
                    trailing_note = last_txt
            if pending_prefix_lines:
                label_str = join_wrapped_prefix_and_label(
                    pending_prefix_lines,
                    line_top,
                    label_str,
                    label_x0,
                    label_words=label_word_spans,
                )
                pending_prefix_lines.clear()
            parsed_rows.append(
                {
                    "label": label_str if label_str else "Tổng cộng",
                    "raw_values": num_slots,
                    "top": line_top,
                    "last_label_top": line_top,
                    "trailing_note": trailing_note,
                }
            )
        else:
            is_introducer_or_footnote = label_str.endswith(":") or label_str.startswith("(*)")
            is_section_header = _is_section_or_table_header(label_str)
            if not seen_first_data_row:
                context_lines.append(line_text)
                if (
                    label_str
                    and not re.fullmatch(r"\d{1,4}", label_str)
                    and not is_hdr_date
                    and not is_introducer_or_footnote
                    and not is_section_header
                    and not _FOOTER_OR_SIG_RE.search(label_str)
                    and len(label_str) < 120
                ):
                    if pending_prefix_lines and (line_top - pending_prefix_lines[-1][1]) > 15.0:
                        pending_prefix_lines.clear()
                    if (
                        pending_prefix_lines
                        and has_vietnamese_cues(pending_prefix_lines[-1][0])
                        and is_english_translation_label(
                            label_str, vi_neighbor=pending_prefix_lines[-1][0]
                        )
                    ):
                        continue
                    pending_prefix_lines.append((label_str, line_top, label_x0))
                else:
                    pending_prefix_lines.clear()
            else:
                if _FOOTER_OR_SIG_RE.search(label_str):
                    stopped_at_footer = True
                    pending_prefix_lines.clear()
                    continue
                if stopped_at_footer or not label_str or re.fullmatch(r"\d{1,4}", label_str):
                    continue
                if is_hdr_date or is_introducer_or_footnote or is_section_header:
                    pending_prefix_lines.clear()
                    continue
                # Drop English subtitle translation rows right under a Vietnamese data/prefix row
                # so they NEVER get glued onto `prev_row["label"]` or `pending_prefix_lines`!
                if (
                    parsed_rows
                    and has_vietnamese_cues(parsed_rows[-1]["label"])
                    and is_english_translation_label(label_str, vi_neighbor=parsed_rows[-1]["label"])
                ):
                    continue
                if (
                    pending_prefix_lines
                    and has_vietnamese_cues(pending_prefix_lines[-1][0])
                    and is_english_translation_label(
                        label_str, vi_neighbor=pending_prefix_lines[-1][0]
                    )
                ):
                    continue
                dy_prev = line_top - parsed_rows[-1]["last_label_top"]
                has_code_col_gap = (
                    len(label_word_spans) >= 2
                    and bool(re.fullmatch(r"[IVX]+\.?|\d{1,3}\.?", label_word_spans[0][0]))
                    and (label_word_spans[1][1] - label_word_spans[0][2]) >= 8.0
                )
                starts_new_item = has_code_col_gap or bool(
                    re.match(r"^(?:(?:[IVX]+\.?|\d+\.\d*|\d{3})\s+[A-ZÀ-Ỹ]|[-•–]\s+)", label_str)
                )
                if (
                    not pending_prefix_lines
                    and not starts_new_item
                    and dy_prev <= 15.5
                    and len(label_str) < 60
                ):
                    prev_row = parsed_rows[-1]
                    note_tok = prev_row.get("trailing_note")
                    if note_tok and prev_row["label"].endswith(f" {note_tok}"):
                        base_lbl = prev_row["label"][: -(len(note_tok) + 1)].rstrip()
                        prev_row["label"] = f"{base_lbl} {label_str} {note_tok}".strip()
                    else:
                        prev_row["label"] = f"{prev_row['label']} {label_str}".strip()
                    prev_row["last_label_top"] = line_top
                elif len(label_str) < 120:
                    if pending_prefix_lines and (line_top - pending_prefix_lines[-1][1]) > 15.0:
                        pending_prefix_lines.clear()
                    pending_prefix_lines.append((label_str, line_top, label_x0))
                else:
                    pending_prefix_lines.clear()

    all_raw_nums = [v for r in parsed_rows for v in r["raw_values"] if v]
    locale = detect_table_locale(all_raw_nums)
    currency, unit_mult, unit_raw = detect_unit_and_currency("\n".join(context_lines))

    # Reconstruct real column headers from words in the header band immediately above `first_data_row_top`
    inferred_col_headers: list[str] | None = None
    if (
        not (expected_col_headers and len(expected_col_headers) == n_num_cols)
        and n_num_cols > 0
        and first_data_row_top is not None
    ):
        hdr_skip_re = re.compile(
            r"(?:mẫu\s+số|ban\s+hành\s+theo|thông\s+tư\s+(?:số\s*)?\d+|quyết\s+định\s+số|"
            r"ngày\s+\d+\s+tháng\s+\d+\s+năm\s+\d+|của\s+bộ\s+tài\s+chính|"
            r"bảng\s+cân\s+đối|báo\s+cáo\s+(?:kết\s+quả|lưu\s+chuyển|tình\s+hình)|"
            r"công\s+ty\s+cổ\s+phần|tổng\s+công\s+ty|ngân\s+hàng\s+tmcp|đơn\s+vị\s*(?:tính)?\s*:|trang\s+\d+|"
            r"thuyết\s+minh\s+báo\s+cáo|báo\s+cáo\s+thường\s+niên)",
            re.IGNORECASE,
        )
        col_spans: list[tuple[float, float]] = []
        for c_i in range(n_num_cols):
            cx = float(col_x_centers[c_i])
            if col_word_x0s[c_i] and col_word_x1s[c_i]:
                c_x0 = min(col_word_x0s[c_i])
                c_x1 = max(col_word_x1s[c_i])
            else:
                c_x0, c_x1 = cx - 45.0, cx
            col_spans.append((c_x0, c_x1))

        col_hdr_tokens: list[list[tuple[float, float, str]]] = [[] for _ in range(n_num_cols)]
        hdr_top_min = max(0.0, first_data_row_top - 75.0)
        for ln in lines:
            l_top = float(ln[0]["top"])
            if not (hdr_top_min <= l_top < first_data_row_top - 1.0):
                continue
            ln_txt = normalize_ctg_font_quirks(" ".join(w["text"] for w in ln)).strip()
            if not ln_txt or hdr_skip_re.search(ln_txt):
                continue
            for w in ln:
                w_txt = normalize_ctg_font_quirks(w["text"].strip())
                if not w_txt or w_txt in ("|", "—", "–", "-"):
                    continue
                wx0, wx1 = float(w["x0"]), float(w["x1"])
                if wx1 < col_left_bound - 8.0:
                    continue
                wx_mid = (wx0 + wx1) / 2.0
                # Prefer column whose [x0, x1] interval contains wx_mid, else nearest column center
                best_c = -1
                best_dist = 1e9
                for c_i, (c_x0, c_x1) in enumerate(col_spans):
                    c_mid = (c_x0 + c_x1) / 2.0
                    c_half = max(32.0, (c_x1 - c_x0) * 0.65 + 16.0)
                    if c_x0 - 16.0 <= wx_mid <= c_x1 + 14.0:
                        d = abs(wx_mid - c_mid) * 0.5
                    else:
                        d = abs(wx_mid - c_mid)
                    if d < best_dist and (c_x0 - 24.0 <= wx_mid <= c_x1 + 20.0 or d <= c_half):
                        best_dist = d
                        best_c = c_i
                if best_c >= 0:
                    col_hdr_tokens[best_c].append((l_top, wx0, w_txt))

        rebuilt_headers: list[str] = []
        for c_i in range(n_num_cols):
            toks = sorted(col_hdr_tokens[c_i], key=lambda item: (round(item[0] / 5.0), item[1]))
            joined_h = normalize_ws(" ".join(t[2] for t in toks))
            # Strip pure noise or overly long accidental prose headers
            if joined_h and 2 <= len(joined_h) <= 64 and any(ch.isalnum() for ch in joined_h):
                rebuilt_headers.append(joined_h)
            else:
                rebuilt_headers.append(f"Col_{c_i + 1}")
        if any(not re.fullmatch(r"Col_\d+", h) for h in rebuilt_headers):
            inferred_col_headers = rebuilt_headers

    headers = ["Chỉ tiêu"] + (
        list(expected_col_headers)
        if expected_col_headers and len(expected_col_headers) == n_num_cols
        else inferred_col_headers
        if inferred_col_headers is not None
        else [f"Col_{i + 1}" for i in range(n_num_cols)]
    )
    headers = [clean_bilingual_table_cell(h)[0] or h for h in headers]
    matrix_rows: list[list[dict[str, Any]]] = []
    for r in parsed_rows:
        clean_lbl = clean_bilingual_table_cell(r["label"])[0] or r["label"]
        cells: list[dict[str, Any]] = [{"raw_text": clean_lbl, "numeric_value": None}]
        for rv in r["raw_values"]:
            cells.append({"raw_text": rv or "", "numeric_value": parse_vn_number(rv, locale=locale)})
        matrix_rows.append(cells)

    return {
        "headers": headers,
        "rows": matrix_rows,
        "locale": locale,
        "currency": currency,
        "unit_multiplier": unit_mult,
        "unit_raw": unit_raw,
        "context_header": " | ".join(context_lines[-5:]),
        "col_x_centers": col_x_centers,
    }


def _parse_lattice_grid(
    raw_grid: list[list[Any]],
    *,
    page_text: str,
    expected_col_headers: list[str] | None = None,
) -> dict[str, Any] | None:
    """Convert a single pdfplumber lattice grid into normalized matrix + bilingual clean + sanity gate."""
    raw_multiline_grid = [
        [normalize_ctg_font_quirks(str(cell or "")).strip() for cell in row]
        for row in raw_grid
        if row and any(str(c or "").strip() for c in row)
    ]
    if len(raw_multiline_grid) < 2 or len(raw_multiline_grid[0]) < 2:
        return None
    max_cols = max(len(r) for r in raw_multiline_grid)
    padded_grid = [r + [""] * (max_cols - len(r)) for r in raw_multiline_grid]
    clean_hdrs, clean_body, _ = clean_bilingual_2d_grid(padded_grid[0], padded_grid[1:])
    if not clean_body or len(clean_hdrs) < 2:
        return None
    cleaned_grid = [
        [normalize_ws(c) for c in clean_hdrs],
        *[[normalize_ws(c) for c in r] for r in clean_body],
    ]
    raw_nums = [cell for r in cleaned_grid[1:] for cell in r[1:] if cell]
    locale = detect_table_locale(raw_nums)
    currency, unit_mult, unit_raw = detect_unit_and_currency(page_text)
    headers = [h if h else f"Col_{i}" for i, h in enumerate(cleaned_grid[0])]
    if expected_col_headers and len(headers) == len(expected_col_headers) + 1:
        headers = ["Chỉ tiêu"] + list(expected_col_headers)
    rows = [
        [{"raw_text": r[0] or "Tổng cộng", "numeric_value": None}]
        + [
            {
                "raw_text": cell,
                "numeric_value": parse_vn_number(cell, locale=locale),
            }
            for cell in r[1:]
        ]
        for r in cleaned_grid[1:]
    ]
    num_cells = sum(1 for r in rows for c in r[1:] if c.get("numeric_value") is not None)
    if num_cells < 4:
        return None
    gate = run_arithmetic_sanity_gate(headers, rows)
    return {
        "headers": headers,
        "rows": rows,
        "locale": locale,
        "currency": currency,
        "unit_multiplier": unit_mult,
        "unit_raw": unit_raw,
        "context_header": normalize_ws(page_text[:300]),
        "extraction_method": "lattice",
        "sanity_gate": gate,
        "needs_mineru_fallback": gate["verification_status"] == "TABLE_UNVERIFIED",
    }


_MAX_LATTICE_VECTOR_EDGES = 4000


def extract_tables_with_lattice_fallback(
    sub_page: Any,
    *,
    y_min: float | None = None,
    y_max: float | None = None,
    expected_col_headers: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Extract ALL valid lattice tables (`lat_tables[0..N]`) on a page; fall back to coordinate reconstructor."""
    page_text = ""
    if hasattr(sub_page, "extract_text"):
        page_text = normalize_ctg_font_quirks(sub_page.extract_text() or "")
    # Guard against Adobe Illustrator vector-art infographic pages (>4,000 vector edges)
    # where pdfplumber's O(E^2) lattice edge-intersection can burn minutes on non-table charts.
    try:
        edge_count = (
            len(getattr(sub_page, "curves", ()) or ())
            + len(getattr(sub_page, "lines", ()) or ())
            + len(getattr(sub_page, "rects", ()) or ())
        )
    except Exception:  # noqa: BLE001
        edge_count = 0
    lat_tables = (
        sub_page.extract_tables()
        if (hasattr(sub_page, "extract_tables") and edge_count <= _MAX_LATTICE_VECTOR_EDGES)
        else []
    )

    verified_lattice: list[dict[str, Any]] = []
    if lat_tables:
        for raw_grid in lat_tables:
            parsed = _parse_lattice_grid(
                raw_grid,
                page_text=page_text,
                expected_col_headers=expected_col_headers,
            )
            if parsed is not None and parsed["sanity_gate"]["verification_status"] == "VERIFIED":
                verified_lattice.append(parsed)
    if verified_lattice:
        return verified_lattice

    reconstructed = extract_numeric_table_from_region(
        sub_page,
        y_min=y_min,
        y_max=y_max,
        expected_col_headers=expected_col_headers,
    )
    gate_rec = run_arithmetic_sanity_gate(reconstructed["headers"], reconstructed["rows"])
    reconstructed["extraction_method"] = "coordinate_reconstructor"
    reconstructed["sanity_gate"] = gate_rec
    reconstructed["needs_mineru_fallback"] = gate_rec["verification_status"] == "TABLE_UNVERIFIED"
    return [reconstructed]


def extract_table_with_lattice_fallback(
    sub_page: Any,
    *,
    y_min: float | None = None,
    y_max: float | None = None,
    expected_col_headers: list[str] | None = None,
) -> dict[str, Any]:
    """Try pdfplumber lattice extraction first; self-heal via coordinate reconstructor if sanity gate fails."""
    tables = extract_tables_with_lattice_fallback(
        sub_page,
        y_min=y_min,
        y_max=y_max,
        expected_col_headers=expected_col_headers,
    )
    return tables[0]


def build_matrix_from_html_table(
    html_str: str,
    *,
    context_text: str = "",
) -> dict[str, Any]:
    """Convert MinerU HTML `<table>` into normalized 2D matrix with unit binding and sanity gate."""
    headers, body = unmerge_html_table(html_str)
    all_raw = [cell for row in body for cell in row[1:]]
    locale = detect_table_locale(all_raw)
    currency, unit_mult, unit_raw = detect_unit_and_currency(context_text)
    matrix_rows: list[list[dict[str, Any]]] = []
    for row in body:
        cells: list[dict[str, Any]] = [{"raw_text": row[0], "numeric_value": None}]
        for cell in row[1:]:
            cells.append({"raw_text": cell, "numeric_value": parse_vn_number(cell, locale=locale)})
        matrix_rows.append(cells)

    gate = run_arithmetic_sanity_gate(headers, matrix_rows)
    return {
        "headers": headers,
        "rows": matrix_rows,
        "locale": locale,
        "currency": currency,
        "unit_multiplier": unit_mult,
        "unit_raw": unit_raw,
        "context_header": normalize_ws(context_text[:300]),
        "extraction_method": "mineru_html",
        "sanity_gate": gate,
    }


def _is_metadata_code_column(
    header: str,
    rows: list[list[dict[str, Any]]],
    col_idx: int,
    *,
    total_row_idx: int | None = None,
) -> bool:
    """Detect `Mã số` (100/110/270) or `Thuyết minh` (3.1/5.1) metadata columns that must NOT be summed."""
    h_low = (header or "").strip().lower()
    if any(k in h_low for k in ("mã số", "ma so", "thuyết minh", "thuyet minh", "ghi chú", "note", "code")):
        return True
    non_empty_texts = [
        (r[col_idx].get("raw_text") or "").strip()
        for r in rows
        if col_idx < len(r) and (r[col_idx].get("raw_text") or "").strip() not in ("", "-", "–", "—")
    ]
    if not non_empty_texts:
        return True
    # Detect accounting codes (1..999 without thousands separators) or note refs (3.1, V.1, (10-02))
    code_like = 0
    for txt in non_empty_texts:
        if re.fullmatch(
            r"(?:\d{1,3}(?:\.\d{1,2})?|\d{1,2}[a-z]?|[IVX]{1,4}(?:\.\d{1,2})?|\(\d{1,2}\s*[+\-–]\s*\d{1,2}\))",
            txt,
            re.IGNORECASE,
        ):
            code_like += 1
    if code_like / max(1, len(non_empty_texts)) >= 0.70:
        return True
    # Also detect sparse left-middle note column where total row is blank and < 40% of rows have numbers
    if total_row_idx is not None and total_row_idx < len(rows) and col_idx < len(rows[total_row_idx]):
        tot_empty = rows[total_row_idx][col_idx].get("numeric_value") is None
        if tot_empty and (len(non_empty_texts) / max(1, len(rows))) < 0.45:
            return True
    return False


def _amount_column_indices(
    headers: list[str],
    rows: list[list[dict[str, Any]]],
    *,
    total_row_idx: int | None = None,
) -> list[int]:
    """Return column indices representing monetary/numeric amount columns (excluding Mã số & Thuyết minh)."""
    n_cols = len(headers)
    if n_cols <= 2:
        return list(range(1, n_cols))
    amt_cols = [
        c
        for c in range(1, n_cols)
        if not _is_metadata_code_column(headers[c], rows, c, total_row_idx=total_row_idx)
    ]
    return amt_cols if amt_cols else list(range(1, n_cols))


def run_arithmetic_sanity_gate(
    headers: list[str],
    rows: list[list[dict[str, Any]]],
    total_row_idx: int | None = None,
    sum_row_indices: list[int] | None = None,
    tolerance: float = 0.005,
) -> dict[str, Any]:
    """Step 4 Arithmetic & Structural Sanity Gate (<= 0.5% tolerance; skips Mã số/Thuyết minh cols)."""
    if not rows or len(headers) < 2:
        return {
            "verification_status": "TABLE_UNVERIFIED",
            "sanity_error_ratio": 1.0,
            "right_col_coverage": 0.0,
            "column_checks": [],
        }

    n_cols = len(headers)
    right_vals = [
        r[-1]["numeric_value"]
        for r in rows
        if len(r) == n_cols and r[-1]["numeric_value"] is not None
    ]
    right_col_coverage = len(right_vals) / max(1, len(rows))

    if total_row_idx is None:
        for idx in range(len(rows) - 1, -1, -1):
            lbl = re.sub(r"^\s*\d{2,3}\s+", "", (rows[idx][0]["raw_text"] or "").lower())
            if any(
                k in lbl
                for k in (
                    "tổng cộng",
                    "cộng",
                    "tổng tài sản",
                    "tổng nguồn vốn",
                    "tổng nợ",
                    "tổng doanh thu",
                    "total",
                )
            ):
                total_row_idx = idx
                break
        if total_row_idx is None and len(rows) >= 2:
            total_row_idx = len(rows) - 1

    if total_row_idx is None:
        return {
            "verification_status": "TABLE_UNVERIFIED",
            "sanity_error_ratio": 1.0,
            "right_col_coverage": round(right_col_coverage, 3),
            "column_checks": [],
        }

    if sum_row_indices is None:
        sum_row_indices = list(range(total_row_idx))

    amt_cols = _amount_column_indices(headers, rows, total_row_idx=total_row_idx)
    col_checks: list[dict[str, Any]] = []
    max_err = 0.0
    for c in amt_cols:
        tot_val = rows[total_row_idx][c]["numeric_value"] if c < len(rows[total_row_idx]) else None
        if tot_val is None:
            col_checks.append(
                {
                    "col": headers[c],
                    "status": "MISSING_TOTAL",
                    "error_ratio": 1.0,
                    "passed": False,
                }
            )
            max_err = max(max_err, 1.0)
            continue
        sub_vals = [
            rows[i][c]["numeric_value"]
            for i in sum_row_indices
            if i < len(rows) and c < len(rows[i]) and rows[i][c]["numeric_value"] is not None
        ]
        calc_sum = sum(sub_vals)
        err = abs(calc_sum - tot_val) / max(abs(tot_val), 1.0)
        max_err = max(max_err, err)
        col_checks.append(
            {
                "col": headers[c],
                "expected_total": tot_val,
                "computed_sum": round(calc_sum, 4),
                "error_ratio": round(err, 6),
                "passed": err <= tolerance,
            }
        )

    if right_col_coverage == 0.0:
        status: VerificationStatus = "TABLE_UNVERIFIED"
    elif col_checks and all(ch.get("passed") for ch in col_checks):
        status = "VERIFIED"
    elif col_checks and any(ch.get("passed") for ch in col_checks):
        status = "PARTIAL"
    else:
        status = "TABLE_UNVERIFIED"

    # Hierarchical fallback for multi-level Vietnamese financial statements (Balance Sheet / P&L / Notes)
    if status != "VERIFIED" and right_col_coverage > 0.0 and len(rows) >= 4:
        hier_gate = _try_auto_hierarchical_reconciliation(headers, rows, tolerance=tolerance)
        if hier_gate is not None:
            hier_rank = {"TABLE_UNVERIFIED": 0, "PARTIAL": 1, "VERIFIED": 2}
            if hier_rank[hier_gate["verification_status"]] > hier_rank[status]:
                return hier_gate

    return {
        "verification_status": status,
        "sanity_error_ratio": round(max_err, 6),
        "right_col_coverage": round(right_col_coverage, 3),
        "column_checks": col_checks,
    }


def _clean_acct_label(raw: str) -> str:
    cleaned = re.sub(
        r"^\s*(?:[a-d]\.|[ivx]+\.|\d{1,3}(?:\.\d)?\.?)\s+",
        "",
        (raw or "").strip().lower(),
    )
    return re.sub(r"\s+", " ", cleaned).strip()


def _try_auto_hierarchical_reconciliation(
    headers: list[str],
    rows: list[list[dict[str, Any]]],
    *,
    tolerance: float = 0.005,
) -> dict[str, Any] | None:
    """Auto-detect standard Vietnamese Balance Sheet, P&L, Cash Flow & subtotal identities."""
    n_cols = len(headers)
    if n_cols < 2 or len(rows) < 4:
        return None

    # 1. Balance Sheet / P&L / Cash Flow row anchors
    assets_idx: int | None = None
    liab_eq_idx: int | None = None
    cur_assets_idx: int | None = None
    noncur_assets_idx: int | None = None
    liab_idx: int | None = None
    equity_idx: int | None = None
    gross_rev_idx: int | None = None
    deduct_idx: int | None = None
    net_rev_idx: int | None = None
    cogs_idx: int | None = None
    gross_profit_idx: int | None = None
    net_cf_idx: int | None = None
    open_cash_idx: int | None = None
    fx_cash_idx: int | None = None
    close_cash_idx: int | None = None

    for idx, r in enumerate(rows):
        lbl = _clean_acct_label(r[0].get("raw_text") or "")
        lbl_compact = re.sub(r"\s+", "", lbl)
        if ("tổng cộng tài sản" in lbl or lbl.startswith("tổng tài sản")) and assets_idx is None:
            assets_idx = idx
        elif (
            "tổng cộng nguồn vốn" in lbl
            or "tổng nợ phải trả và vốn chủ" in lbl
            or lbl.startswith("tổng nguồn vốn")
        ):
            liab_eq_idx = idx
        elif (
            ("tàisảnngắnhạn" in lbl_compact or lbl.startswith("tài sản ngắn hạn"))
            and "tổng" not in lbl
            and len(lbl) <= 45
            and cur_assets_idx is None
        ):
            cur_assets_idx = idx
        elif (
            ("tàisảndàihạn" in lbl_compact or lbl.startswith("tài sản dài hạn"))
            and "tổng" not in lbl
            and len(lbl) <= 45
            and noncur_assets_idx is None
        ):
            noncur_assets_idx = idx
        elif lbl.startswith("nợ phải trả") and len(lbl) <= 35 and liab_idx is None:
            liab_idx = idx
        elif lbl.startswith("vốn chủ sở hữu") and len(lbl) <= 40 and equity_idx is None:
            equity_idx = idx
        elif lbl.startswith("doanh thu bán hàng") and gross_rev_idx is None:
            gross_rev_idx = idx
        elif "giảm trừ doanh thu" in lbl and deduct_idx is None:
            deduct_idx = idx
        elif lbl.startswith("doanh thu thuần") and net_rev_idx is None:
            net_rev_idx = idx
        elif lbl.startswith("giá vốn hàng bán") and cogs_idx is None:
            cogs_idx = idx
        elif lbl.startswith("lợi nhuận gộp") and gross_profit_idx is None:
            gross_profit_idx = idx
        elif "lưu chuyển tiền thuần trong" in lbl and net_cf_idx is None:
            net_cf_idx = idx
        elif (
            re.search(r"tiền\s+và\s+(?:các\s+khoản\s+)?tương\s+đương\s+tiền\s+đầu\s+(?:năm|kỳ)", lbl)
            and open_cash_idx is None
        ):
            open_cash_idx = idx
        elif "ảnh hưởng của thay đổi tỷ giá" in lbl and fx_cash_idx is None:
            fx_cash_idx = idx
        elif (
            re.search(r"tiền\s+và\s+(?:các\s+khoản\s+)?tương\s+đương\s+tiền\s+cuối\s+(?:năm|kỳ)", lbl)
            and close_cash_idx is None
        ):
            close_cash_idx = idx

    if assets_idx is not None and liab_eq_idx is not None and assets_idx != liab_eq_idx:
        return run_hierarchical_identities_sanity_gate(
            headers,
            rows,
            [("Total_Assets_eq_Liabilities_Plus_Equity", assets_idx, [liab_eq_idx])],
            tolerance=tolerance,
        )

    if assets_idx is not None and cur_assets_idx is not None and noncur_assets_idx is not None:
        return run_hierarchical_identities_sanity_gate(
            headers,
            rows,
            [("Total_Assets_eq_Current_Plus_NonCurrent", assets_idx, [cur_assets_idx, noncur_assets_idx])],
            tolerance=tolerance,
        )

    if liab_eq_idx is not None and liab_idx is not None and equity_idx is not None:
        gate_le = run_hierarchical_identities_sanity_gate(
            headers,
            rows,
            [("Total_Liab_Equity_eq_Liab_Plus_Equity", liab_eq_idx, [liab_idx, equity_idx])],
            tolerance=tolerance,
        )
        if gate_le["verification_status"] in ("VERIFIED", "PARTIAL"):
            return gate_le

    if close_cash_idx is not None and net_cf_idx is not None and open_cash_idx is not None:
        cf_comps = [net_cf_idx, open_cash_idx] + ([fx_cash_idx] if fx_cash_idx is not None else [])
        gate_cf = run_hierarchical_identities_sanity_gate(
            headers,
            rows,
            [("Ending_Cash_eq_NetCF_Plus_OpenCash", close_cash_idx, cf_comps)],
            tolerance=tolerance,
        )
        if gate_cf["verification_status"] in ("VERIFIED", "PARTIAL"):
            return gate_cf

    # 2. P&L identities (e.g. Gross Profit = Net Revenue - COGS; Net Revenue = Gross Rev - Deductions)
    pnl_identities: list[tuple[str, int, list[int]]] = []
    if net_rev_idx is not None and cogs_idx is not None and gross_profit_idx is not None:
        # Handle both positive-coded COGS (NetRev - COGS = GP) and negative-coded COGS (NetRev + (-COGS) = GP)
        amt_cols = _amount_column_indices(headers, rows, total_row_idx=gross_profit_idx)
        if amt_cols:
            c0 = amt_cols[0]
            nr_v = rows[net_rev_idx][c0].get("numeric_value")
            cg_v = rows[cogs_idx][c0].get("numeric_value")
            gp_v = rows[gross_profit_idx][c0].get("numeric_value")
            if nr_v is not None and cg_v is not None and gp_v is not None:
                if cg_v <= 0:
                    pnl_identities.append(
                        ("Gross_Profit_eq_NetRev_Plus_NegCOGS", gross_profit_idx, [net_rev_idx, cogs_idx])
                    )
                else:
                    # Net Revenue == Gross Profit + COGS
                    pnl_identities.append(
                        ("NetRev_eq_GrossProfit_Plus_COGS", net_rev_idx, [gross_profit_idx, cogs_idx])
                    )
    if gross_rev_idx is not None and deduct_idx is not None and net_rev_idx is not None:
        amt_cols = _amount_column_indices(headers, rows, total_row_idx=net_rev_idx)
        if amt_cols:
            c0 = amt_cols[0]
            dv = rows[deduct_idx][c0].get("numeric_value")
            if dv is not None:
                if dv <= 0:
                    pnl_identities.append(
                        ("NetRev_eq_GrossRev_Plus_NegDeduct", net_rev_idx, [gross_rev_idx, deduct_idx])
                    )
                else:
                    pnl_identities.append(
                        ("GrossRev_eq_NetRev_Plus_Deduct", gross_rev_idx, [net_rev_idx, deduct_idx])
                    )
    if pnl_identities:
        gate_pnl = run_hierarchical_identities_sanity_gate(headers, rows, pnl_identities, tolerance=tolerance)
        if gate_pnl["verification_status"] in ("VERIFIED", "PARTIAL"):
            return gate_pnl

    # 3. Local subtotal-block checks (e.g. multiple sub-sections each ending in "Cộng" / "Tổng cộng")
    total_indices = [
        idx
        for idx, r in enumerate(rows)
        if any(
            k in _clean_acct_label(r[0].get("raw_text") or "")
            for k in ("tổng cộng", "cộng", "tổng tài sản", "tổng nguồn vốn", "tổng nợ", "tổng doanh thu", "total")
        )
    ]
    if len(total_indices) >= 2:
        identities: list[tuple[str, int, list[int]]] = []
        grand_idx = total_indices[-1]
        prev_totals = total_indices[:-1]
        gate_a = run_hierarchical_identities_sanity_gate(
            headers,
            rows,
            [("Grand_Total_eq_Sum_Subtotals", grand_idx, prev_totals)],
            tolerance=tolerance,
        )
        if gate_a["verification_status"] == "VERIFIED":
            return gate_a
        prev_cut = 0
        for sub_idx in total_indices:
            comp = list(range(prev_cut, sub_idx))
            if len(comp) >= 2:
                identities.append((f"Block_{prev_cut}_{sub_idx}", sub_idx, comp))
            prev_cut = sub_idx + 1
        if identities:
            return run_hierarchical_identities_sanity_gate(
                headers, rows, identities, tolerance=tolerance
            )
    return None


def run_hierarchical_identities_sanity_gate(
    headers: list[str],
    rows: list[list[dict[str, Any]]],
    identities: list[tuple[str, int, list[int]]],
    tolerance: float = 0.005,
) -> dict[str, Any]:
    """Verify multi-level P&L / Balance Sheet accounting identities across all numeric columns."""
    if not rows or len(headers) < 2 or not identities:
        return {
            "verification_status": "TABLE_UNVERIFIED",
            "sanity_error_ratio": 1.0,
            "right_col_coverage": 0.0,
            "column_checks": [],
        }

    n_cols = len(headers)
    right_vals = [
        r[-1]["numeric_value"]
        for r in rows
        if len(r) == n_cols and r[-1]["numeric_value"] is not None
    ]
    right_col_coverage = len(right_vals) / max(1, len(rows))

    checks: list[dict[str, Any]] = []
    max_err = 0.0
    for name, target_idx, component_indices in identities:
        if target_idx >= len(rows) or any(ci >= len(rows) for ci in component_indices):
            checks.append(
                {
                    "identity": name,
                    "status": "MISSING_ROW",
                    "error_ratio": 1.0,
                    "passed": False,
                }
            )
            max_err = 1.0
            continue
        amt_cols = _amount_column_indices(headers, rows, total_row_idx=target_idx)
        for c in amt_cols:
            tgt = rows[target_idx][c]["numeric_value"] if c < len(rows[target_idx]) else None
            comps = [
                rows[ci][c]["numeric_value"] if c < len(rows[ci]) else None
                for ci in component_indices
            ]
            if tgt is None or any(v is None for v in comps):
                checks.append(
                    {
                        "identity": name,
                        "col": headers[c],
                        "status": "MISSING_VAL",
                        "error_ratio": 1.0,
                        "passed": False,
                    }
                )
                max_err = 1.0
                continue
            calc = sum(v for v in comps if v is not None)
            err = abs(calc - tgt) / max(abs(tgt), 1.0)
            max_err = max(max_err, err)
            checks.append(
                {
                    "identity": name,
                    "col": headers[c],
                    "expected": tgt,
                    "computed": round(calc, 4),
                    "error_ratio": round(err, 6),
                    "passed": err <= tolerance,
                }
            )

    if right_col_coverage == 0.0:
        status: VerificationStatus = "TABLE_UNVERIFIED"
    elif checks and all(ch["passed"] for ch in checks):
        status = "VERIFIED"
    elif checks and any(ch["passed"] for ch in checks):
        status = "PARTIAL"
    else:
        status = "TABLE_UNVERIFIED"

    return {
        "verification_status": status,
        "sanity_error_ratio": round(max_err, 6),
        "right_col_coverage": round(right_col_coverage, 3),
        "column_checks": checks,
    }


def _is_repeated_header_or_carryover_row(row: list[dict[str, Any]], headers: list[str]) -> bool:
    if not row:
        return True
    first = (row[0].get("raw_text") or "").strip().lower()
    if any(k in first for k in ("cộng chuyển sang", "mang sang", "chuyển sang trang")):
        return True
    num_vals = [c.get("numeric_value") for c in row[1:]]
    if all(v is None for v in num_vals):
        row_texts = {
            (c.get("raw_text") or "").strip().lower()
            for c in row
            if (c.get("raw_text") or "").strip()
        }
        hdr_texts = {h.strip().lower() for h in headers if h.strip()}
        if row_texts and (len(row_texts & hdr_texts) / len(row_texts)) >= 0.5:
            return True
    return False


def _align_row_width(row: list[dict[str, Any]], target_cols: int) -> list[dict[str, Any]]:
    if len(row) == target_cols:
        return list(row)
    if len(row) < target_cols:
        return list(row) + [
            {"raw_text": "", "numeric_value": None} for _ in range(target_cols - len(row))
        ]
    return list(row[:target_cols])


def _detect_statement_family(tbl: dict[str, Any]) -> str:
    """Identify statement family (`BS`/`IS`/`CF`/`NOTES`) to block cross-statement over-stitching."""
    blob = f"{tbl.get('section_path') or ''} {tbl.get('context_header') or ''}".lower()
    if "thuyết minh báo cáo tài chính" in blob or "thuyết minh bctc" in blob:
        return "NOTES"
    if "lưu chuyển tiền tệ" in blob or "cash flow" in blob:
        return "CF"
    if "kết quả hoạt động" in blob or "income statement" in blob:
        return "IS"
    if "cân đối kế toán" in blob or "tình hình tài chính" in blob or "balance sheet" in blob:
        return "BS"
    rows = tbl.get("rows") or []
    first_lbls = " ".join(
        _clean_acct_label(r[0].get("raw_text") or "")
        for r in rows[:3]
        if isinstance(r, list) and r and isinstance(r[0], dict)
    )
    if "lưu chuyển tiền" in first_lbls:
        return "CF"
    if "doanh thu bán hàng" in first_lbls or "doanh thu thuần" in first_lbls:
        return "IS"
    if any(k in first_lbls for k in ("tài sản ngắn hạn", "nợ phải trả", "vốn chủ sở hữu")):
        return "BS"
    return ""


def _ends_with_terminal_statement_row(rows: list[list[dict[str, Any]]]) -> bool:
    """True when a table's tail closes a full financial statement (NOT mid-BS `TỔNG CỘNG TÀI SẢN`)."""
    tail_lbls = " ".join(
        _clean_acct_label(r[0].get("raw_text") or "")
        for r in rows[-3:]
        if isinstance(r, list) and r and isinstance(r[0], dict)
    )
    return bool(
        re.search(
            r"(?:tổng\s+cộng\s+nguồn\s+vốn|tổng\s+nguồn\s+vốn|tổng\s+nợ\s+phải\s+trả\s+và\s+vốn\s+chủ|"
            r"lãi\s+(?:cơ\s+bản|suy\s+giảm)\s+trên\s+cổ\s+phiếu|"
            r"tiền\s+và\s+(?:các\s+khoản\s+)?tương\s+đương\s+tiền\s+cuối\s+(?:năm|kỳ))",
            tail_lbls,
        )
    )


def _starts_new_statement_body(rows: list[list[dict[str, Any]]]) -> bool:
    """True when a table's opening rows begin a brand-new financial statement."""
    head_lbls = " ".join(
        _clean_acct_label(r[0].get("raw_text") or "")
        for r in rows[:2]
        if isinstance(r, list) and r and isinstance(r[0], dict)
    )
    return bool(
        re.search(
            r"(?:doanh\s+thu\s+bán\s+hàng|lưu\s+chuyển\s+tiền\s+từ\s+hoạt\s+động\s+kinh\s+doanh|"
            r"tài\s+sản\s+ngắn\s+hạn)",
            head_lbls,
        )
    )


def _is_balance_sheet_continuation(prev: dict[str, Any], tbl: dict[str, Any]) -> bool:
    """True when `prev` is Balance Sheet Assets/Liabilities and `tbl` continues with Liabilities/Equity."""
    if _detect_statement_family(prev) != "BS":
        return False
    prev_tail = " ".join(
        _clean_acct_label(r[0].get("raw_text") or "")
        for r in (prev.get("rows") or [])[-3:]
        if isinstance(r, list) and r and isinstance(r[0], dict)
    )
    cur_head = " ".join(
        _clean_acct_label(r[0].get("raw_text") or "")
        for r in (tbl.get("rows") or [])[:2]
        if isinstance(r, list) and r and isinstance(r[0], dict)
    )
    cur_compact = re.sub(r"\s+", "", cur_head)
    return (
        not _ends_with_terminal_statement_row(prev.get("rows") or [])
        and (
            bool(re.search(r"(?:nợ\s+phải\s+trả|vốn\s+chủ\s+sở\s+hữu|tài\s+sản\s+dài\s+hạn)", cur_head))
            or any(k in cur_compact for k in ("tàisảndàihạn", "nợphảitrả", "vốnchủsởhữu"))
        )
        and ("tổng cộng nguồn vốn" not in prev_tail)
    )


def stitch_cross_page_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 2 Cross-Page Table Stitcher: merges consecutive tables while blocking cross-statement over-stitching."""
    if not tables:
        return []
    stitched: list[dict[str, Any]] = [dict(tables[0])]
    for tbl in tables[1:]:
        prev = stitched[-1]
        seq_delta = int(tbl["virtual_seq"]) - int(prev["virtual_seq"])
        same_slot = tbl["sub_doc_slot"] == prev["sub_doc_slot"]
        is_bs_cont = _is_balance_sheet_continuation(prev, tbl)
        consecutive = (seq_delta == 1) or (seq_delta == 0 and is_bs_cont)
        col_diff = abs(len(tbl["headers"]) - len(prev["headers"])) <= 1
        h_prev = {
            h.lower() for h in prev["headers"] if not re.match(r"^col_\d+$", h.strip(), re.IGNORECASE)
        }
        h_cur = {
            h.lower() for h in tbl["headers"] if not re.match(r"^col_\d+$", h.strip(), re.IGNORECASE)
        }
        # Do not stitch unrelated Note tables purely because both fell back to generic Col_1/Col_2 headers
        sig_jaccard = (
            len(h_prev & h_cur) / max(1, len(h_prev | h_cur))
            if (len(h_prev) >= 2 and len(h_cur) >= 2)
            else 0.0
        )
        ctx_low = (tbl.get("context_header") or "").lower()
        is_cont = ("tiếp theo" in ctx_low) or ("continued" in ctx_low)

        # Statement-boundary guard: NEVER over-stitch B02 (KQKD) with B03 (LCTT) or past terminal rows!
        prev_fam = _detect_statement_family(prev)
        cur_fam = _detect_statement_family(tbl)
        cross_statement = bool(prev_fam and cur_fam and prev_fam != cur_fam)
        prev_closed = _ends_with_terminal_statement_row(prev.get("rows") or [])
        cur_opens_new = len(prev.get("rows") or []) >= 4 and _starts_new_statement_body(tbl.get("rows") or [])
        # When column counts differ without explicit "(tiếp theo)", avoid shifting mismatched statements
        safe_cols = (len(tbl["headers"]) == len(prev["headers"])) or is_cont or is_bs_cont

        can_stitch = (
            consecutive
            and same_slot
            and col_diff
            and safe_cols
            and not cross_statement
            and not prev_closed
            and not cur_opens_new
            and (is_cont or sig_jaccard >= 0.80 or (is_bs_cont and sig_jaccard >= 0.50))
        )
        if can_stitch:
            target_cols = len(prev["headers"])
            cont_rows = [
                _align_row_width(r, target_cols)
                for r in tbl["rows"]
                if not _is_repeated_header_or_carryover_row(r, prev["headers"])
            ]
            prev["rows"] = [_align_row_width(r, target_cols) for r in prev["rows"]] + cont_rows
            prev["page_to"] = tbl["page_to"]
            prev["virtual_seq"] = tbl["virtual_seq"]
            prev["stitched_from_pages"] = prev.get("stitched_from_pages", [prev["page_from"]]) + [
                tbl["page_from"]
            ]
        else:
            stitched.append(dict(tbl))
    return stitched


def compute_table_hash(rows: list[list[dict[str, Any]]]) -> str:
    return hashlib.sha256(
        json.dumps(rows, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def _cell_raw_and_num(row: Any, col_idx: int) -> tuple[str, float | None]:
    """Read `(raw_text, numeric_value)` uniformly from raw matrix rows or hydrated dict rows."""
    if isinstance(row, dict) and "values" in row:
        if col_idx == 0:
            return str(row.get("label") or ""), None
        vals = row.get("values") or []
        v_idx = col_idx - 1
        if 0 <= v_idx < len(vals):
            v = vals[v_idx]
            if v is None:
                return "", None
            return str(v), float(v) if isinstance(v, (int, float)) else None
        return "", None
    if isinstance(row, list) and 0 <= col_idx < len(row):
        cell = row[col_idx]
        if isinstance(cell, dict):
            return str(cell.get("raw_text") or ""), cell.get("numeric_value")
        return str(cell or ""), None
    return "", None


_COL_CODE_HDR_RE = re.compile(r"(?:^|\b)(?:mã\s*số|ma\s*so|code|stt)(?:\b|$)", re.IGNORECASE)
_COL_NOTE_HDR_RE = re.compile(
    r"(?:thuyết\s*minh|thuyet\s*minh|ghi\s*chú|ghi\s*chu|\bnote(?:s)?\b|\btm\b)",
    re.IGNORECASE,
)
_COL_RATIO_HDR_RE = re.compile(
    r"(?:%|tỷ\s*lệ|tỉ\s*lệ|tỷ\s*trọng|tỉ\s*trọng|tăng\s*trưởng|thay\s*đổi|biến\s*động|"
    r"biên|margin|ratio|change|yoy|qoq|cagr|roe|roa|nim|cir|car|npl|ldr|casa)",
    re.IGNORECASE,
)
_COL_COUNT_HDR_RE = re.compile(
    r"(?:số\s*lượng|khối\s*lượng|nhân\s*sự|cổ\s*phiếu|cổ\s*đông|số\s*tàu|cửa\s*hàng|"
    r"chi\s*nhánh|volume|count|quantity|shares)",
    re.IGNORECASE,
)
_COL_PERIOD_HDR_RE = re.compile(
    r"(?:20\d\d|19\d\d|q[1-4]\b|quý\s*[1-4ivx]|năm\s*(?:nay|trước|\d{4})|kỳ\s*(?:này|trước)|"
    r"cuối\s*(?:năm|kỳ)|đầu\s*(?:năm|kỳ)|31[./\-]12|30[./\-]06|30[./\-]09|31[./\-]03|01[./\-]01|"
    r"kế\s*hoạch|thực\s*hiện|lũy\s*kế|fy\d{2}|ytd|6t\b|9t\b|12t\b|tổng\s*cộng|total)",
    re.IGNORECASE,
)
_COL_COMPONENT_HDR_RE = re.compile(
    r"(?:giá\s*gốc|dự\s*phòng|nguyên\s*giá|hao\s*mòn|khấu\s*hao|giá\s*trị\s*(?:hợp\s*lý|ghi\s*sổ|còn\s*lại|thuần)|"
    r"tăng\s*trong\s*(?:năm|kỳ)|giảm\s*trong\s*(?:năm|kỳ)|phát\s*sinh|trong\s*hạn|quá\s*hạn|"
    r"tài\s*sản\s*đảm\s*bảo|nhóm\s*[1-5]|cost|provision|allowance|depreciation|carrying|fair\s*value)",
    re.IGNORECASE,
)


def classify_table_columns(
    headers: list[str] | list[list[str]],
    rows: list[Any],
) -> tuple[list[str], list[int]]:
    """Classify each column into semantic role & return `(column_roles, amount_col_indices)`.

    - `column_roles` has length `len(flat_headers)` with roles in:
      `label`, `code`, `note_ref`, `period_amount`, `component_amount`, `ratio_pct`, `count`, `other`.
    - `amount_col_indices` are 0-based indices into `HydratedTable.rows[i]["values"]`
      (i.e. `col_idx - 1` for monetary columns excluding `code`, `note_ref`, `ratio_pct`, `count`).
    """
    flat_headers: list[str]
    if headers and isinstance(headers[0], list):
        flat_headers = [str(h or "") for h in headers[0]]
    else:
        flat_headers = [str(h or "") for h in headers]
    if not flat_headers:
        return [], []

    roles: list[str] = ["label"]
    amount_indices: list[int] = []
    n_rows = len(rows)

    for c_idx in range(1, len(flat_headers)):
        hdr = flat_headers[c_idx].strip()
        non_empty_raw: list[str] = []
        numeric_vals: list[float] = []
        for r in rows:
            raw_t, num_v = _cell_raw_and_num(r, c_idx)
            s = raw_t.strip()
            if s and s not in ("-", "–", "—"):
                non_empty_raw.append(s)
            if num_v is not None:
                numeric_vals.append(float(num_v))

        role: str
        if _COL_CODE_HDR_RE.search(hdr):
            role = "code"
        elif _COL_NOTE_HDR_RE.search(hdr):
            role = "note_ref"
        elif _COL_RATIO_HDR_RE.search(hdr) or (
            non_empty_raw and (sum(1 for s in non_empty_raw if s.endswith("%")) / len(non_empty_raw)) >= 0.5
        ):
            role = "ratio_pct"
        elif _COL_COUNT_HDR_RE.search(hdr):
            role = "count"
        else:
            # Value-based detection for unlabeled/misaligned Mã số & Thuyết minh columns.
            # NEVER reclassify an explicit period/year or component header (e.g. "2024") as code/note_ref.
            is_period_hdr = bool(_COL_PERIOD_HDR_RE.search(hdr))
            is_component_hdr = bool(_COL_COMPONENT_HDR_RE.search(hdr))
            is_code_vals = False
            is_note_vals = False
            if non_empty_raw and not is_period_hdr and not is_component_hdr:
                acct_codes = sum(
                    1
                    for s in non_empty_raw
                    if re.fullmatch(r"\d{2,3}", s) or re.fullmatch(r"\(\d{1,2}\s*[+\-–]\s*\d{1,2}\)", s)
                )
                note_refs = sum(
                    1
                    for s in non_empty_raw
                    if re.fullmatch(
                        r"(?:\d{1,2}\.\d{1,2}[a-z]?|\d{1,2}[a-z]|[IVX]{1,4}(?:\.\d{1,2})?)",
                        s,
                        re.IGNORECASE,
                    )
                )
                max_abs = max((abs(v) for v in numeric_vals), default=0.0)
                if (
                    c_idx <= 2
                    and (acct_codes / len(non_empty_raw)) >= 0.70
                    and max_abs < 1000.0
                    and len(flat_headers) >= 3
                ):
                    is_code_vals = True
                elif (
                    c_idx <= 2
                    and (note_refs / len(non_empty_raw)) >= 0.65
                    and len(flat_headers) >= 3
                ) or (
                    len(flat_headers) >= 4
                    and c_idx <= 2
                    and n_rows >= 4
                    and (len(non_empty_raw) / max(1, n_rows)) < 0.40
                    and max_abs < 100.0
                ):
                    is_note_vals = True

            if is_code_vals:
                role = "code"
            elif is_note_vals:
                role = "note_ref"
            elif is_component_hdr:
                role = "component_amount"
            elif is_period_hdr:
                role = "period_amount"
            elif numeric_vals:
                role = "other"
            else:
                role = "other"

        roles.append(role)
        if role in ("period_amount", "component_amount", "other") and (numeric_vals or role == "period_amount"):
            amount_indices.append(c_idx - 1)

    # Fallback: if no monetary column detected on a simple 2-col table, keep non-code/non-note numeric cols
    if not amount_indices and len(flat_headers) >= 2:
        amount_indices = [
            c_idx - 1
            for c_idx in range(1, len(flat_headers))
            if roles[c_idx] not in ("code", "note_ref")
        ]
    return roles, amount_indices


def build_grouped_table_search_cards(
    table_rec: dict[str, Any],
    *,
    rows_per_card: int = 6,
) -> list[dict[str, Any]]:
    """Representation B (Q3=A): Grouped Row-Linearized Search Cards (e.g. 6 rows/card) for indexing."""
    headers = table_rec["headers"]
    flat_headers = headers[0] if headers and isinstance(headers[0], list) else headers
    fq = int(table_rec.get("fiscal_quarter", 0) or 0)
    period_lbl = f"FY{table_rec['fiscal_year']}" if fq == 0 else f"Q{fq}/{table_rec['fiscal_year']}"
    page_from = int(table_rec.get("page_from") or 1)
    page_to = int(table_rec.get("page_to") or page_from)
    page_str = f"{page_from}-{page_to}" if page_to != page_from else str(page_from)
    header_line = (
        f"[Bảng: {table_rec['table_title']} | Mã: {table_rec['project_id']} | "
        f"Kỳ: {period_lbl} | Trang: {page_str} | Đơn vị: {table_rec['unit_raw']}]"
    )

    row_lines: list[tuple[int, str]] = []
    for r_idx, row in enumerate(table_rec.get("rows") or []):
        label = str(row[0].get("raw_text") or "").strip() if isinstance(row, list) and row else ""
        pairs: list[str] = []
        for c_idx in range(1, len(flat_headers)):
            if not isinstance(row, list) or c_idx >= len(row):
                break
            rv = str(row[c_idx].get("raw_text") or "").strip()
            if rv:
                pairs.append(f"{flat_headers[c_idx]}: {rv}")
        if not pairs:
            continue
        row_lines.append((r_idx, f"Chỉ tiêu: {label} | " + " | ".join(pairs)))

    if not row_lines:
        return []

    chunk_size = max(1, int(rows_per_card))
    cards: list[dict[str, Any]] = []
    for card_idx, start in enumerate(range(0, len(row_lines), chunk_size)):
        group = row_lines[start : start + chunk_size]
        card_text = header_line + "\n" + "\n".join(line for _, line in group)
        cards.append(
            {
                "table_id": str(table_rec["table_id"]),
                "card_idx": card_idx,
                "row_from": group[0][0],
                "row_to": group[-1][0],
                "page_from": page_from,
                "page_to": page_to,
                "section_path": str(table_rec.get("section_path") or ""),
                "sub_doc_slot": str(table_rec.get("sub_doc_slot") or ""),
                "fiscal_year": int(table_rec.get("fiscal_year") or 0),
                "fiscal_quarter": fq,
                "card_text": card_text,
            }
        )
    return cards


def build_row_linearized_cards(table_rec: dict[str, Any]) -> list[dict[str, Any]]:
    """Representation B: Row-Linearized Search Cards carrying table_id, sub_doc_slot, period, unit, page."""
    headers = table_rec["headers"]
    flat_headers = headers[0] if headers and isinstance(headers[0], list) else headers
    fq = int(table_rec.get("fiscal_quarter", 0) or 0)
    period_lbl = f"FY{table_rec['fiscal_year']}" if fq == 0 else f"Q{fq}/{table_rec['fiscal_year']}"
    cards: list[dict[str, Any]] = []
    for r_idx, row in enumerate(table_rec["rows"]):
        label = row[0]["raw_text"]
        pairs: list[str] = []
        for c_idx in range(1, len(flat_headers)):
            if c_idx >= len(row):
                break
            rv = row[c_idx]["raw_text"]
            if rv:
                pairs.append(f"{flat_headers[c_idx]}: {rv}")
        if not pairs:
            continue
        card_text = (
            f"[Bảng: {table_rec['table_title']} | Mã: {table_rec['project_id']} | "
            f"Kỳ: {period_lbl} | Trang: {table_rec['page_from']}"
            f"{'-' + str(table_rec['page_to']) if table_rec['page_to'] != table_rec['page_from'] else ''} | "
            f"Đơn vị: {table_rec['unit_raw']}]\n"
            f"Chỉ tiêu: {label} | " + " | ".join(pairs)
        )
        cards.append(
            {
                "table_id": table_rec["table_id"],
                "row_idx": r_idx,
                "sub_doc_slot": table_rec["sub_doc_slot"],
                "fiscal_year": table_rec["fiscal_year"],
                "fiscal_quarter": fq,
                "card_text": card_text,
            }
        )
    return cards


def to_hydrated_table(table_rec: dict[str, Any]) -> dict[str, Any]:
    """Convert an extracted table record into `PROJECT.md` Contract 4 `HydratedTable` JSON."""
    raw_headers = table_rec.get("headers") or table_rec.get("headers_json") or []
    headers_2d = (
        raw_headers
        if (raw_headers and isinstance(raw_headers[0], list))
        else [list(raw_headers)]
    )

    raw_rows = table_rec.get("rows") or table_rec.get("rows_json") or []
    col_roles = table_rec.get("column_roles")
    amt_indices = table_rec.get("amount_col_indices")
    if col_roles is None or amt_indices is None:
        col_roles, amt_indices = classify_table_columns(headers_2d, raw_rows)

    hydrated_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if isinstance(row, dict) and "label" in row and "values" in row:
            hydrated_rows.append({"label": row["label"], "values": list(row["values"])})
        elif isinstance(row, list) and row:
            label = str(row[0].get("raw_text") or "")
            vals: list[Any] = []
            for col_offset, cell in enumerate(row[1:], start=1):
                role = col_roles[col_offset] if col_offset < len(col_roles) else "other"
                if role in {"note_ref", "code"}:
                    raw_txt = (
                        str(cell.get("raw_text") or "").strip()
                        if isinstance(cell, dict)
                        else str(cell or "").strip()
                    )
                    vals.append(raw_txt or None)
                else:
                    vals.append(cell.get("numeric_value") if isinstance(cell, dict) else cell)
            hydrated_rows.append({"label": label, "values": vals})

    return {
        "table_id": str(table_rec["table_id"]),
        "title": str(table_rec.get("title") or table_rec.get("table_title") or ""),
        "sub_doc_slot": str(table_rec.get("sub_doc_slot") or ""),
        "page_from": int(table_rec.get("page_from") or 1),
        "page_to": int(table_rec.get("page_to") or table_rec.get("page_from") or 1),
        "currency": str(table_rec.get("currency") or "VND"),
        "unit_raw": str(table_rec.get("unit_raw") or "unspecified"),
        "unit_multiplier": float(table_rec.get("unit_multiplier") or 1.0),
        "verification_status": str(table_rec.get("verification_status") or "TABLE_UNVERIFIED"),
        "sanity_error_ratio": float(table_rec.get("sanity_error_ratio") or 0.0),
        "headers": headers_2d,
        "rows": hydrated_rows,
        "column_roles": list(col_roles),
        "amount_col_indices": list(amt_indices),
        "footnotes_text": str(table_rec.get("footnotes_text") or ""),
        "table_hash": str(table_rec.get("table_hash") or ""),
    }


_VN_THOUSANDS_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:\.\d{3})+)(?![.\d])")
_BM25_TOKEN_RE = re.compile(r"(?:\d+(?:[.,]\d+)*%?|[^\W\d_]+)", re.UNICODE)


def vn_dot_bm25_tokenize(text_val: str) -> list[str]:
    """Normalize Vietnamese dot-thousands (16.012.813 -> 16012813) while preserving decimals & section codes."""
    nfc = unicodedata.normalize("NFC", text_val or "").lower()
    cleaned = _VN_THOUSANDS_RE.sub(lambda m: m.group(1).replace(".", ""), nfc)
    return _BM25_TOKEN_RE.findall(cleaned)


def table_safe_shingles(
    text_val: str,
    *,
    fiscal_year: int,
    sub_doc_slot: str,
    table_id: str | None,
) -> set[str]:
    norm = normalize_ws(text_val).lower()
    prefix = f"fy{fiscal_year}|{sub_doc_slot}|{table_id or 'prose'}|"
    base = prefix + norm
    if len(base) < 6:
        return {base}
    return {base[i : i + 6] for i in range(len(base) - 5)}


def table_safe_jaccard_should_merge(
    sec_a: dict[str, Any],
    sec_b: dict[str, Any],
    threshold: float = 0.98,
) -> bool:
    """Only allow near-duplicate merge on tables when period, slot, AND numeric sequences match."""
    if sec_a.get("table_id") or sec_b.get("table_id"):
        if (sec_a.get("fiscal_year") != sec_b.get("fiscal_year")) or (
            sec_a.get("sub_doc_slot") != sec_b.get("sub_doc_slot")
        ):
            return False
        nums_a = re.findall(r"-?\d+(?:[.,]\d+)*", sec_a["text"])
        nums_b = re.findall(r"-?\d+(?:[.,]\d+)*", sec_b["text"])
        if nums_a != nums_b:
            return False
    sa = table_safe_shingles(
        sec_a["text"],
        fiscal_year=sec_a.get("fiscal_year", 0),
        sub_doc_slot=sec_a.get("sub_doc_slot", ""),
        table_id=sec_a.get("table_id"),
    )
    sb = table_safe_shingles(
        sec_b["text"],
        fiscal_year=sec_b.get("fiscal_year", 0),
        sub_doc_slot=sec_b.get("sub_doc_slot", ""),
        table_id=sec_b.get("table_id"),
    )
    jac = len(sa & sb) / max(1, len(sa | sb))
    return jac >= threshold


@dataclass(slots=True)
class ExtractedTableRecord:
    table_id: str
    project_id: str
    document_version_id: str
    sub_doc_slot: str
    fiscal_year: int
    fiscal_quarter: int
    table_title: str
    section_path: str
    page_from: int
    page_to: int
    currency: str
    unit_raw: str
    unit_multiplier: float
    headers: list[str]
    rows: list[list[dict[str, Any]]]
    footnotes_text: str
    sanity_error_ratio: float
    verification_status: VerificationStatus
    table_hash: str
    valid_from: datetime

    @classmethod
    def from_extraction(
        cls,
        *,
        project_id: str,
        document_version_id: str,
        sub_doc_slot: str,
        fiscal_year: int,
        fiscal_quarter: int = 0,
        table_title: str,
        section_path: str = "",
        page_from: int,
        page_to: int,
        extracted: dict[str, Any],
        valid_from: datetime,
        footnotes_text: str = "",
        table_id: str | None = None,
        virtual_side: str = "FULL",
        table_idx: int = 0,
    ) -> ExtractedTableRecord:
        gate = extracted.get("sanity_gate")
        if gate is None:
            gate = run_arithmetic_sanity_gate(extracted["headers"], extracted["rows"])
        t_hash = compute_table_hash(extracted["rows"])
        tid = table_id or str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{project_id.upper()}:{document_version_id}:{fiscal_year}:Q{fiscal_quarter}:"
                f"{sub_doc_slot}:{table_title}:{page_from}:{virtual_side}:{table_idx}",
            )
        )
        return cls(
            table_id=tid,
            project_id=project_id.upper(),
            document_version_id=document_version_id,
            sub_doc_slot=sub_doc_slot,
            fiscal_year=fiscal_year,
            fiscal_quarter=fiscal_quarter,
            table_title=table_title,
            section_path=section_path,
            page_from=page_from,
            page_to=page_to,
            currency=str(extracted.get("currency") or "VND"),
            unit_raw=str(extracted.get("unit_raw") or "unspecified"),
            unit_multiplier=float(extracted.get("unit_multiplier") or 1.0),
            headers=list(extracted["headers"]),
            rows=list(extracted["rows"]),
            footnotes_text=footnotes_text,
            sanity_error_ratio=float(gate["sanity_error_ratio"]),
            verification_status=gate["verification_status"],
            table_hash=t_hash,
            valid_from=valid_from if valid_from.tzinfo else valid_from.replace(tzinfo=UTC),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "table_id": self.table_id,
            "project_id": self.project_id,
            "document_version_id": self.document_version_id,
            "sub_doc_slot": self.sub_doc_slot,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "table_title": self.table_title,
            "section_path": self.section_path,
            "page_from": self.page_from,
            "page_to": self.page_to,
            "currency": self.currency,
            "unit_raw": self.unit_raw,
            "unit_multiplier": self.unit_multiplier,
            "headers": self.headers,
            "rows": self.rows,
            "footnotes_text": self.footnotes_text,
            "sanity_error_ratio": self.sanity_error_ratio,
            "verification_status": self.verification_status,
            "table_hash": self.table_hash,
            "valid_from": self.valid_from,
        }

    def to_hydrated_table(self) -> dict[str, Any]:
        return to_hydrated_table(self.to_dict())

    def to_search_cards(self) -> list[dict[str, Any]]:
        return build_row_linearized_cards(self.to_dict())

    def to_grouped_search_cards(self, *, rows_per_card: int = 6) -> list[dict[str, Any]]:
        return build_grouped_table_search_cards(self.to_dict(), rows_per_card=rows_per_card)


class TableRepositoryProtocol(Protocol):
    """Async repository protocol for `sag_extracted_tables` persistence & PIT hydration."""

    async def upsert_table(self, record: ExtractedTableRecord) -> str: ...

    async def get_hydrated_table(
        self,
        table_id: str,
        *,
        as_of_ts: datetime | None = None,
    ) -> dict[str, Any] | None: ...

    async def list_hydrated_tables(
        self,
        project_id: str,
        *,
        as_of_ts: datetime,
        sub_doc_slot: str | None = None,
    ) -> list[dict[str, Any]]: ...


class InMemoryTableRepository:
    """Offline in-memory repository for deterministic unit testing without Postgres."""

    def __init__(self) -> None:
        self._tables: dict[str, ExtractedTableRecord] = {}

    async def upsert_table(self, record: ExtractedTableRecord) -> str:
        self._tables[record.table_id] = record
        return record.table_id

    async def get_hydrated_table(
        self,
        table_id: str,
        *,
        as_of_ts: datetime | None = None,
    ) -> dict[str, Any] | None:
        rec = self._tables.get(table_id)
        if rec is None:
            return None
        if as_of_ts is not None:
            cutoff = as_of_ts if as_of_ts.tzinfo else as_of_ts.replace(tzinfo=UTC)
            if rec.valid_from > cutoff:
                return None
        return rec.to_hydrated_table()

    async def list_hydrated_tables(
        self,
        project_id: str,
        *,
        as_of_ts: datetime,
        sub_doc_slot: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = as_of_ts if as_of_ts.tzinfo else as_of_ts.replace(tzinfo=UTC)
        proj = project_id.upper()
        matched = [
            rec.to_hydrated_table()
            for rec in self._tables.values()
            if rec.project_id == proj
            and rec.valid_from <= cutoff
            and (sub_doc_slot is None or rec.sub_doc_slot == sub_doc_slot)
        ]
        return matched


class SqlTableRepository:
    """Async SQLAlchemy persistence helper compatible with `sag_extracted_tables` (migration 0013)."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_table(self, record: ExtractedTableRecord) -> str:
        stmt = text(
            """
            INSERT INTO sag_extracted_tables (
                table_id, project_id, document_version_id, sub_doc_slot,
                fiscal_year, fiscal_quarter, table_title, section_path,
                page_from, page_to, currency, unit_raw, unit_multiplier,
                headers_json, rows_json, footnotes_text,
                sanity_error_ratio, verification_status, table_hash, valid_from
            ) VALUES (
                :table_id, :project_id, :document_version_id, :sub_doc_slot,
                :fiscal_year, :fiscal_quarter, :table_title, :section_path,
                :page_from, :page_to, :currency, :unit_raw, :unit_multiplier,
                CAST(:headers_json AS jsonb), CAST(:rows_json AS jsonb), :footnotes_text,
                :sanity_error_ratio, :verification_status, :table_hash, :valid_from
            )
            ON CONFLICT (table_id) DO UPDATE SET
                document_version_id = EXCLUDED.document_version_id,
                sub_doc_slot = EXCLUDED.sub_doc_slot,
                fiscal_year = EXCLUDED.fiscal_year,
                fiscal_quarter = EXCLUDED.fiscal_quarter,
                table_title = EXCLUDED.table_title,
                section_path = EXCLUDED.section_path,
                page_from = EXCLUDED.page_from,
                page_to = EXCLUDED.page_to,
                currency = EXCLUDED.currency,
                unit_raw = EXCLUDED.unit_raw,
                unit_multiplier = EXCLUDED.unit_multiplier,
                headers_json = EXCLUDED.headers_json,
                rows_json = EXCLUDED.rows_json,
                footnotes_text = EXCLUDED.footnotes_text,
                sanity_error_ratio = EXCLUDED.sanity_error_ratio,
                verification_status = EXCLUDED.verification_status,
                table_hash = EXCLUDED.table_hash,
                valid_from = EXCLUDED.valid_from
            """
        )
        await self._session.execute(
            stmt,
            {
                "table_id": record.table_id,
                "project_id": record.project_id,
                "document_version_id": record.document_version_id,
                "sub_doc_slot": record.sub_doc_slot,
                "fiscal_year": record.fiscal_year,
                "fiscal_quarter": record.fiscal_quarter,
                "table_title": record.table_title,
                "section_path": record.section_path,
                "page_from": record.page_from,
                "page_to": record.page_to,
                "currency": record.currency,
                "unit_raw": record.unit_raw,
                "unit_multiplier": record.unit_multiplier,
                "headers_json": json.dumps(record.headers, ensure_ascii=False),
                "rows_json": json.dumps(record.rows, ensure_ascii=False),
                "footnotes_text": record.footnotes_text,
                "sanity_error_ratio": record.sanity_error_ratio,
                "verification_status": record.verification_status,
                "table_hash": record.table_hash,
                "valid_from": record.valid_from,
            },
        )
        return record.table_id

    async def get_hydrated_table(
        self,
        table_id: str,
        *,
        as_of_ts: datetime | None = None,
    ) -> dict[str, Any] | None:
        if as_of_ts is not None:
            cutoff = as_of_ts if as_of_ts.tzinfo else as_of_ts.replace(tzinfo=UTC)
            stmt = text(
                """
                SELECT table_id, table_title, sub_doc_slot, page_from, page_to,
                       currency, unit_raw, unit_multiplier, verification_status,
                       sanity_error_ratio, headers_json, rows_json, footnotes_text, table_hash
                FROM sag_extracted_tables
                WHERE table_id = :table_id AND valid_from <= :as_of_ts
                LIMIT 1
                """
            )
            res = await self._session.execute(stmt, {"table_id": table_id, "as_of_ts": cutoff})
        else:
            stmt = text(
                """
                SELECT table_id, table_title, sub_doc_slot, page_from, page_to,
                       currency, unit_raw, unit_multiplier, verification_status,
                       sanity_error_ratio, headers_json, rows_json, footnotes_text, table_hash
                FROM sag_extracted_tables
                WHERE table_id = :table_id
                LIMIT 1
                """
            )
            res = await self._session.execute(stmt, {"table_id": table_id})
        row = res.mappings().first()
        if row is None:
            return None
        return to_hydrated_table(dict(row))

    async def list_hydrated_tables(
        self,
        project_id: str,
        *,
        as_of_ts: datetime,
        sub_doc_slot: str | None = None,
    ) -> list[dict[str, Any]]:
        cutoff = as_of_ts if as_of_ts.tzinfo else as_of_ts.replace(tzinfo=UTC)
        if sub_doc_slot:
            stmt = text(
                """
                SELECT table_id, table_title, sub_doc_slot, page_from, page_to,
                       currency, unit_raw, unit_multiplier, verification_status,
                       sanity_error_ratio, headers_json, rows_json, footnotes_text, table_hash
                FROM sag_extracted_tables
                WHERE project_id = :project_id AND valid_from <= :as_of_ts
                  AND sub_doc_slot = :sub_doc_slot
                ORDER BY valid_from DESC, page_from ASC
                """
            )
            params = {
                "project_id": project_id.upper(),
                "as_of_ts": cutoff,
                "sub_doc_slot": sub_doc_slot,
            }
        else:
            stmt = text(
                """
                SELECT table_id, table_title, sub_doc_slot, page_from, page_to,
                       currency, unit_raw, unit_multiplier, verification_status,
                       sanity_error_ratio, headers_json, rows_json, footnotes_text, table_hash
                FROM sag_extracted_tables
                WHERE project_id = :project_id AND valid_from <= :as_of_ts
                ORDER BY valid_from DESC, page_from ASC
                """
            )
            params = {"project_id": project_id.upper(), "as_of_ts": cutoff}
        res = await self._session.execute(stmt, params)
        return [to_hydrated_table(dict(r)) for r in res.mappings().all()]


_PAGE_MARKER_SPLIT_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->", re.IGNORECASE)
_HTML_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)
_TABLE_NUMERIC_CUE_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3}){2,})(?!\d)")


_DATE_OR_UNIT_LINE_RE = re.compile(
    r"^(?:[\(\[]?\s*(?:đơn\s+vị(?:\s+tính)?|unit)\s*[:\-]?.*|"
    r"(?:ngày|tại\s+ngày|cho\s+năm\s+tài\s+chính)\s+.*|"
    r"\d{1,2}[./\-]\d{1,2}[./\-]\d{2,4}|"
    r"\(?-?[\d.,\s%]+\)?)$",
    re.IGNORECASE,
)


def _infer_table_title(
    context_text: str,
    *,
    fallback: str,
    prefer_top: bool = False,
) -> str:
    """Pick the cleanest heading/title line for a financial table, never picking raw numbers or units."""
    raw_lines = [
        ln.strip()
        for ln in (context_text or "").splitlines()
        if ln.strip() and not ln.strip().startswith("<")
    ]
    # 1. Prioritize normalized Markdown headings (`#` / `##`) first
    md_headings = [
        normalize_ws(ln.lstrip("#").strip())
        for ln in raw_lines
        if ln.startswith("#")
    ]
    candidates_md = md_headings if prefer_top else list(reversed(md_headings))
    for h in candidates_md:
        if 5 <= len(h) <= 130 and not _DATE_OR_UNIT_LINE_RE.match(h):
            return h[:140]

    # 2. Fallback to clean descriptive lines (top-down for page-level digital tables, bottom-up for HTML context)
    scan_lines = raw_lines[:18] if prefer_top else list(reversed(raw_lines[-10:]))
    for raw_ln in scan_lines:
        ln = normalize_ws(raw_ln.lstrip("#").strip())
        low = ln.lower()
        if len(ln) < 5 or len(ln) > 130:
            continue
        if _DATE_OR_UNIT_LINE_RE.match(ln):
            continue
        if any(
            skip in low
            for skip in (
                "đơn vị tính",
                "đơn vị:",
                "mẫu số",
                "ban hành theo",
                "bộ tài chính",
                "các thuyết minh",
                "số cuối năm",
                "số đầu năm",
                "tài sản ngắn hạn",
            )
        ):
            continue
        if len(_TABLE_NUMERIC_CUE_RE.findall(ln)) >= 1:
            continue
        alpha_n = sum(1 for c in ln if c.isalpha())
        if alpha_n < 4:
            continue
        return ln[:140]
    return fallback[:140]


def _split_markdown_by_page_marker(markdown_content: str) -> dict[int, str]:
    """Parse `<!-- PAGE N -->` sections from bundle markdown into `{page_no: body}`."""
    if not markdown_content:
        return {}
    matches = list(_PAGE_MARKER_SPLIT_RE.finditer(markdown_content))
    if not matches:
        return {}
    out: dict[int, str] = {}
    for idx, m in enumerate(matches):
        pno = int(m.group(1))
        start = m.end()
        end = matches[idx + 1].start() if idx + 1 < len(matches) else len(markdown_content)
        body = markdown_content[start:end].strip()
        if body:
            out[pno] = body
    return out


def _build_page_section_paths(markdown_content: str, sub_doc_slot: str) -> dict[int, str]:
    """Track active Markdown heading hierarchy (`#`/`##`/`###`) across `<!-- PAGE N -->` markers."""
    page_md_map = _split_markdown_by_page_marker(markdown_content)
    stack: list[tuple[int, str]] = []
    out: dict[int, str] = {}
    for pno in sorted(page_md_map):
        body = page_md_map[pno]
        for raw_ln in body.splitlines():
            s = raw_ln.strip()
            m = re.match(r"^(#{1,3})\s+(.+)$", s)
            if not m:
                continue
            lvl = len(m.group(1))
            title = normalize_ws(m.group(2))
            if not title:
                continue
            while stack and stack[-1][0] >= lvl:
                stack.pop()
            stack.append((lvl, title))
        if stack:
            parts = [sub_doc_slot] + [t for _, t in stack]
            path = " > ".join(parts)
            out[pno] = path[:512]
        else:
            out[pno] = f"{sub_doc_slot} / P{pno}"
    return out


def extract_tables_from_document(
    *,
    pdf_bytes: bytes | None,
    project_id: str,
    document_version_id: str,
    sub_doc_slot: str,
    fiscal_year: int,
    fiscal_quarter: int = 0,
    page_numbers: list[int],
    markdown_content: str,
    valid_from: datetime,
    left_sidebar_x: float = 0.0,
    two_col_crops: dict[int, tuple[float, float]] | None = None,
    max_pages: int = 250,
) -> list[ExtractedTableRecord]:
    """End-to-end table extractor wiring Dual-Representation Engine into HOSE batch ingest.

    1. Extracts MinerU HTML `<table>` blocks present in OCR'd Markdown (`build_matrix_from_html_table`)
       and stitches cross-page HTML continuations via `stitch_cross_page_tables`.
    2. For remaining numeric digital pages, extracts ALL valid 2D matrices via `pdfplumber` +
       `extract_tables_with_lattice_fallback` (A3 virtual-page aware) and stitches cross-page
       `(Tiếp theo)` tables via `stitch_cross_page_tables`.
    """
    records: list[ExtractedTableRecord] = []
    page_md_map = _split_markdown_by_page_marker(markdown_content)
    page_sec_paths = _build_page_section_paths(markdown_content, sub_doc_slot)
    # Only allow formal explicit unit headers (never narrative prose like '1.300 triệu USD'
    # in BCTN intro) as doc-level fallback, and only on dedicated financial-statement slots.
    doc_currency, doc_unit_mult, doc_unit_raw = (
        detect_explicit_unit_declaration(markdown_content[:8000])
        if sub_doc_slot in ("BCTC_AUDITED", "QUARTER_NOTES")
        else ("VND", 1.0, "unspecified")
    )
    html_handled_pages: set[int] = set()

    # Path 1: MinerU HTML `<table>` blocks inside Markdown + cross-page HTML stitching
    raw_html_tables: list[dict[str, Any]] = []
    for pno in sorted(page_numbers):
        body = page_md_map.get(pno, "")
        if not body or "<table" not in body.lower():
            continue
        html_matches = list(_HTML_TABLE_BLOCK_RE.finditer(body))
        if not html_matches:
            continue
        html_handled_pages.add(pno)
        for tbl_idx, tm in enumerate(html_matches):
            ctx_before = body[max(0, tm.start() - 600) : tm.start()]
            extracted = build_matrix_from_html_table(tm.group(0), context_text=ctx_before)
            rows = extracted.get("rows") or []
            num_cells = sum(
                1
                for r in rows
                for c in r[1:]
                if c.get("numeric_value") is not None
            )
            if len(rows) < 2 or num_cells < 4:
                continue
            extracted["virtual_seq"] = pno
            extracted["sub_doc_slot"] = sub_doc_slot
            extracted["page_from"] = pno
            extracted["page_to"] = pno
            extracted["side"] = "HTML"
            extracted["ctx_before"] = ctx_before
            extracted["section_path"] = page_sec_paths.get(pno, "")
            extracted["raw_table_idx"] = tbl_idx
            raw_html_tables.append(extracted)

    if raw_html_tables:
        stitched_html = stitch_cross_page_tables(raw_html_tables)
        stitched_html, _ = deduplicate_english_mirror_tables(stitched_html)
        last_unit: tuple[str, float, str] | None = None
        last_unit_page: int | None = None
        for idx, tbl in enumerate(stitched_html):
            p_from = int(tbl.get("page_from") or 1)
            p_to = int(tbl.get("page_to") or p_from)
            if tbl.get("stitched_from_pages"):
                tbl["sanity_gate"] = run_arithmetic_sanity_gate(tbl["headers"], tbl["rows"])
            ctx_before = str(tbl.get("ctx_before") or page_md_map.get(p_from, ""))
            is_non_monetary = _table_is_non_monetary(tbl)
            if is_non_monetary:
                tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = ("VND", 1.0, "unspecified")
            elif str(tbl.get("unit_raw") or "unspecified") == "unspecified":
                p_cur, p_mult, p_raw = detect_explicit_unit_declaration(page_md_map.get(p_from, ""))
                if p_raw == "unspecified":
                    p_cur, p_mult, p_raw = detect_unit_and_currency(ctx_before[-400:])
                if p_raw != "unspecified":
                    tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = p_cur, p_mult, p_raw
                elif last_unit is not None and last_unit_page is not None and abs(p_from - last_unit_page) <= 2:
                    tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = last_unit
                elif doc_unit_raw != "unspecified":
                    tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = (
                        doc_currency,
                        doc_unit_mult,
                        doc_unit_raw,
                    )
            if not is_non_monetary and str(tbl.get("unit_raw") or "unspecified") != "unspecified":
                last_unit = (
                    str(tbl.get("currency") or "VND"),
                    float(tbl.get("unit_multiplier") or 1.0),
                    str(tbl.get("unit_raw")),
                )
                last_unit_page = p_to
            title = _infer_table_title(
                ctx_before,
                fallback=f"{sub_doc_slot} Table P{p_from}#{idx + 1}",
            )
            sec_path = page_sec_paths.get(
                p_from,
                f"{sub_doc_slot} / P{p_from}" + (f"-P{p_to}" if p_to != p_from else ""),
            )
            records.append(
                ExtractedTableRecord.from_extraction(
                    project_id=project_id,
                    document_version_id=document_version_id,
                    sub_doc_slot=sub_doc_slot,
                    fiscal_year=fiscal_year,
                    fiscal_quarter=fiscal_quarter,
                    table_title=title,
                    section_path=sec_path,
                    page_from=p_from,
                    page_to=p_to,
                    extracted=tbl,
                    valid_from=valid_from,
                    virtual_side="HTML",
                    table_idx=idx,
                )
            )

    # Path 2: Digital PDF extraction via pdfplumber + A3 virtual-page splitting + cross-page stitcher
    digital_candidates = [
        pno
        for pno in sorted(page_numbers)
        if pno not in html_handled_pages
        and len(_TABLE_NUMERIC_CUE_RE.findall(page_md_map.get(pno, ""))) >= 4
    ][:max_pages]

    if pdf_bytes and digital_candidates:
        try:
            import pdfplumber
        except ImportError:
            pdfplumber = None  # type: ignore[assignment]

        if pdfplumber is not None:
            raw_page_tables: list[dict[str, Any]] = []
            virtual_seq = 0
            with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
                total_p = len(pdf.pages)
                for pno in digital_candidates:
                    if not (1 <= pno <= total_p):
                        continue
                    pg = pdf.pages[pno - 1]
                    w, h = float(pg.width), float(pg.height)
                    eff_sidebar = (
                        left_sidebar_x if (w > h * 1.25 and 0.0 < left_sidebar_x < w * 0.30) else 0.0
                    )
                    vpages = split_virtual_pages(
                        pg,
                        left_sidebar_x=eff_sidebar,
                        two_col_crop=(two_col_crops or {}).get(pno),
                    )
                    for vp in vpages:
                        virtual_seq += 1
                        try:
                            cropped = pg.crop(vp.bbox)
                            extracted_list = extract_tables_with_lattice_fallback(cropped)
                        except Exception:  # noqa: BLE001
                            continue
                        for sub_idx, extracted in enumerate(extracted_list):
                            rows = extracted.get("rows") or []
                            num_cells = sum(
                                1
                                for r in rows
                                for c in r[1:]
                                if c.get("numeric_value") is not None
                            )
                            if len(rows) < 2 or num_cells < 4:
                                continue
                            extracted["virtual_seq"] = virtual_seq
                            extracted["sub_doc_slot"] = sub_doc_slot
                            extracted["page_from"] = pno
                            extracted["page_to"] = pno
                            extracted["side"] = vp.side
                            extracted["section_path"] = page_sec_paths.get(pno, "")
                            extracted["raw_table_idx"] = sub_idx
                            raw_page_tables.append(extracted)

            stitched = stitch_cross_page_tables(raw_page_tables)
            stitched, _ = deduplicate_english_mirror_tables(stitched)
            last_unit_dig: tuple[str, float, str] | None = None
            last_unit_dig_page: int | None = None
            for idx, tbl in enumerate(stitched):
                p_from = int(tbl.get("page_from") or digital_candidates[0])
                p_to = int(tbl.get("page_to") or p_from)
                # Re-run sanity gate if table was stitched across pages
                if tbl.get("stitched_from_pages"):
                    tbl["sanity_gate"] = run_arithmetic_sanity_gate(tbl["headers"], tbl["rows"])
                ctx_text = page_md_map.get(p_from, "") or tbl.get("context_header", "")
                is_non_monetary_dig = _table_is_non_monetary(tbl)
                if is_non_monetary_dig:
                    tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = ("VND", 1.0, "unspecified")
                elif str(tbl.get("unit_raw") or "unspecified") == "unspecified":
                    p_cur, p_mult, p_raw = detect_explicit_unit_declaration(ctx_text)
                    if p_raw == "unspecified":
                        p_cur, p_mult, p_raw = detect_unit_and_currency(str(tbl.get("context_header") or "")[:500])
                    if p_raw != "unspecified":
                        tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = p_cur, p_mult, p_raw
                    elif (
                        last_unit_dig is not None
                        and last_unit_dig_page is not None
                        and abs(p_from - last_unit_dig_page) <= 2
                    ):
                        tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = last_unit_dig
                    elif doc_unit_raw != "unspecified":
                        tbl["currency"], tbl["unit_multiplier"], tbl["unit_raw"] = (
                            doc_currency,
                            doc_unit_mult,
                            doc_unit_raw,
                        )
                if not is_non_monetary_dig and str(tbl.get("unit_raw") or "unspecified") != "unspecified":
                    last_unit_dig = (
                        str(tbl.get("currency") or "VND"),
                        float(tbl.get("unit_multiplier") or 1.0),
                        str(tbl.get("unit_raw")),
                    )
                    last_unit_dig_page = p_to
                title = _infer_table_title(
                    ctx_text,
                    fallback=f"{sub_doc_slot} Table P{p_from}#{idx + 1}",
                    prefer_top=True,
                )
                sec_path = page_sec_paths.get(
                    p_from,
                    f"{sub_doc_slot} / P{p_from}" + (f"-P{p_to}" if p_to != p_from else ""),
                )
                records.append(
                    ExtractedTableRecord.from_extraction(
                        project_id=project_id,
                        document_version_id=document_version_id,
                        sub_doc_slot=sub_doc_slot,
                        fiscal_year=fiscal_year,
                        fiscal_quarter=fiscal_quarter,
                        table_title=title,
                        section_path=sec_path,
                        page_from=p_from,
                        page_to=p_to,
                        extracted=tbl,
                        valid_from=valid_from,
                        virtual_side=str(tbl.get("side") or "FULL"),
                        table_idx=idx,
                    )
                )

    if len(records) >= 2:
        records, _ = deduplicate_english_mirror_tables(records)
    return records
