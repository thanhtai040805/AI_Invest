"""Làm sạch Markdown OCR BCTC trước khi đưa vào zleap-sag để tạo chunk sạch theo heading."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_NUMBERED_HEADING_RE = re.compile(r"^(#{1,6})\s*\d")
_HTML_COMMENT_RE = re.compile(r"^\s*<!--.*?-->\s*$")
_MUC_LUC_RE = re.compile(r"^\s*(#{1,6}\s*)?MỤC LỤC\s*$")
_SIGNATURE_RE = re.compile(
    r"^\s*(\(?(Ký|ký),?\s*họ\s*tên\)?|Người phê duyệt|Người lập( bảng)?|"
    r"Kế toán trưởng|KẾ TOÁN TRƯỞNG|Tổng Giám đốc|TỔNG GIÁM ĐỐC|"
    r"Giám đốc|GIÁM ĐỐC|Chủ tịch|CHỦ TỊCH|Đại diện theo pháp luật|"
    r"M\.?S\.?D\.?N\s*[:：]|Mã số doanh nghiệp\s*[:：])"
)
_DATE_RE = re.compile(r"^\s*Ngày \d{1,2}(/|\s+tháng\s+)\d{1,2}")
_CITY_RE = re.compile(r"^\s*(Hà Nội,?\s*(Việt Nam)?|Thành phố Hồ Chí Minh|Việt Nam)\s*$")
_ALL_CAPS_FRAGMENT_RE = re.compile(r"^[\sA-ZÀÁẢÃẠĂẮẰẲẴẶÂẤẦẨẪẬĐÈÉẺẼẸÊẾỀỂỄỆÌÍỈĨỊÒÓỎÕỌÔỐỒỔỖỘƠỚỜỞỠỢÙÚỦŨỤƯỨỪỬỮỰỲÝỶỸỴ0-9.]+$")
_LATEX_JUNK_RE = re.compile(r"\\(frac|delta|partial|sum|theta|alpha|beta|omega)", re.IGNORECASE)

_STATEMENT_PATTERNS = (
    "bảng cân đối kế toán",
    "bang can doi ke toan",
    "can doi ke toan",
    "báo cáo kết quả hoạt động kinh doanh",
    "bao cao ket qua hoat dong kinh doanh",
    "báo cáo kết quả hoạt động",
    "bao cao ket qua hoat dong",
    "báo cáo lưu chuyển tiền tệ",
    "bao cao luu chuyen tien te",
    "báo cáo tình hình tài chính",
    "bao cao tinh hinh tai chinh",
    "báo cáo thay đổi vốn chủ sở hữu",
    "bao cao thay doi von chu so huu",
)

_FINANCIAL_REPORT_WRAPPER_PATTERNS = (
    "báo cáo tài chính riêng",
    "bao cao tai chinh rieng",
    "báo cáo tài chính hợp nhất",
    "bao cao tai chinh hop nhat",
)

_NOTES_BOUNDARY_PATTERNS = (
    "bản thuyết minh báo cáo tài chính",
    "ban thuyet minh bao cao tai chinh",
    "thuyết minh báo cáo tài chính",
    "thuyet minh bao cao tai chinh",
)

_ACCOUNTING_POLICY_PATTERNS = (
    "tóm tắt các chính sách kế toán chủ yếu",
    "các chính sách kế toán chủ yếu",
    "các chính sách kế toán áp dụng",
    "chính sách kế toán chủ yếu",
    "chuẩn mực và chế độ kế toán áp dụng",
    "quản trị rủi ro tài chính đối với ctck",
    "quản trị rủi ro tài chính",
)

_POLICY_CHANGE_KEYWORDS = (
    "thay đổi chính sách",
    "sửa đổi chính sách",
    "áp dụng mới",
    "thông tư 99",
    "thông tư 334",
    "phân loại lại",
    "điều chỉnh chính sách",
)


_IMAGE_RE = re.compile(r"!\[.*?\]\(.*?\)")
_FORM_CODE_RE = re.compile(
    r"^\s*(?:Mẫu\s*số\s*)?B\s*\d{1,3}\s*[-–/]\s*(?:DN|CT|HN|VN|TT\d+|QĐ\d+)(?:\s*/\s*HN)?\s*$",
    re.IGNORECASE,
)
_PAGE_NUM_RE = re.compile(r"^\s*(?:Trang\s*)?\d{1,3}(?:\s*/\s*\d{1,3})?\s*$", re.IGNORECASE)
_STAMP_SYMBOLS_RE = re.compile(r"^[\d\s./\-+*★\^\\_\|~©®:]{2,}$")
_STAMP_NOISE_RE = re.compile(
    r"^\s*(?:M\.?S\.?D\.?N|C\.?T\.?T?\.?N\.?H\.?H|C\.?I\.?T|C\.?T\.?N|C\.?T\.?I\.?NG|"
    r"ERNST\s*&\s*YOUNG|KPMG|PWC|DELOITTE|PHÒNG|VIETNAM|"
    r"YOUN|YOUNG|NAM|HÔ\s*CHÍ|HỒ\s*CHÍ|CHÍ\s*MINH|OB11|302-C|08118|11802|03008|RNST|PHÔT|PHÓH|THÁT|"
    r"C\.\s*UNG\s*M\s*HiN|Z\.?H\.?H\.?\s*★?|MINH\s*★|N\.?H\.?H\s*★?)\b.*$",
    re.IGNORECASE,
)
_GARBLED_HEADING_RE = re.compile(r"^#{1,6}\s*(?:[^\w\s]+|cn\s+n\s+anh)\s*$", re.IGNORECASE)
_ENGLISH_DUPLICATE_RE = re.compile(
    r"^\s*(?:The State Securities Commission|The Stock Exchange|State Securities Commission|Ho Chi Minh Stock Exchange|Ha Noi Stock Exchange|"
    r"Name of organization:|Ticker symbol:|Address:|Tel\.:|E-mail:|Contents of disclosure|"
    r"Disclosure of|This information was published|We certify|Attachment:|Report on|"
    r"LEGAL REPRESENTATIVE|Sign, write|Chief Executive Officer)\b",
    re.IGNORECASE,
)
_ENGLISH_SECTION_RE = re.compile(
    r"^\s*(?:#{1,6}\s*)?(?:financial statements?|financial position|income statement|"
    r"cash flow statement|notes to the financial statements?|management report|"
    r"related party transactions?)\b",
    re.IGNORECASE,
)
_ENGLISH_WORD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]*\b")
_VIETNAMESE_CHAR_RE = re.compile(r"[ĂÂÊÔƠƯĐăâêôơưđÀ-ỹà-ỹ]")
_ENGLISH_SENTENCE_WORDS = frozenset({
    "the", "and", "or", "of", "for", "from", "to", "in", "on", "with", "as", "by",
    "company", "group", "corporation", "management", "department", "participate", "training",
    "program", "programs", "workshop", "workshops", "seminar", "seminars", "legal", "regulations",
    "business", "activities", "financial", "governance", "report", "reports", "updated", "applicable",
    "meeting", "shareholders", "board", "directors", "committee", "audit", "general", "resolution", "decisions",
})


from html.parser import HTMLParser

_HTML_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


class HTMLTableToMarkdownParser(HTMLParser):
    """Parser bóc tách bảng HTML sang ma trận các ô văn bản chuẩn."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: dict[int, str] | None = None
        self._occupied: set[int] = set()
        self._next_col = 0
        self._rowspans: dict[int, tuple[str, int]] = {}
        self.current_cell: list[str] | None = None
        self.in_cell = False
        self._cell_col = 0
        self._cell_colspan = 1
        self._cell_rowspan = 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "tr":
            self.current_row = {column: value for column, (value, _remaining) in self._rowspans.items()}
            self._occupied = set(self.current_row)
            self._next_col = 0
        elif t in ("th", "td"):
            if self.current_row is None:
                return
            while self._next_col in self._occupied:
                self._next_col += 1
            attr_map = {key.lower(): value for key, value in attrs}
            try:
                self._cell_colspan = max(1, int(attr_map.get("colspan") or "1"))
            except ValueError:
                self._cell_colspan = 1
            try:
                self._cell_rowspan = max(1, int(attr_map.get("rowspan") or "1"))
            except ValueError:
                self._cell_rowspan = 1
            self._cell_col = self._next_col
            self.current_cell = []
            self.in_cell = True
        elif t == "br" and self.in_cell and self.current_cell is not None:
            self.current_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("th", "td"):
            cell_text = "".join(self.current_cell or []).replace("\n", " ").strip()
            cell_text = cell_text.replace("|", "\\|")
            if self.current_row is not None:
                for column in range(self._cell_col, self._cell_col + self._cell_colspan):
                    self.current_row[column] = cell_text if column == self._cell_col else ""
                    self._occupied.add(column)
                    if self._cell_rowspan > 1:
                        self._rowspans[column] = (cell_text if column == self._cell_col else "", self._cell_rowspan)
                self._next_col = self._cell_col + self._cell_colspan
            self.current_cell = None
            self.in_cell = False
        elif t == "tr":
            if self.current_row is not None and any(c.strip() for c in self.current_row.values()):
                width = max(self.current_row) + 1 if self.current_row else 0
                self.rows.append([self.current_row.get(column, "") for column in range(width)])
            self._rowspans = {
                column: (value, remaining - 1)
                for column, (value, remaining) in self._rowspans.items()
                if remaining > 1
            }
            self.current_row = None

    def handle_data(self, data: str) -> None:
        if self.in_cell and self.current_cell is not None:
            self.current_cell.append(data)


