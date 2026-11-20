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
import json
import re
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any, Literal, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.research.input_inspector import (
    normalize_ctg_font_quirks,
    normalize_ws,
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


def detect_unit_and_currency(context_text: str) -> tuple[str, float, str]:
    """Scan lines above table / top-right header for (currency, unit_multiplier, unit_raw)."""
    cleaned = normalize_ctg_font_quirks(context_text)
    low = cleaned.lower()
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
        r"(?:đơn\s*vị(?:\s*tính)?\s*[:\-]?\s*(?:đồng|vnd|vnđ)|\(đơn\s*vị\s*:\s*(?:đồng|vnd|vnđ)\))",
        low,
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
                self._cell_buf.append(" ")

        def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() == "br" and self._in_cell:
                self._cell_buf.append(" ")

        def handle_data(self, data: str) -> None:
            if self._in_cell:
                self._cell_buf.append(data)

        def handle_endtag(self, tag: str) -> None:
            t = tag.lower()
            if t == "thead":
                self._in_thead = False
            elif t in ("td", "th") and self._in_cell and self._cur_row is not None:
                self._cur_row.append(
                    (normalize_ws("".join(self._cell_buf)), self._rs, self._cs, self._is_th)
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
        if len(t) <= 4 and t.isdigit() and int(t) in range(1990, 2035):
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
    stopped_at_footer = False

    for ln in lines:
        line_top = round(float(ln[0]["top"]), 1)
        line_text = normalize_ctg_font_quirks(" ".join(w["text"] for w in ln))
        num_slots: list[str | None] = [None] * n_num_cols
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
            seen_first_data_row = True
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

    headers = ["Chỉ tiêu"] + (
        expected_col_headers
        if expected_col_headers and len(expected_col_headers) == n_num_cols
        else [f"Col_{i + 1}" for i in range(n_num_cols)]
    )
    matrix_rows: list[list[dict[str, Any]]] = []
    for r in parsed_rows:
        cells: list[dict[str, Any]] = [{"raw_text": r["label"], "numeric_value": None}]
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


def extract_table_with_lattice_fallback(
    sub_page: Any,
    *,
    y_min: float | None = None,
    y_max: float | None = None,
    expected_col_headers: list[str] | None = None,
) -> dict[str, Any]:
    """Try pdfplumber lattice extraction first; self-heal via coordinate reconstructor if sanity gate fails."""
    page_text = ""
    if hasattr(sub_page, "extract_text"):
        page_text = sub_page.extract_text() or ""
    lat_tables = sub_page.extract_tables() if hasattr(sub_page, "extract_tables") else []

    if lat_tables and len(lat_tables[0]) >= 2:
        raw_grid = lat_tables[0]
        raw_nums = [
            str(cell or "")
            for r in raw_grid[1:]
            for cell in r[1:]
            if cell
        ]
        locale = detect_table_locale(raw_nums)
        currency, unit_mult, unit_raw = detect_unit_and_currency(page_text)
        headers = [normalize_ws(str(h or f"Col_{i}")) for i, h in enumerate(raw_grid[0])]
        if expected_col_headers and len(headers) == len(expected_col_headers) + 1:
            headers = ["Chỉ tiêu"] + list(expected_col_headers)
        rows = [
            [{"raw_text": normalize_ws(str(r[0] or "Tổng cộng")), "numeric_value": None}]
            + [
                {
                    "raw_text": normalize_ws(str(cell or "")),
                    "numeric_value": parse_vn_number(cell, locale=locale),
                }
                for cell in r[1:]
            ]
            for r in raw_grid[1:]
        ]
        gate = run_arithmetic_sanity_gate(headers, rows)
        if gate["verification_status"] == "VERIFIED":
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
                "needs_mineru_fallback": False,
            }

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
    return reconstructed


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


def run_arithmetic_sanity_gate(
    headers: list[str],
    rows: list[list[dict[str, Any]]],
    total_row_idx: int | None = None,
    sum_row_indices: list[int] | None = None,
    tolerance: float = 0.005,
) -> dict[str, Any]:
    """Step 4 Arithmetic & Structural Sanity Gate (<= 0.5% tolerance; right-col guard)."""
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
            lbl = (rows[idx][0]["raw_text"] or "").lower()
            if any(
                k in lbl
                for k in (
                    "tổng cộng",
                    "cộng",
                    "tổng tài sản",
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

    col_checks: list[dict[str, Any]] = []
    max_err = 0.0
    for c in range(1, n_cols):
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

    return {
        "verification_status": status,
        "sanity_error_ratio": round(max_err, 6),
        "right_col_coverage": round(right_col_coverage, 3),
        "column_checks": col_checks,
    }


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
        for c in range(1, n_cols):
            tgt = rows[target_idx][c]["numeric_value"]
            comps = [rows[ci][c]["numeric_value"] for ci in component_indices]
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


def stitch_cross_page_tables(tables: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Step 2 Cross-Page Table Stitcher: merges consecutive tables, strips repeated header/carryover rows."""
    if not tables:
        return []
    stitched: list[dict[str, Any]] = [dict(tables[0])]
    for tbl in tables[1:]:
        prev = stitched[-1]
        consecutive = tbl["virtual_seq"] == prev["virtual_seq"] + 1
        same_slot = tbl["sub_doc_slot"] == prev["sub_doc_slot"]
        col_diff = abs(len(tbl["headers"]) - len(prev["headers"])) <= 1
        h_prev = {h.lower() for h in prev["headers"]}
        h_cur = {h.lower() for h in tbl["headers"]}
        sig_jaccard = len(h_prev & h_cur) / max(1, len(h_prev | h_cur))
        ctx_low = (tbl.get("context_header") or "").lower()
        is_cont = ("tiếp theo" in ctx_low) or ("continued" in ctx_low)

        if consecutive and same_slot and col_diff and (is_cont or sig_jaccard >= 0.80):
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
    hydrated_rows: list[dict[str, Any]] = []
    for row in raw_rows:
        if isinstance(row, dict) and "label" in row and "values" in row:
            hydrated_rows.append({"label": row["label"], "values": list(row["values"])})
        elif isinstance(row, list) and row:
            label = str(row[0].get("raw_text") or "")
            vals = [cell.get("numeric_value") for cell in row[1:]]
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
    ) -> ExtractedTableRecord:
        gate = extracted.get("sanity_gate")
        if gate is None:
            gate = run_arithmetic_sanity_gate(extracted["headers"], extracted["rows"])
        t_hash = compute_table_hash(extracted["rows"])
        tid = table_id or str(
            uuid.uuid5(
                uuid.NAMESPACE_DNS,
                f"{project_id.upper()}:{fiscal_year}:Q{fiscal_quarter}:{sub_doc_slot}:{table_title}:{page_from}",
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
