"""HOSE ResearchInputBuilder: 11+2 Logical Sub-Documents (S01..S13) & 3-Layer Localization.

Productionizes Stage 1 & Master Blueprint §2.2 / §4.1 with 3-Layer Document Localization
(eliminating brittle page-by-page regex failures on messy HOSE filings):
- Layer 1 — Document-Level Section Map (priority order):
  1A. Native PDF Outline / Bookmarks (`doc.get_toc()` — 0đ).
  1B. Optional cheap LLM TOC Router (`TocRouterProtocol` / `DeepInfraTocRouter` using
      `deepseek-ai/DeepSeek-V4-Flash-0731` on ONLY the 2–5 TOC pages or a compact font-size
      heading outline — ~800 tokens ≈ 0.0001 USD/filing; understands creative Vietnamese
      annual-report headings that regex cannot cover).
  1C. Deterministic TOC Span Parser (`parse_toc_section_spans`) with full `[start..end]` ranges.
- Layer 2 — Printed-to-PDF Page Offset & A3 Spread Calibration (`calibrate_printed_to_pdf_mapping`):
  Samples footer/header page numbers across interior pages and automatically fits either
  `SINGLE_PAGE` (`pdf = printed + offset`) or `SPREAD_A3` (`1 PDF page = 2 printed pages`,
  e.g. CTG/PVT), mapping TOC printed ranges to exact 1-based PDF page numbers.
- Layer 3 — Stateful Section Span Assignment + Heading-Aware Boilerplate Filter + Safety Net:
  - Pages inside a mapped `[pdf_start..pdf_end]` span inherit the section's `sub_doc_slot`,
    so headingless continuation pages (e.g. pages 2–3 of a multi-page financial note table)
    never fall back to `S01`.
  - Heading-aware boilerplate filter checks ONLY the top heading zone and shields pages with
    financial statements / numeric tables (`>= 6` formatted accounting figures).
  - Sticky heading fallback + structural safety net detects missing core slots (`S05..S10`).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from statistics import median
from typing import Any, Literal, Protocol

import fitz
import httpx

from sag_api.core.config import settings
from sag_api.core.logging import get_logger
from sag_api.research.input_inspector import (
    BranchCode,
    InspectionResult,
    inspect_pdf_bytes,
    normalize_ctg_font_quirks,
)
from sag_api.research.input_selector import (
    MissingPublishedDateError,
    normalize_doc_type,
    parse_fiscal_quarter_from_meta,
    parse_fiscal_year_from_meta,
)

log = get_logger("research.input_builder")

SubDocSlot = Literal[
    "S01_MDNA_SEGMENT",
    "S02_OPERATING_SCALE",
    "S03_PLAN_CAPEX",
    "S04_RISK_SWOT",
    "S05_AUDIT_OPINION",
    "S06_SEGMENT_NOTE",
    "S07_REV_COGS_DETAIL",
    "S08_FA_DEPR_CIP",
    "S09_FIN_INVEST_PROV",
    "S10_DEBT_LEASE_FX",
    "S11_CAPITAL_POST_EVT",
    "S12_AGM_TARGETS",
    "S13_QUARTER_NOTES",
]

SLOT_HALF_LIFE_DAYS: dict[str, int] = {
    "S01_MDNA_SEGMENT": 365,
    "S02_OPERATING_SCALE": 365,
    "S03_PLAN_CAPEX": 365,
    "S04_RISK_SWOT": 365,
    "S05_AUDIT_OPINION": 365,
    "S06_SEGMENT_NOTE": 365,
    "S07_REV_COGS_DETAIL": 365,
    "S08_FA_DEPR_CIP": 365,
    "S09_FIN_INVEST_PROV": 365,
    "S10_DEBT_LEASE_FX": 365,
    "S11_CAPITAL_POST_EVT": 365,
    "S12_AGM_TARGETS": 180,
    "S13_QUARTER_NOTES": 90,
}

ALL_SUB_DOC_SLOTS: tuple[str, ...] = tuple(SLOT_HALF_LIFE_DAYS.keys())
VALID_SLOT_SET: frozenset[str] = frozenset(ALL_SUB_DOC_SLOTS)
CORE_AUDIT_NOTE_SLOTS: tuple[str, ...] = (
    "S05_AUDIT_OPINION",
    "S06_SEGMENT_NOTE",
    "S07_REV_COGS_DETAIL",
    "S08_FA_DEPR_CIP",
    "S09_FIN_INVEST_PROV",
    "S10_DEBT_LEASE_FX",
)


class InputBuildError(RuntimeError):
    """Fail-closed error raised when PDF building, OCR, or PIT validation fails."""


class OcrAdapterProtocol(Protocol):
    """Abstract targeted OCR adapter (e.g. MinerU with language='vi')."""

    async def ocr_pdf_pages(
        self,
        pdf_bytes: bytes,
        page_numbers: list[int],
        *,
        language: str = "vi",
    ) -> dict[int, str]: ...


@dataclass(slots=True)
class TocSectionSpan:
    """A section range mapped to one of `S01..S13` (or `EXCLUDE` for boilerplate)."""

    slot: str
    title: str
    printed_start: int
    printed_end: int
    pdf_start: int | None = None
    pdf_end: int | None = None
    confidence: float = 0.85
    source: str = "DETERMINISTIC_TOC"


@dataclass(slots=True)
class PageOffsetCalibration:
    """Mapping model converting printed report page numbers into 1-based PDF page indices."""

    mode: Literal["SINGLE_PAGE", "SPREAD_A3", "IDENTITY"] = "IDENTITY"
    offset: float = 0.0
    sample_pairs: int = 0
    confidence: float = 0.0

    def printed_to_pdf(self, printed_page: int, *, max_pages: int) -> int:
        if self.mode == "SPREAD_A3":
            mapped = round((float(printed_page) + self.offset) / 2.0)
        elif self.mode == "SINGLE_PAGE":
            mapped = round(float(printed_page) + self.offset)
        else:
            mapped = int(printed_page)
        return max(1, min(max_pages, int(mapped)))


class TocRouterProtocol(Protocol):
    """Optional semantic TOC router (e.g. 1 cheap DeepSeek-V4-Flash call on 2–5 TOC pages)."""

    async def route_toc(
        self,
        *,
        ticker: str,
        doc_type: str,
        toc_or_outline_text: str,
        total_pdf_pages: int,
    ) -> list[TocSectionSpan]: ...


@dataclass(slots=True)
class SubDocumentBundle:
    project_id: str
    sub_doc_slot: str
    half_life_days: int
    fiscal_year: int
    fiscal_quarter: int
    doc_type: str
    published_date: date
    valid_from: datetime
    routed_branch: BranchCode
    page_numbers: list[int]
    markdown_content: str
    content_hash: str
    ocr_pages_used: int = 0
    source_url: str = ""
    title: str = ""


@dataclass(slots=True)
class ResearchInputBuildResult:
    ticker: str
    doc_type: str
    routed_branch: BranchCode
    inspection: InspectionResult
    sub_documents: list[SubDocumentBundle] = field(default_factory=list)
    excluded_pages: dict[int, str] = field(default_factory=dict)
    pr_narrative_markdown: str = ""
    follow_up_urls: list[str] = field(default_factory=list)
    ocr_pages_used: int = 0
    localization_method: str = "STATEFUL_HEADING_FALLBACK"
    page_calibration: PageOffsetCalibration = field(default_factory=PageOffsetCalibration)
    section_spans: list[TocSectionSpan] = field(default_factory=list)
    missing_core_slots: list[str] = field(default_factory=list)


_BOILERPLATE_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "PR_LETTER",
        re.compile(
            r"(?:thư\s+(?:chủ\s+tịch|tổng\s+giám\s+đốc|người\s+đứng\s+đầu|gửi\s+cổ\s+đông)|"
            r"message\s+from\s+(?:the\s+)?(?:chairman|ceo)|thông\s+điệp\s+(?:chủ\s+tịch|ban\s+lãnh\s+đạo))",
            re.IGNORECASE,
        ),
    ),
    (
        "ESG_CHARITY",
        re.compile(
            r"(?:báo\s+cáo\s+phát\s+triển\s+bền\s+vững|trách\s+nhiệm\s+xã\s+hội|"
            r"hoạt\s+động\s+(?:từ\s+thiện|cộng\s+đồng|an\s+sinh)|phát\s+thải\s+khí\s+nhà\s+kính|\besg\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "AGM_PROCEDURAL",
        re.compile(
            r"(?:biên\s+bản\s+kiểm\s+phiếu|thể\s+lệ\s+biểu\s+quyết|danh\s+sách\s+ban\s+kiểm\s+phiếu|"
            r"chương\s+trình\s+đại\s+hội|giấy\s+ủy\s+quyền\s+tham\s+dự)",
            re.IGNORECASE,
        ),
    ),
    (
        "TT200_POLICY_BOILERPLATE",
        re.compile(
            r"(?:tóm\s+tắt\s+các\s+chính\s+sách\s+kế\s+toán\s+chủ\s+yếu|"
            r"chuẩn\s+mực\s+kế\s+toán\s+việt\s+nam|cơ\s+sở\s+lập\s+báo\s+cáo\s+tài\s+chính)",
            re.IGNORECASE,
        ),
    ),
)

_FINANCIAL_SHIELD_RE = re.compile(
    r"(?:bảng\s+cân\s+đối\s+kế\s+toán|báo\s+cáo\s+kết\s+quả\s+hoạt\s+động|"
    r"báo\s+cáo\s+lưu\s+chuyển\s+tiền\s+tệ|b0[1-9][a-z]?-dn|b0[1-5]/tctd|"
    r"thuyết\s+minh\s+(?:số\s*)?\d+|báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập)",
    re.IGNORECASE,
)
_ACCOUNTING_NUMBER_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3}){2,})(?!\d)")

_SLOT_MATCHERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "S05_AUDIT_OPINION",
        re.compile(
            r"(?:báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập|ý\s+kiến\s+của\s+kiểm\s+toán\s+viên|"
            r"vấn\s+đề\s+cần\s+nhấn\s+mạnh|independent\s+auditor)",
            re.IGNORECASE,
        ),
    ),
    (
        "S06_SEGMENT_NOTE",
        re.compile(
            r"(?:báo\s+cáo\s+bộ\s+phận|thông\s+tin\s+(?:theo\s+)?bộ\s+phận|segment\s+report|"
            r"chất\s+lượng\s+nợ\s+cho\s+vay|tiền\s+gửi\s+của\s+khách\s+hàng|b0[35]/tctd|thông\s+tư\s+334)",
            re.IGNORECASE,
        ),
    ),
    (
        "S07_REV_COGS_DETAIL",
        re.compile(
            r"(?:chi\s+tiết\s+doanh\s+thu|doanh\s+thu\s+thuần\s+về\s+bán\s+hàng|"
            r"giá\s+vốn\s+hàng\s+bán|báo\s+cáo\s+kết\s+quả\s+hoạt\s+động)",
            re.IGNORECASE,
        ),
    ),
    (
        "S08_FA_DEPR_CIP",
        re.compile(
            r"(?:tài\s+sản\s+cố\s+định|xây\s+dựng\s+cơ\s+bản\s+dở\s+dang|khấu\s+hao|"
            r"tài\s+sản\s+thế\s+chấp|bảng\s+cân\s+đối\s+kế\s+toán)",
            re.IGNORECASE,
        ),
    ),
    (
        "S09_FIN_INVEST_PROV",
        re.compile(
            r"(?:đầu\s+tư\s+tài\s+chính|công\s+ty\s+con|công\s+ty\s+liên\s+kết|"
            r"dự\s+phòng\s+phải\s+thu|fvtpl|afs|htm\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "S10_DEBT_LEASE_FX",
        re.compile(
            r"(?:vay\s+và\s+nợ\s+thuê\s+tài\s+chính|thuê\s+hoạt\s+động|độ\s+nhạy\s+(?:tỷ\s+giá|lãi\s+suất)|"
            r"rủi\s+ro\s+tỷ\s+giá|an\s+toàn\s+vốn|\bcar\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "S11_CAPITAL_POST_EVT",
        re.compile(
            r"(?:vốn\s+chủ\s+sở\s+hữu|tăng\s+vốn|cổ\s+tức|\besop\b|"
            r"sự\s+kiện\s+phát\s+sinh\s+sau\s+ngày|thay\s+đổi\s+chính\s+sách\s+kế\s+toán)",
            re.IGNORECASE,
        ),
    ),
    (
        "S03_PLAN_CAPEX",
        re.compile(
            r"(?:kế\s+hoạch\s+(?:kinh\s+doanh|năm\s+20\d{2}|đầu\s+tư)|ngân\s+sách\s+capex|"
            r"thực\s+hiện\s+(?:so\s+với\s+)?kế\s+hoạch|mục\s+tiêu\s+doanh\s+thu)",
            re.IGNORECASE,
        ),
    ),
    (
        "S04_RISK_SWOT",
        re.compile(
            r"(?:quản\s+trị\s+rủi\s+ro|rủi\s+ro\s+kinh\s+doanh|\bswot\b|điểm\s+mạnh|điểm\s+yếu|cơ\s+hội|thách\s+thức)",
            re.IGNORECASE,
        ),
    ),
    (
        "S02_OPERATING_SCALE",
        re.compile(
            r"(?:công\s+suất|đội\s+tàu|cửa\s+hàng|quỹ\s+đất|nhà\s+máy|quy\s+mô\s+vận\s+hành|thị\s+phần|sản\s+lượng)",
            re.IGNORECASE,
        ),
    ),
    (
        "S01_MDNA_SEGMENT",
        re.compile(
            r"(?:kết\s+quả\s+(?:hoạt\s+động\s+)?kinh\s+doanh|tình\s+hình\s+hoạt\s+động|"
            r"phân\s+tích\s+(?:theo\s+)?mảng|thị\s+trường\s+xuất\s+khẩu|đánh\s+giá\s+kqkd)",
            re.IGNORECASE,
        ),
    ),
)

_TOC_PAGE_CUE_RE = re.compile(r"(?:mục\s+lục|table\s+of\s+contents|\bcontents\b)", re.IGNORECASE)
_TOC_LINE_RE = re.compile(r"^(?P<title>.{4,140}?)(?:\.{2,}|\s{2,}|\s+)(?P<page>\d{1,3})\s*$")
_AUDIT_FIRM_SIG_RE = re.compile(
    r"(?:deloitte|pwc|pricewaterhousecoopers|ernst\s*&\s*young|\bey\b|kpmg|aasc|a&c|moore|vacpa)",
    re.IGNORECASE,
)


def _extract_heading_zone(text: str, *, max_lines: int = 8, max_chars: int = 480) -> str:
    """Return top heading zone of a page so body mentions do not hijack slot/boilerplate classification."""
    non_empty = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not non_empty:
        return ""
    return "\n".join(non_empty[:max_lines])[:max_chars]


def _page_has_financial_table_shield(text: str) -> bool:
    """True when page carries audited statement headers or dense accounting figures."""
    if _FINANCIAL_SHIELD_RE.search(text or ""):
        return True
    return len(_ACCOUNTING_NUMBER_RE.findall(text or "")) >= 6


def classify_page_boilerplate(text: str, *, doc_type: str) -> str | None:
    """Heading-aware boilerplate exclusion with financial-table shield."""
    raw = (text or "").strip()
    if len(raw) < 30:
        return "EMPTY_OR_COVER"

    low_full = raw.lower()
    title_zone = _extract_heading_zone(raw, max_lines=2, max_chars=220).lower()
    first_line = title_zone.splitlines()[0] if title_zone else ""
    has_policy_change = bool(
        re.search(r"thay\s+đổi\s+chính\s+sách\s+kế\s+toán", low_full, re.IGNORECASE)
    )
    has_fin_shield = _page_has_financial_table_shield(raw)
    has_core_title = _match_slot_in_text(first_line) is not None

    for reason, pat in _BOILERPLATE_RULES:
        if reason == "TT200_POLICY_BOILERPLATE" and has_policy_change:
            continue
        if reason == "AGM_PROCEDURAL" and doc_type != "NQ_DHDCD":
            continue
        if reason in ("PR_LETTER", "ESG_CHARITY") and (has_fin_shield or has_core_title):
            continue
        # Check title zone (top 2 lines) so casual body mentions ("ESG" inside an MD&A page) never drop the page.
        if pat.search(title_zone):
            return reason
    return None


def _match_slot_in_text(text: str) -> str | None:
    """Return highest-priority matching slot on the given text, or None if no explicit signal."""
    for slot, pat in _SLOT_MATCHERS:
        if pat.search(text or ""):
            return slot
    return None


def classify_text_to_slot(
    text: str,
    *,
    doc_type: str,
    previous_slot: str | None = None,
) -> str | None:
    """Route a page's text to one of S01..S13, prioritizing page title and sticky continuation."""
    canonical = normalize_doc_type(doc_type)
    if canonical == "NQ_DHDCD":
        return "S12_AGM_TARGETS"
    if canonical in ("LATEST_QUARTER", "GIAI_TRINH"):
        return "S13_QUARTER_NOTES"

    # 1. Explicit page title (top 2 lines) wins immediately over any body mention.
    title_zone = _extract_heading_zone(text or "", max_lines=2, max_chars=220)
    title_slot = _match_slot_in_text(title_zone)
    if title_slot is not None:
        return title_slot

    # 2. Secondary heading zone (top 6 lines) when no sticky slot is active.
    if previous_slot is None:
        heading_zone = _extract_heading_zone(text or "", max_lines=6, max_chars=420)
        heading_slot = _match_slot_in_text(heading_zone)
        if heading_slot is not None:
            return heading_slot
    else:
        # Preserve sticky context on headingless continuation pages (e.g. pages 2–3 of a table).
        return previous_slot

    # 3. Full-page match when no sticky context exists yet.
    body_slot = _match_slot_in_text(text or "")
    if body_slot is not None:
        return body_slot

    if canonical == "BCTN":
        return "S01_MDNA_SEGMENT"
    if canonical == "BCTC":
        return "S07_REV_COGS_DETAIL"
    if canonical == "CAO_BACH":
        return "S11_CAPITAL_POST_EVT"
    return "S01_MDNA_SEGMENT"