def _table_html_to_markdown(html_table: str) -> str:
    """Chuyển đổi 1 bảng HTML sang bảng Markdown (GFM) tối ưu cho RAG/LLM."""
    parser = HTMLTableToMarkdownParser()
    parser.feed(html_table)
    if not parser.rows:
        return ""
    max_cols = max(len(r) for r in parser.rows)
    if max_cols == 0:
        return ""
    norm_rows = [r + [""] * (max_cols - len(r)) for r in parser.rows]
    header = norm_rows[0]
    sep = [":---"] * max_cols
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join(sep) + " |"]
    for r in norm_rows[1:]:
        lines.append("| " + " | ".join(r) + " |")
    return "\n" + "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class CleanStats:
    lines_in: int = 0
    lines_out: int = 0
    html_comments: int = 0
    repeated_headings: int = 0
    toc_blocks: int = 0
    statement_sections: int = 0
    accounting_policy_sections: int = 0
    signature_lines: int = 0
    latex_junk: int = 0
    images_removed: int = 0
    stamps_removed: int = 0
    tables_converted: int = 0
    bilingual_duplicates_removed: int = 0
    english_lines_removed: int = 0
    english_inline_fragments_removed: int = 0


def clean_markdown(
    markdown: str,
    *,
    doc_role: str | None = None,
) -> tuple[str, CleanStats]:
    """Làm sạch Markdown OCR BCTC, trả về (nội dung sạch, thống kê)."""
    # 1. Chuyển đổi các khối bảng HTML sang Markdown Table trước khi tách dòng
    converted_tables = 0
    if "<table" in markdown.lower():
        def _repl(match: re.Match[str]) -> str:
            nonlocal converted_tables
            converted_tables += 1
            return _table_html_to_markdown(match.group(0))

        markdown = _HTML_TABLE_BLOCK_RE.sub(_repl, markdown)

    lines = markdown.splitlines()
    stats = CleanStats(lines_in=len(lines), tables_converted=converted_tables)
    lines, stats = _strip_images(lines, stats)
    lines, stats = _strip_bilingual_duplicates(lines, stats)
    lines, stats = _strip_english_duplicate_lines(lines, stats)
    lines, stats = _strip_inline_english_mirrors(lines, stats)
    lines, stats = _strip_audit_stamps_and_form_codes(lines, stats)
    lines, stats = _strip_html_comments(lines, stats)
    lines, stats = _strip_repeated_headings(lines, stats)
    lines, stats = _strip_toc(lines, stats)
    # Note: Statement tables (BCTC tables) are strictly preserved for deterministic compilers.
    # Accounting policies can explain restatements, estimates and risk. Keep
    # them in the canonical analytical input; filtering belongs to retrieval.
    lines, stats = _strip_trailing_signature(lines, stats)
    lines, stats = _strip_latex_junk(lines, stats)
    lines = _normalize_blank_lines(lines)
    return "\n".join(lines).strip() + "\n", replace(stats, lines_out=len(lines))


