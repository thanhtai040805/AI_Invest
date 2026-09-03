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
_LATEX_JUNK_RE = re.compile(r"\\(frac|delta|partial|sum|theta|alpha|beta|omega)")

_STATEMENT_PATTERNS = (
    "bảng cân đối kế toán",
    "báo cáo kết quả hoạt động kinh doanh",
    "báo cáo kết quả hoạt động",
    "báo cáo lưu chuyển tiền tệ",
    "báo cáo tình hình tài chính",
    "báo cáo thay đổi vốn chủ sở hữu",
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


from html.parser import HTMLParser

_HTML_TABLE_BLOCK_RE = re.compile(r"<table\b[^>]*>.*?</table>", re.IGNORECASE | re.DOTALL)


class HTMLTableToMarkdownParser(HTMLParser):
    """Parser bóc tách bảng HTML sang ma trận các ô văn bản chuẩn."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.current_row: list[str] | None = None
        self.current_cell: list[str] | None = None
        self.in_cell = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        if t == "tr":
            self.current_row = []
        elif t in ("th", "td"):
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
                self.current_row.append(cell_text)
            self.current_cell = None
            self.in_cell = False
        elif t == "tr":
            if self.current_row is not None and any(c.strip() for c in self.current_row):
                self.rows.append(self.current_row)
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


def clean_markdown(markdown: str) -> tuple[str, CleanStats]:
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
    lines, stats = _strip_audit_stamps_and_form_codes(lines, stats)
    lines, stats = _strip_html_comments(lines, stats)
    lines, stats = _strip_repeated_headings(lines, stats)
    lines, stats = _strip_toc(lines, stats)
    lines, stats = _strip_statement_leaks(lines, stats)
    lines, stats = _strip_accounting_policy_boilerplate(lines, stats)
    lines, stats = _strip_trailing_signature(lines, stats)
    lines, stats = _strip_latex_junk(lines, stats)
    lines = _normalize_blank_lines(lines)
    return "\n".join(lines).strip() + "\n", replace(stats, lines_out=len(lines))


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
    out: list[str] = []
    in_statement = False
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
                sections += 1
                continue
            out.append(line)
            continue
        if match and (
            _NUMBERED_HEADING_RE.match(line) or "thuyết minh" in match.group(2).casefold()
        ):
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