# ── Layer 2: Printed-to-PDF Page Offset & Spread A3 Calibration ──────────────


def _extract_edge_page_numbers(page: fitz.Page) -> list[tuple[float, int]]:
    """Extract candidate printed page numbers located in top/bottom 14% margins with their x-center."""
    rect = page.rect
    w, h = float(rect.width), float(rect.height)
    if w <= 0 or h <= 0:
        return []
    words = page.get_text("words")
    out: list[tuple[float, int]] = []
    for wd in words:
        x0, y0, x1, y1, token = float(wd[0]), float(wd[1]), float(wd[2]), float(wd[3]), str(wd[4]).strip()
        if not (y1 <= h * 0.14 or y0 >= h * 0.86):
            continue
        cleaned = token.strip("|-–—[]()")
        if re.fullmatch(r"\d{1,3}", cleaned):
            val = int(cleaned)
            if 1 <= val <= 999:
                out.append(((x0 + x1) / (2.0 * w), val))
    return out


def calibrate_printed_to_pdf_mapping(doc: fitz.Document) -> PageOffsetCalibration:
    """Calibrate printed-page -> 1-based PDF-page mapping, auto-detecting A3 2-page spreads."""
    n_pages = len(doc)
    if n_pages < 3:
        return PageOffsetCalibration(mode="IDENTITY", offset=0.0, sample_pairs=0, confidence=0.0)

    sample_count = min(18, max(6, n_pages - 2))
    step = max(1, (n_pages - 2) // sample_count)
    sample_indices = list(range(2, n_pages, step))[:sample_count]

    spread_offsets: list[float] = []
    single_offsets: list[float] = []

    for pdf_no in sample_indices:
        page = doc[pdf_no - 1]
        rect = page.rect
        is_wide = float(rect.width) > float(rect.height) * 1.25
        cands = _extract_edge_page_numbers(page)
        if not cands:
            continue

        if is_wide:
            left_nums = [v for x_rel, v in cands if x_rel < 0.45]
            right_nums = [v for x_rel, v in cands if x_rel > 0.55]
            matched_spread = False
            for lv in left_nums:
                for rv in right_nums:
                    if rv == lv + 1 and abs(2 * pdf_no - rv) <= max(20, n_pages):
                        # Since pdf_no ≈ (printed + offset) / 2 -> offset ≈ 2*pdf_no - printed_right
                        spread_offsets.append(float(2 * pdf_no - rv))
                        matched_spread = True
                        break
                if matched_spread:
                    break
            if matched_spread:
                continue

        # Single-page candidate: choose candidate closest to pdf_no within reasonable offset
        best = min(cands, key=lambda item: abs(item[1] - pdf_no))
        if abs(best[1] - pdf_no) <= max(30, n_pages // 2):
            single_offsets.append(float(pdf_no - best[1]))

    if len(spread_offsets) >= 3 and len(spread_offsets) >= len(single_offsets):
        med_off = float(median(spread_offsets))
        inliers = sum(1 for v in spread_offsets if abs(v - med_off) <= 1.5)
        conf = inliers / max(1, len(spread_offsets))
        if conf >= 0.60:
            return PageOffsetCalibration(
                mode="SPREAD_A3",
                offset=med_off,
                sample_pairs=len(spread_offsets),
                confidence=round(conf, 3),
            )

    if len(single_offsets) >= 3:
        med_off = float(median(single_offsets))
        inliers = sum(1 for v in single_offsets if abs(v - med_off) <= 1.5)
        conf = inliers / max(1, len(single_offsets))
        if conf >= 0.60:
            return PageOffsetCalibration(
                mode="SINGLE_PAGE",
                offset=med_off,
                sample_pairs=len(single_offsets),
                confidence=round(conf, 3),
            )

    return PageOffsetCalibration(mode="IDENTITY", offset=0.0, sample_pairs=0, confidence=0.0)


# ── Layer 1A/1B/1C: Bookmarks, Font-Size Outline, Cheap LLM TOC Router ──────


def extract_pdf_outline_spans(
    doc: fitz.Document,
    *,
    doc_type: str,
) -> list[TocSectionSpan]:
    """Layer 1A (0đ): Parse native PDF bookmarks (`doc.get_toc()`) into exact PDF page spans."""
    raw_toc = doc.get_toc(simple=True) or []
    n_pages = len(doc)
    if len(raw_toc) < 3 or n_pages == 0:
        return []

    entries: list[tuple[int, str, int]] = []
    for item in raw_toc:
        if len(item) < 3:
            continue
        lvl, title, pno = int(item[0]), str(item[1]).strip(), int(item[2])
        if title and 1 <= pno <= n_pages and lvl <= 3:
            entries.append((lvl, title, pno))
    if len(entries) < 3:
        return []

    spans: list[TocSectionSpan] = []
    for idx, (_lvl, title, start_p) in enumerate(entries):
        end_p = n_pages
        for nxt_idx in range(idx + 1, len(entries)):
            if entries[nxt_idx][2] > start_p:
                end_p = max(start_p, entries[nxt_idx][2] - 1)
                break
        bp_reason = classify_page_boilerplate(f"{title}\n" + "x" * 35, doc_type=doc_type)
        if bp_reason is not None:
            slot = "EXCLUDE"
        else:
            slot = _match_slot_in_text(title) or ""
        if not slot:
            continue
        spans.append(
            TocSectionSpan(
                slot=slot,
                title=title,
                printed_start=start_p,
                printed_end=end_p,
                pdf_start=start_p,
                pdf_end=end_p,
                confidence=0.92,
                source="PDF_BOOKMARKS",
            )
        )
    return spans


def extract_toc_or_heading_digest(
    doc: fitz.Document,
    page_texts: dict[int, str],
    *,
    max_toc_scan_pages: int = 12,
    max_chars: int = 4500,
) -> tuple[str, bool]:
    """Return `(digest_text, is_explicit_toc)` for Layer 1B/1C TOC routing.

    Prefers explicit TOC pages found in the first 12 pages; falls back to a compact
    font-size heading outline extracted via PyMuPDF `get_text("dict")`.
    """
    n_pages = len(doc)
    toc_pages_found: list[str] = []
    for pno in range(1, min(n_pages, max_toc_scan_pages) + 1):
        txt = page_texts.get(pno) or ""
        if not txt and pno <= n_pages:
            txt = (doc[pno - 1].get_text("text") or "").strip()
        if not txt:
            continue
        toc_line_hits = sum(1 for ln in txt.splitlines() if _TOC_LINE_RE.match(ln.strip()))
        if _TOC_PAGE_CUE_RE.search(txt) or toc_line_hits >= 5:
            toc_pages_found.append(f"[PDF_PAGE {pno}]\n{txt}")
            if len(toc_pages_found) >= 4:
                break

    if toc_pages_found:
        return "\n\n".join(toc_pages_found)[:max_chars], True

    # Fallback: build compact font-size heading outline across digital pages
    all_sizes: Counter[int] = Counter()
    page_headings: list[tuple[int, float, str]] = []
    for pno in range(1, n_pages + 1):
        page_dict = doc[pno - 1].get_text("dict")
        blocks = page_dict.get("blocks", []) if isinstance(page_dict, dict) else []
        for blk in blocks:
            if blk.get("type") != 0:
                continue
            for line in blk.get("lines", []):
                spans = line.get("spans", [])
                if not spans:
                    continue
                max_sz = max(float(sp.get("size", 0.0)) for sp in spans)
                line_txt = " ".join(str(sp.get("text", "")).strip() for sp in spans).strip()
                if len(line_txt) < 4:
                    continue
                all_sizes[round(max_sz)] += 1
                if len(line_txt) <= 120:
                    page_headings.append((pno, max_sz, line_txt))

    if not all_sizes:
        return "", False
    body_sz = all_sizes.most_common(1)[0][0]
    min_heading_sz = max(body_sz * 1.15, body_sz + 1.5)
    outline_lines = [
        f"P{pno}: {txt}"
        for pno, sz, txt in page_headings
        if sz >= min_heading_sz
    ]
    return "\n".join(outline_lines)[:max_chars], False


def parse_toc_section_spans(
    toc_text: str,
    *,
    max_pages: int,
    doc_type: str = "BCTN",
) -> list[TocSectionSpan]:
    """Layer 1C: Deterministic TOC parser producing ordered `[printed_start..printed_end]` spans."""
    entries: list[tuple[str, int]] = []
    for raw_line in (toc_text or "").splitlines():
        line = raw_line.strip()
        m = _TOC_LINE_RE.match(line)
        if not m:
            continue
        title = m.group("title").strip(" .-–—:\t")
        pno = int(m.group("page"))
        if len(title) >= 4 and 1 <= pno <= max(max_pages * 2, 400):
            entries.append((title, pno))

    if not entries:
        return []

    spans: list[TocSectionSpan] = []
    for idx, (title, start_p) in enumerate(entries):
        end_p = start_p + 2
        for nxt_idx in range(idx + 1, len(entries)):
            if entries[nxt_idx][1] > start_p:
                end_p = max(start_p, entries[nxt_idx][1] - 1)
                break
        bp_reason = classify_page_boilerplate(f"{title}\n" + "x" * 35, doc_type=doc_type)
        if bp_reason is not None:
            slot = "EXCLUDE"
        else:
            slot = _match_slot_in_text(title) or ""
        if not slot:
            continue
        spans.append(
            TocSectionSpan(
                slot=slot,
                title=title,
                printed_start=start_p,
                printed_end=end_p,
                confidence=0.75,
                source="DETERMINISTIC_TOC",
            )
        )
    return spans


def parse_toc_target_pages(
    toc_text: str,
    *,
    max_pages: int,
    calibration: PageOffsetCalibration | None = None,
) -> list[int]:
    """Parse target 1-based PDF page numbers from TOC text for 2-Pass TOC-First OCR."""
    spans = parse_toc_section_spans(toc_text, max_pages=max_pages)
    cal = calibration or PageOffsetCalibration()
    targets: set[int] = set()
    for sp in spans:
        if sp.slot == "EXCLUDE":
            continue
        p_start = cal.printed_to_pdf(sp.printed_start, max_pages=max_pages)
        p_end = cal.printed_to_pdf(sp.printed_end, max_pages=max_pages)
        if p_end < p_start:
            p_end = p_start
        # Bound each individual section slice so a malformed TOC end never grabs 200 pages
        p_end = min(max_pages, p_end, p_start + 12)
        targets.update(range(p_start, p_end + 1))

    if targets:
        return sorted(targets)

    # Fallback line-level scan if TOC had non-standard formatting
    relevant_line_re = re.compile(
        r"(?:kết\s+quả|kinh\s+doanh|kế\s+hoạch|rủi\s+ro|bộ\s+phận|doanh\s+thu|giá\s+vốn|"
        r"kiểm\s+toán|thuyết\s+minh|tài\s+sản|vay\s+nợ|công\s+ty\s+con|vốn\s+chủ|cổ\s+tức|"
        r"nghị\s+quyết|chỉ\s+tiêu|capex)",
        re.IGNORECASE,
    )
    for line in (toc_text or "").splitlines():
        if not relevant_line_re.search(line):
            continue
        m_page = re.search(r"(?:\.{2,}|\s+)(\d{1,3})\s*$", line.strip())
        if m_page:
            pno = cal.printed_to_pdf(int(m_page.group(1)), max_pages=max_pages)
            targets.update(range(pno, min(max_pages, pno + 2) + 1))
    return sorted(targets)


class DeepInfraTocRouter:
    """Layer 1B: 1-call cheap LLM TOC router using DeepInfra `deepseek-ai/DeepSeek-V4-Flash-0731`.

    Sends ONLY 2–5 TOC pages (or compact font-size heading outline, ~800 tokens ≈ 0.0001 USD)
    and returns structured `TocSectionSpan` ranges mapped to `S01..S13` or `EXCLUDE`.
    Fails softly (returns `[]`) on network/JSON errors so deterministic layers always recover.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 25.0,
    ) -> None:
        self._base_url = (base_url or settings.llm_base_url or "https://api.deepinfra.com/v1/openai").rstrip("/")
        self._api_key = api_key if api_key is not None else (settings.llm_api_key or "")
        self._model = model or settings.llm_model or "deepseek-ai/DeepSeek-V4-Flash-0731"
        self._timeout = timeout_seconds

    async def route_toc(
        self,
        *,
        ticker: str,
        doc_type: str,
        toc_or_outline_text: str,
        total_pdf_pages: int,
    ) -> list[TocSectionSpan]:
        if not self._api_key or not toc_or_outline_text.strip():
            return []

        system_prompt = (
            "Bạn là bộ định tuyến Mục lục hồ sơ doanh nghiệp HOSE (Việt Nam). "
            "Từ Mục lục hoặc dàn ý tiêu đề, hãy ánh xạ các mục sang đúng mã hộc chuẩn sau:\n"
            "- S01_MDNA_SEGMENT: Đánh giá KQKD theo mảng/sản phẩm/địa lý\n"
            "- S02_OPERATING_SCALE: Quy mô vận hành, tài sản lõi, công suất, đội tàu, cửa hàng, quỹ đất\n"
            "- S03_PLAN_CAPEX: Kế hoạch kinh doanh, ngân sách CAPEX, so sánh TH vs KH\n"
            "- S04_RISK_SWOT: Quản trị rủi ro, SWOT, thách thức cạnh tranh\n"
            "- S05_AUDIT_OPINION: Báo cáo kiểm toán độc lập\n"
            "- S06_SEGMENT_NOTE: Báo cáo bộ phận / chất lượng nợ & CASA (Bank) / TT334 (Chứng khoán)\n"
            "- S07_REV_COGS_DETAIL: Báo cáo KQKD & Thuyết minh Doanh thu / Giá vốn\n"
            "- S08_FA_DEPR_CIP: CĐKT, TSCĐ, Khấu hao, XDCB dở dang (CIP), Tài sản thế chấp\n"
            "- S09_FIN_INVEST_PROV: Đầu tư tài chính, Công ty con/liên kết, Dự phòng, FVTPL/AFS\n"
            "- S10_DEBT_LEASE_FX: Vay nợ, Thuê hoạt động ngoại bảng, Độ nhạy tỷ giá/lãi suất, CAR\n"
            "- S11_CAPITAL_POST_EVT: Vốn chủ sở hữu, Tăng vốn, Cổ tức, ESOP, Sự kiện sau khóa sổ\n"
            "- S12_AGM_TARGETS: Nghị quyết ĐHĐCĐ chốt chỉ tiêu\n"
            "- S13_QUARTER_NOTES: Thuyết minh BCTC quý\n"
            "- EXCLUDE: Thư Chủ tịch/TGĐ (PR), Báo cáo ESG/từ thiện, Lịch sử chung, "
            "Chính sách TT200 mẫu, Biên bản kiểm phiếu.\n"
            "Chỉ trả về JSON hợp lệ dạng: "
            '{"sections":[{"slot":"S01_MDNA_SEGMENT","title":"...","printed_start":12,"printed_end":24}]}'
        )
        user_prompt = (
            f"Mã cổ phiếu: {ticker} | Loại tài liệu: {doc_type} | Tổng trang PDF: {total_pdf_pages}\n\n"
            f"Mục lục / Dàn ý:\n{toc_or_outline_text}"
        )
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/chat/completions",
                    headers={
                        "Authorization": f"Bearer {self._api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "model": self._model,
                        "temperature": 0.0,
                        "max_tokens": 1800,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
            if resp.status_code != 200:
                log.warning(
                    "LLM TOC router HTTP %s for %s; falling back to deterministic TOC",
                    resp.status_code,
                    ticker,
                )
                return []
            content = resp.json()["choices"][0]["message"]["content"]
            return parse_llm_toc_json(content)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM TOC router failed for %s (%s); falling back", ticker, type(exc).__name__)
            return []


def parse_llm_toc_json(raw_json: str) -> list[TocSectionSpan]:
    """Parse and strictly validate JSON returned by an LLM TOC Router."""
    text = (raw_json or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
    try:
        payload: Any = json.loads(text)
    except ValueError:
        return []
    items = payload.get("sections", []) if isinstance(payload, dict) else (payload if isinstance(payload, list) else [])
    spans: list[TocSectionSpan] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        slot = str(item.get("slot") or "").strip().upper()
        if slot != "EXCLUDE" and slot not in VALID_SLOT_SET:
            continue
        try:
            p_start = int(item.get("printed_start"))
            p_end = int(item.get("printed_end", p_start))
        except (TypeError, ValueError):
            continue
        if p_start < 1 or p_end < p_start:
            continue
        spans.append(
            TocSectionSpan(
                slot=slot,
                title=str(item.get("title") or slot).strip()[:160],
                printed_start=p_start,
                printed_end=p_end,
                confidence=0.90,
                source="LLM_TOC_ROUTER",
            )
        )
    return spans


def _map_spans_to_pdf_pages(
    spans: list[TocSectionSpan],
    *,
    calibration: PageOffsetCalibration,
    max_pages: int,
    already_pdf_indexed: bool = False,
) -> dict[int, TocSectionSpan]:
    """Expand `TocSectionSpan` list into a 1-based `pdf_page -> TocSectionSpan` lookup table."""
    page_map: dict[int, TocSectionSpan] = {}
    for sp in spans:
        if already_pdf_indexed and sp.pdf_start is not None and sp.pdf_end is not None:
            p_start = max(1, min(max_pages, sp.pdf_start))
            p_end = max(p_start, min(max_pages, sp.pdf_end))
        else:
            p_start = calibration.printed_to_pdf(sp.printed_start, max_pages=max_pages)
            p_end = calibration.printed_to_pdf(sp.printed_end, max_pages=max_pages)
            if p_end < p_start:
                p_end = p_start
            sp.pdf_start = p_start
            sp.pdf_end = p_end
        for pno in range(p_start, p_end + 1):
            # Prefer specific non-EXCLUDE financial slots if two ranges overlap on a boundary page
            existing = page_map.get(pno)
            if existing is None or (existing.slot == "EXCLUDE" and sp.slot != "EXCLUDE"):
                page_map[pno] = sp
    return page_map


def _structural_safety_net_slot(text: str) -> str | None:
    """Detect missing core audit/financial slots via structural document signatures."""
    low = (text or "").lower()
    if _AUDIT_FIRM_SIG_RE.search(low) and ("ý kiến" in low or "kiểm toán" in low):
        return "S05_AUDIT_OPINION"
    if re.search(r"\bb01[a-z]?-dn|\bb02/tctd", low):
        return "S08_FA_DEPR_CIP"
    if re.search(r"\bb02[a-z]?-dn|\bb03/tctd", low):
        return "S07_REV_COGS_DETAIL"
    return None


def _extract_digital_page_text(page: fitz.Page, *, left_sidebar_x: float) -> str:
    """Extract clean reading-order text from a digital PDF page, handling sidebars and 2-page spreads."""
    rect = page.rect
    w, h = float(rect.width), float(rect.height)
    if w > h * 1.25 and left_sidebar_x == 0.0:
        words = page.get_text("words")
        mid = w / 2.0
        crossing = sum(1 for wd in words if wd[0] < mid - 4 and wd[2] > mid + 4)
        if crossing <= 2 and len(words) > 10:
            left_txt = page.get_text("text", clip=fitz.Rect(0.0, 0.0, mid, h)) or ""
            right_txt = page.get_text("text", clip=fitz.Rect(mid, 0.0, w, h)) or ""
            return normalize_ctg_font_quirks(f"{left_txt.strip()}\n\n{right_txt.strip()}".strip())
    if left_sidebar_x > 0.0:
        txt = page.get_text("text", clip=fitz.Rect(left_sidebar_x, 0.0, w, h)) or ""
        return normalize_ctg_font_quirks(txt.strip())
    return normalize_ctg_font_quirks((page.get_text("text") or "").strip())


def _slice_pdf_pages(pdf_bytes: bytes, page_numbers: list[int]) -> bytes:
    """Create an in-memory sub-PDF containing only 1-based `page_numbers`."""
    src = fitz.open(stream=pdf_bytes, filetype="pdf")
    dst = fitz.open()
    try:
        for pno in page_numbers:
            if 1 <= pno <= len(src):
                dst.insert_pdf(src, from_page=pno - 1, to_page=pno - 1)
        return dst.tobytes()
    finally:
        dst.close()
        src.close()


async def build_research_sub_documents(
    *,
    ticker: str,
    pdf_bytes: bytes,
    doc_type: str,
    published_date: date | None,
    title: str = "",
    source_url: str = "",
    fiscal_year: int | None = None,
    fiscal_quarter: int | None = None,
    ocr_adapter: OcrAdapterProtocol | None = None,
    toc_router: TocRouterProtocol | None = None,
    toc_scan_limit: int = 5,
    max_targeted_ocr_pages: int = 25,
) -> ResearchInputBuildResult:
    """Build S01..S13 Logical Sub-Documents using 3-Layer Localization & `<!-- PAGE N -->` anchors."""
    if published_date is None:
        raise MissingPublishedDateError(
            f"Missing published_date for {ticker} ({doc_type}); failing closed."
        )

    canonical_doc_type = normalize_doc_type(doc_type, title=title)
    resolved_fy = fiscal_year or parse_fiscal_year_from_meta(title, source_url) or published_date.year
    resolved_fq = (
        fiscal_quarter
        if fiscal_quarter is not None
        else parse_fiscal_quarter_from_meta(title, source_url)
    )
    valid_from_dt = datetime(
        published_date.year, published_date.month, published_date.day, tzinfo=UTC
    )

    inspection = inspect_pdf_bytes(pdf_bytes, source_url=source_url)
    if inspection.error:
        raise InputBuildError(f"PDF inspection failed for {ticker}: {inspection.error}")

    build_res = ResearchInputBuildResult(
        ticker=ticker.upper(),
        doc_type=canonical_doc_type,
        routed_branch=inspection.routed_branch,
        inspection=inspection,
        follow_up_urls=list(inspection.embedded_urls),
    )

    if inspection.doc_class == "NOTICE_ONLY":
        return build_res

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_texts: dict[int, str] = {}
    ocr_pages_used = 0
    calibration = PageOffsetCalibration()
    section_spans: list[TocSectionSpan] = []
    spans_already_pdf = False
    localization_method = "STATEFUL_HEADING_FALLBACK"

    try:
        n_pages = len(doc)
        triage_by_page = {pt.page_no: pt for pt in inspection.page_triage}
        calibration = calibrate_printed_to_pdf_mapping(doc)

        if inspection.routed_branch == "C":
            # 2-Pass TOC-First OCR for 100% scanned PDFs
            if ocr_adapter is None:
                raise InputBuildError(
                    f"Scan PDF ({ticker}, {n_pages}p) requires OcrAdapterProtocol; "
                    "silent MarkItDown fallback is forbidden."
                )
            toc_pages = list(range(1, min(n_pages, toc_scan_limit) + 1))
            try:
                toc_ocr = await ocr_adapter.ocr_pdf_pages(
                    _slice_pdf_pages(pdf_bytes, toc_pages),
                    toc_pages,
                    language="vi",
                )
            except Exception as exc:
                raise InputBuildError(f"Pass-1 TOC OCR failed for {ticker}: {exc}") from exc
            ocr_pages_used += len(toc_pages)
            page_texts.update(toc_ocr)

            toc_joined = "\n".join(toc_ocr.get(p, "") for p in toc_pages)
            if toc_router is not None and toc_joined.strip():
                section_spans = await toc_router.route_toc(
                    ticker=ticker.upper(),
                    doc_type=canonical_doc_type,
                    toc_or_outline_text=toc_joined,
                    total_pdf_pages=n_pages,
                )
                if section_spans:
                    localization_method = "LLM_TOC_ROUTER"
            if not section_spans:
                section_spans = parse_toc_section_spans(
                    toc_joined, max_pages=n_pages, doc_type=canonical_doc_type
                )
                if section_spans:
                    localization_method = "DETERMINISTIC_TOC"

            if section_spans:
                span_page_map = _map_spans_to_pdf_pages(
                    section_spans, calibration=calibration, max_pages=n_pages
                )
                target_pages = sorted(
                    pno for pno, sp in span_page_map.items() if sp.slot != "EXCLUDE"
                )
            else:
                target_pages = parse_toc_target_pages(
                    toc_joined, max_pages=n_pages, calibration=calibration
                )

            if not target_pages:
                target_pages = [
                    p for p in range(1, min(n_pages, max_targeted_ocr_pages) + 1) if p not in toc_pages
                ]
            remaining_targets = [p for p in target_pages if p not in page_texts][:max_targeted_ocr_pages]
            if remaining_targets:
                try:
                    pass2_ocr = await ocr_adapter.ocr_pdf_pages(
                        _slice_pdf_pages(pdf_bytes, remaining_targets),
                        remaining_targets,
                        language="vi",
                    )
                except Exception as exc:
                    raise InputBuildError(f"Pass-2 targeted OCR failed for {ticker}: {exc}") from exc
                ocr_pages_used += len(remaining_targets)
                page_texts.update(pass2_ocr)
        else:
            # Hybrid/Digital/BadFont: extract clean digital pages at 0đ, route BAD_FONT/SCAN to OCR
            ocr_needed: list[int] = []
            for pno in range(1, n_pages + 1):
                t_info = triage_by_page.get(pno)
                if t_info and t_info.triage_class == "DIGITAL_CLEAN":
                    page_texts[pno] = _extract_digital_page_text(
                        doc[pno - 1], left_sidebar_x=inspection.auto_sidebar_x
                    )
                else:
                    ocr_needed.append(pno)

            if ocr_needed:
                bad_font_set = set(inspection.bad_font_page_numbers)
                must_ocr = [p for p in ocr_needed if p in bad_font_set] or (
                    ocr_needed if inspection.routed_branch in ("B", "D") else []
                )
                must_ocr = must_ocr[:max_targeted_ocr_pages]
                if must_ocr:
                    if ocr_adapter is None:
                        if inspection.doc_class == "BAD_FONT" or bad_font_set:
                            raise InputBuildError(
                                f"BAD_FONT pages {sorted(bad_font_set)} require OcrAdapterProtocol; fail-closed."
                            )
                    else:
                        try:
                            ocr_res = await ocr_adapter.ocr_pdf_pages(
                                _slice_pdf_pages(pdf_bytes, must_ocr),
                                must_ocr,
                                language="vi",
                            )
                        except Exception as exc:
                            raise InputBuildError(f"Targeted OCR failed for {ticker}: {exc}") from exc
                        ocr_pages_used += len(must_ocr)
                        page_texts.update(ocr_res)

            # Layer 1A -> 1B -> 1C localization on digital/hybrid filings
            if canonical_doc_type not in ("NQ_DHDCD", "LATEST_QUARTER", "GIAI_TRINH"):
                bm_spans = extract_pdf_outline_spans(doc, doc_type=canonical_doc_type)
                if bm_spans:
                    section_spans = bm_spans
                    spans_already_pdf = True
                    localization_method = "PDF_BOOKMARKS"
                else:
                    digest, is_explicit_toc = extract_toc_or_heading_digest(doc, page_texts)
                    if toc_router is not None and digest:
                        llm_spans = await toc_router.route_toc(
                            ticker=ticker.upper(),
                            doc_type=canonical_doc_type,
                            toc_or_outline_text=digest,
                            total_pdf_pages=n_pages,
                        )
                        if llm_spans:
                            section_spans = llm_spans
                            spans_already_pdf = not is_explicit_toc
                            localization_method = "LLM_TOC_ROUTER"
                    if not section_spans and is_explicit_toc:
                        det_spans = parse_toc_section_spans(
                            digest, max_pages=n_pages, doc_type=canonical_doc_type
                        )
                        if det_spans:
                            section_spans = det_spans
                            spans_already_pdf = False
                            localization_method = "DETERMINISTIC_TOC"
    finally:
        n_pages_total = len(doc)
        doc.close()

    # Layer 3: Stateful Section Span assignment + Heading-aware boilerplate + Safety Net
    span_by_pdf_page = (
        _map_spans_to_pdf_pages(
            section_spans,
            calibration=calibration,
            max_pages=n_pages_total,
            already_pdf_indexed=spans_already_pdf,
        )
        if section_spans
        else {}
    )

    slot_chunks: dict[str, list[tuple[int, str]]] = {}
    pr_chunks: list[str] = []
    active_slot: str | None = None

    for pno in sorted(page_texts):
        txt = (page_texts[pno] or "").strip()
        if not txt:
            continue

        mapped_span = span_by_pdf_page.get(pno)
        if mapped_span is not None:
            if mapped_span.slot == "EXCLUDE" and not _page_has_financial_table_shield(txt):
                build_res.excluded_pages[pno] = f"TOC_EXCLUDE:{mapped_span.title[:48]}"
                if re.search(r"thư|thông\s+điệp|chairman|ceo", mapped_span.title, re.IGNORECASE):
                    pr_chunks.append(f"<!-- PAGE {pno} -->\n{txt}")
                active_slot = None
                continue
            if mapped_span.slot != "EXCLUDE":
                slot_chunks.setdefault(mapped_span.slot, []).append((pno, txt))
                active_slot = mapped_span.slot
                continue

        bp_reason = classify_page_boilerplate(txt, doc_type=canonical_doc_type)
        if bp_reason is not None:
            build_res.excluded_pages[pno] = bp_reason
            if bp_reason == "PR_LETTER":
                pr_chunks.append(f"<!-- PAGE {pno} -->\n{txt}")
            active_slot = None
            continue

        slot = classify_text_to_slot(
            txt,
            doc_type=canonical_doc_type,
            previous_slot=active_slot,
        )
        if slot is None:
            continue
        active_slot = slot
        slot_chunks.setdefault(slot, []).append((pno, txt))

    # Structural safety net: check missing core audit/note slots on BCTN/BCTC annual filings
    if canonical_doc_type in ("BCTN", "BCTC"):
        present = set(slot_chunks.keys())
        missing = [s for s in CORE_AUDIT_NOTE_SLOTS if s not in present]
        if missing:
            for pno in sorted(page_texts):
                txt = (page_texts[pno] or "").strip()
                if not txt or pno in build_res.excluded_pages:
                    continue
                rescued = _structural_safety_net_slot(txt)
                if rescued and rescued in missing:
                    slot_chunks.setdefault(rescued, []).append((pno, txt))
                    missing.remove(rescued)
        build_res.missing_core_slots = [s for s in CORE_AUDIT_NOTE_SLOTS if s not in slot_chunks]

    for slot in ALL_SUB_DOC_SLOTS:
        chunks = slot_chunks.get(slot)
        if not chunks:
            continue
        # Deduplicate pages within the same slot while preserving PDF page order
        seen_p: set[int] = set()
        ordered_chunks: list[tuple[int, str]] = []
        for pno, body in sorted(chunks, key=lambda item: item[0]):
            if pno not in seen_p:
                seen_p.add(pno)
                ordered_chunks.append((pno, body))
        page_nums = [p for p, _ in ordered_chunks]
        md_parts = [f"<!-- PAGE {p} -->\n{body}" for p, body in ordered_chunks]
        md_content = "\n\n".join(md_parts)
        build_res.sub_documents.append(
            SubDocumentBundle(
                project_id=ticker.upper(),
                sub_doc_slot=slot,
                half_life_days=SLOT_HALF_LIFE_DAYS[slot],
                fiscal_year=resolved_fy,
                fiscal_quarter=resolved_fq,
                doc_type=canonical_doc_type,
                published_date=published_date,
                valid_from=valid_from_dt,
                routed_branch=inspection.routed_branch,
                page_numbers=page_nums,
                markdown_content=md_content,
                content_hash=hashlib.sha256(md_content.encode("utf-8")).hexdigest(),
                ocr_pages_used=ocr_pages_used,
                source_url=source_url,
                title=title,
            )
        )

    build_res.pr_narrative_markdown = "\n\n".join(pr_chunks)
    build_res.ocr_pages_used = ocr_pages_used
    build_res.localization_method = localization_method
    build_res.page_calibration = calibration
    build_res.section_spans = section_spans
    return build_res