def _strip_bilingual_duplicates(
    lines: list[str], stats: CleanStats
) -> tuple[list[str], CleanStats]:
    """Remove common English mirror lines and bilingual heading suffixes without deleting English-only facts."""
    out: list[str] = []
    removed = 0
    english_section_level: int | None = None
    for line in lines:
        stripped = line.strip()
        heading = _HEADING_RE.match(stripped)
        if english_section_level is not None:
            if heading and len(heading.group(1)) <= english_section_level:
                english_section_level = None
            else:
                removed += 1
                continue
        if stripped and _ENGLISH_SECTION_RE.match(stripped):
            previous = out[-1].strip() if out else ""
            has_vietnamese_context = bool(_VIETNAMESE_CHAR_RE.search(previous))
            if has_vietnamese_context:
                english_section_level = len(heading.group(1)) if heading else 1
                removed += 1
                continue
        if stripped and _ENGLISH_DUPLICATE_RE.match(stripped):
            removed += 1
            continue
        previous = out[-1].strip() if out else ""
        has_vietnamese_context = bool(re.search(r"[À-ỹĐđ]", previous))
        if stripped and has_vietnamese_context and _ENGLISH_DUPLICATE_RE.match(stripped):
            removed += 1
            continue

        # Strip inline bilingual suffix in headings: e.g. "## Tiêu đề / English title"
        if heading:
            hashes, text = heading.group(1), heading.group(2)
            if " / " in text:
                parts = text.split(" / ")
                if len(parts) == 2 and _VIETNAMESE_CHAR_RE.search(parts[0]) and not _VIETNAMESE_CHAR_RE.search(parts[1]):
                    line = f"{hashes} {parts[0].strip()}"
                    removed += 1
                    out.append(line)
                    continue
            # Strip consecutive duplicate English headings (where previous line was Vietnamese heading of same level)
            if out:
                prev_match = _HEADING_RE.match(out[-1].strip())
                if (
                    prev_match
                    and len(prev_match.group(1)) == len(hashes)
                    and _VIETNAMESE_CHAR_RE.search(prev_match.group(2))
                    and not _VIETNAMESE_CHAR_RE.search(text)
                ):
                    words = _ENGLISH_WORD_RE.findall(text)
                    if len(words) >= 2 and any(w.casefold() in _ENGLISH_SENTENCE_WORDS for w in words):
                        removed += 1
                        continue

        out.append(line)
    return out, replace(stats, bilingual_duplicates_removed=stats.bilingual_duplicates_removed + removed)


