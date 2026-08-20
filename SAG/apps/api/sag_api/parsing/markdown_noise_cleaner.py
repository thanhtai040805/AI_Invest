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


def clean_markdown(markdown: str) -> tuple[str, CleanStats]:
    """Làm sạch Markdown OCR BCTC, trả về (nội dung sạch, thống kê)."""
    lines = markdown.splitlines()
    stats = CleanStats(lines_in=len(lines))
    lines, stats = _strip_html_comments(lines, stats)
    lines, stats = _strip_repeated_headings(lines, stats)
    lines, stats = _strip_toc(lines, stats)
    lines, stats = _strip_statement_leaks(lines, stats)
    lines, stats = _strip_accounting_policy_boilerplate(lines, stats)
    lines, stats = _strip_trailing_signature(lines, stats)
    lines, stats = _strip_latex_junk(lines, stats)
    lines = _normalize_blank_lines(lines)
    return "\n".join(lines).strip() + "\n", replace(stats, lines_out=len(lines))


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