def _strip_english_duplicate_lines(
    lines: list[str], stats: CleanStats
) -> tuple[list[str], CleanStats]:
    """Remove long English mirror paragraphs interleaved with Vietnamese OCR.

    Short proper names and table cells remain available for entity resolution;
    only non-table, English-dominant sentences are treated as duplicated text.
    """
    out: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("|", "#", ">", "-", "*")):
            out.append(line)
            continue
        words = _ENGLISH_WORD_RE.findall(stripped)
        if (
            len(words) >= 8
            and not _VIETNAMESE_CHAR_RE.search(stripped)
            and sum(word.casefold() in _ENGLISH_SENTENCE_WORDS for word in words) >= 2
        ):
            removed += 1
            continue
        out.append(line)
    return out, replace(stats, english_lines_removed=stats.english_lines_removed + removed)


def _strip_inline_english_mirrors(
    lines: list[str], stats: CleanStats
) -> tuple[list[str], CleanStats]:
    """Remove long English clauses embedded after Vietnamese text.

    Tables and short identifiers are intentionally untouched because their
    English tokens can be legal names, counterparty names, or column labels.
    """
    out: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("|", "#", ">", "-", "*")):
            out.append(line)
            continue
        parts = re.split(r"(?<=[.!?;])\s+(?=[A-Z][a-z])|\s+/\s+", stripped)
        if len(parts) == 1:
            out.append(line)
            continue
        kept: list[str] = []
        has_removed = False
        for part in parts:
            words = _ENGLISH_WORD_RE.findall(part)
            is_english_mirror = (
                len(words) >= 8
                and not _VIETNAMESE_CHAR_RE.search(part)
                and sum(word.casefold() in _ENGLISH_SENTENCE_WORDS for word in words) >= 2
            )
            if is_english_mirror:
                has_removed = True
                removed += 1
            else:
                kept.append(part)
        if has_removed and kept:
            out.append(" ".join(kept).strip())
        else:
            out.append(line)
    return out, replace(stats, english_inline_fragments_removed=stats.english_inline_fragments_removed + removed)


def _strip_images(lines: list[str], stats: CleanStats) -> tuple[list[str], CleanStats]:
    out: list[str] = []
    removed = 0
    for line in lines:
        if _IMAGE_RE.search(line):
            cleaned_line = _IMAGE_RE.sub("", line)
            cleaned_line = re.sub(r"[ \t]{2,}", " ", cleaned_line).strip()
            removed += 1
            if cleaned_line:
                out.append(cleaned_line)
            continue
        out.append(line)
    return out, replace(stats, images_removed=stats.images_removed + removed)


def _strip_audit_stamps_and_form_codes(lines: list[str], stats: CleanStats) -> tuple[list[str], CleanStats]:
    out: list[str] = []
    removed = 0
    for line in lines:
        stripped = line.strip()
        if not stripped:
            out.append(line)
            continue

        # Không lọc dòng Markdown heading hoặc bảng
        if stripped.startswith(("#", "|", "-", "*", ">")):
            # Chỉ loại bỏ garbled heading
            if _GARBLED_HEADING_RE.match(stripped):
                removed += 1
                continue
            out.append(line)
            continue

        # Form codes (B09-DN/HN, v.v.)
        if _FORM_CODE_RE.match(stripped):
            removed += 1
            continue

        # Standalone Page numbers
        if _PAGE_NUM_RE.match(stripped):
            removed += 1
            continue

        # Stamp symbols (like 'Z.H.H. ★', '46', v.v.)
        if _STAMP_SYMBOLS_RE.match(stripped):
            removed += 1
            continue

        # Stamp text fragments (like '302-C. TY H YOUN...', 'C.T.N.H.H', v.v.)
        if _STAMP_NOISE_RE.match(stripped):
            removed += 1
            continue

        # Isolated 1-3 letter uppercase fragments that are noise lines (e.g. 'TY', 'H', '03')
        if re.match(r"^[A-Z0-9.\s]{1,3}$", stripped):
            removed += 1
            continue

        out.append(line)
    return out, replace(stats, stamps_removed=stats.stamps_removed + removed)


def _strip_html_comments(lines: list[str], stats: CleanStats) -> list[str]:
    out = [line for line in lines if not _HTML_COMMENT_RE.match(line)]
    return out, replace(stats, html_comments=stats.html_comments + (len(lines) - len(out)))


def _strip_repeated_headings(lines: list[str], stats: CleanStats) -> list[str]:
    groups: dict[str, list[tuple[int, int]]] = {}
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        if not match or _NUMBERED_HEADING_RE.match(line):
            continue
        text = _normalize_heading(match.group(2))
        if not text:
            continue
        groups.setdefault(text, []).append((idx, len(match.group(1))))

    drop: set[int] = set()
    removed = 0
    for occurrences in groups.values():
        if len(occurrences) < 2:
            continue
        keep = min(occurrences, key=lambda item: (item[1], item[0]))
        for idx, _level in occurrences:
            if idx != keep[0]:
                drop.add(idx)
                removed += 1
    stats = replace(stats, repeated_headings=stats.repeated_headings + removed)
    return [line for idx, line in enumerate(lines) if idx not in drop], stats


def _strip_toc(lines: list[str], stats: CleanStats) -> list[str]:
    start = next((idx for idx, line in enumerate(lines) if _MUC_LUC_RE.match(line)), None)
    if start is None:
        return lines, stats
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if _HEADING_RE.match(lines[idx]) or _HTML_COMMENT_RE.match(lines[idx]):
            end = idx
            break
    return lines[:start] + lines[end:], replace(stats, toc_blocks=stats.toc_blocks + 1)


def _strip_statement_leaks(lines: list[str], stats: CleanStats) -> list[str]:
    # BCTC exports commonly wrap the first three statements below a generic
    # heading such as "Báo cáo tài chính riêng ...".  Removing only headings
    # named CDKT/KQKD/LCTT misses the first table in that wrapper.  For the
    # financial document roles, the notes heading is the hard boundary: when a
    # report wrapper/statement exists before it, drop the complete prefix so
    # CDKT, KQKD and LCTT cannot leak into the analytical input.
    notes_idx = None
    prefix_has_statement = False
    for idx, line in enumerate(lines):
        match = _HEADING_RE.match(line)
        normalized_line = _normalize_heading(line.lstrip("# "))
        if not match:
            # Annual reports often start with a plain-text wrapper title
            # instead of a Markdown heading.
            if idx < 40 and any(
                normalized_line.startswith(pattern)
                for pattern in _FINANCIAL_REPORT_WRAPPER_PATTERNS
            ):
                prefix_has_statement = True
            continue
        heading = _normalize_heading(match.group(2))
        if any(heading.startswith(pattern) for pattern in _NOTES_BOUNDARY_PATTERNS):
            notes_idx = idx
            break
        if _is_statement_heading(match.group(2)) or any(
            heading.startswith(pattern) for pattern in _FINANCIAL_REPORT_WRAPPER_PATTERNS
        ):
            prefix_has_statement = True
    if notes_idx is not None and prefix_has_statement:
        removed_sections = sum(
            1
            for line in lines[:notes_idx]
            if (match := _HEADING_RE.match(line))
            and (
                _is_statement_heading(match.group(2))
                or any(
                    _normalize_heading(match.group(2)).startswith(pattern)
                    for pattern in _FINANCIAL_REPORT_WRAPPER_PATTERNS
                )
            )
        )
        return lines[notes_idx:], replace(
            stats,
            statement_sections=stats.statement_sections + max(removed_sections, 3),
        )

    out: list[str] = []
    in_statement = False
    statement_level = 0
    sections = 0
    for line in lines:
        match = _HEADING_RE.match(line)
        if not in_statement:
            if (
                match
                and not _NUMBERED_HEADING_RE.match(line)
                and _is_statement_heading(match.group(2))
            ):
                in_statement = True
                statement_level = len(match.group(1))
                sections += 1
                continue
            out.append(line)
            continue
        if match and len(match.group(1)) <= statement_level:
            in_statement = False
            out.append(line)
            continue
    return out, replace(stats, statement_sections=stats.statement_sections + sections)


def _strip_accounting_policy_boilerplate(lines: list[str], stats: CleanStats) -> list[str]:
    out: list[str] = []
    in_policy = False
    policy_block: list[str] = []
    sections = 0

    for line in lines:
        match = _HEADING_RE.match(line)
        if not in_policy:
            if match and _is_accounting_policy_heading(match.group(2)):
                in_policy = True
                policy_block = [line]
                sections += 1
                continue
            out.append(line)
            continue

        # Checking next heading while in policy section
        if match and (_NUMBERED_HEADING_RE.match(line) or "thuyết minh" in match.group(2).casefold()):
            # If policy block contains explicit policy change keywords, preserve it
            block_text = "\n".join(policy_block).casefold()
            if any(kw in block_text for kw in _POLICY_CHANGE_KEYWORDS):
                out.extend(policy_block)
            
            in_policy = False
            policy_block = []
            out.append(line)
            continue

        policy_block.append(line)

    if in_policy and policy_block:
        block_text = "\n".join(policy_block).casefold()
        if any(kw in block_text for kw in _POLICY_CHANGE_KEYWORDS):
            out.extend(policy_block)

    return out, replace(stats, accounting_policy_sections=stats.accounting_policy_sections + sections)


def _strip_trailing_signature(lines: list[str], stats: CleanStats) -> list[str]:
    idx = len(lines)
    while idx > 0 and not lines[idx - 1].strip():
        idx -= 1
    removed = 0
    while idx > 0:
        line = lines[idx - 1]
        if not line.strip():
            idx -= 1
            continue
        if _is_signature_line(line):
            idx -= 1
            removed += 1
            continue
        break
    return lines[:idx], replace(stats, signature_lines=stats.signature_lines + removed)


def _strip_latex_junk(lines: list[str], stats: CleanStats) -> list[str]:
    out = [line for line in lines if not _LATEX_JUNK_RE.search(line)]
    return out, replace(stats, latex_junk=stats.latex_junk + (len(lines) - len(out)))


def _normalize_blank_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    prev_blank = False
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if prev_blank:
                continue
            prev_blank = True
            out.append("")
            continue
        prev_blank = False
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    return out


def _normalize_heading(text: str) -> str:
    return re.sub(r"\s+", " ", text).casefold().strip()


def _is_statement_heading(text: str) -> bool:
    normalized = _normalize_heading(text)
    return any(normalized.startswith(pattern) for pattern in _STATEMENT_PATTERNS)


def _is_accounting_policy_heading(text: str) -> bool:
    normalized = _normalize_heading(text)
    return any(pattern in normalized for pattern in _ACCOUNTING_POLICY_PATTERNS)


def _is_signature_line(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if _SIGNATURE_RE.match(stripped):
        return True
    if _DATE_RE.match(stripped):
        return True
    if _CITY_RE.match(stripped):
        return True
    if len(stripped) >= 4 and _ALL_CAPS_FRAGMENT_RE.match(stripped):
        return True
    return False
