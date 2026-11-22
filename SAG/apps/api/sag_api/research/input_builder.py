"""HOSE ResearchInputBuilder: 4 Natural Document Classes + 0%-Drop BCTC + SAG Native Tree.

Architecture v2.1 (eliminating artificial 13-slot shredding in favor of SAG v2's native tree):
- 4 Natural Document Classes matching real HOSE filings 1-to-1:
  1. `BCTN_CORE` (`365d`): Core Annual Report narrative, MD&A, operating scale, plans, risks —
     cleaned via 3-Way BCTN Triage (`KEEP` / `PR` / `DROP`) with Financial-Table Shield.
  2. `BCTN_PR` (`365d`): Separated Chairman/CEO PR letter (mirrored in `pr_narrative_markdown`
     so Discrepancy Detector audits management promises vs audited figures without polluting MOAT).
  3. `BCTC_AUDITED` (`365d`, **0%-Drop Invariant**): Complete Audited Financial Statements
     (`Audit Opinion + B01..B03 + 100% Notes B09 / TT49 / TT334`) preserved in original company
     order with normalized Markdown `#/##` headings so SAG v2 builds the exact native Note tree.
     Covers BOTH standalone `BCTC` filings AND embedded BCTC sections inside `BCTN` filings.
  4. `AGM_TARGETS` (`180d`) & `QUARTER_NOTES` (`90d`): `NQ_DHDCD` (excluding pure ballot-counting
     minutes) and `LATEST_QUARTER` / `GIAI_TRINH` (0%-drop).
- Zero-Hardcode Layout Hygiene (0 LLM):
  - `detect_repeating_running_lines`: auto-strips repeating page headers/footers (e.g.
    `THUYẾT MINH BCTC (Tiếp theo) | MẪU SỐ B 09-DN/HN`) before heading extraction.
  - `calibrate_printed_to_pdf_mapping`: auto-detects `SINGLE_PAGE` cover offset & `SPREAD_A3`
    2-page spreads (e.g. CTG, PVT, VCI, MWG).
  - `detect_embedded_bctc_start_page`: locates embedded audited FS + Notes inside BCTN filings
    so digital BCTNs (FPT, CTG, PVT, VCI) deliver audited Notes at 0đ OCR.
  - `normalize_financial_markdown_headings`: promotes PyMuPDF bold/large/numbered/ALL-CAPS
    Note titles into Markdown `#/##` while preserving `<!-- PAGE N -->` anchors.
  - Separate OCR budgets: `max_targeted_ocr_pages=35` for BCTN narrative, up to `120` pages
    for BCTC / embedded scanned BCTC so scanned Notes (46–99p) are never truncated.
  - `DocumentCoverageAudit`: full transparency report per filing (`bctc_notes_coverage_pct`,
    `noise_reduction_pct`, `tt200_policy_pages`, `sample_headings`, `dropped_by_reason`).
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
    "BCTN_CORE",
    "BCTN_PR",
    "BCTC_AUDITED",
    "AGM_TARGETS",
    "QUARTER_NOTES",
]

SLOT_HALF_LIFE_DAYS: dict[str, int] = {
    "BCTN_CORE": 365,
    "BCTN_PR": 365,
    "BCTC_AUDITED": 365,
    "AGM_TARGETS": 180,
    "QUARTER_NOTES": 90,
}

ALL_SUB_DOC_SLOTS: tuple[str, ...] = tuple(SLOT_HALF_LIFE_DAYS.keys())
VALID_SLOT_SET: frozenset[str] = frozenset(ALL_SUB_DOC_SLOTS)

# Backward-compatible mapping from legacy S01..S13 labels to the 4 Natural Document Classes.
LEGACY_SLOT_TO_NATURAL: dict[str, str] = {
    "S01_MDNA_SEGMENT": "BCTN_CORE",
    "S02_OPERATING_SCALE": "BCTN_CORE",
    "S03_PLAN_CAPEX": "BCTN_CORE",
    "S04_RISK_SWOT": "BCTN_CORE",
    "S05_AUDIT_OPINION": "BCTC_AUDITED",
    "S06_SEGMENT_NOTE": "BCTC_AUDITED",
    "S07_REV_COGS_DETAIL": "BCTC_AUDITED",
    "S08_FA_DEPR_CIP": "BCTC_AUDITED",
    "S09_FIN_INVEST_PROV": "BCTC_AUDITED",
    "S10_DEBT_LEASE_FX": "BCTC_AUDITED",
    "S11_CAPITAL_POST_EVT": "BCTC_AUDITED",
    "S12_AGM_TARGETS": "AGM_TARGETS",
    "S13_QUARTER_NOTES": "QUARTER_NOTES",
    "KEEP": "BCTN_CORE",
    "CORE_KEEP": "BCTN_CORE",
    "PR": "BCTN_PR",
    "PR_SEPARATE": "BCTN_PR",
    "DROP": "EXCLUDE",
    "DROP_NOISE": "EXCLUDE",
}


def normalize_natural_slot(raw_slot: str) -> str | None:
    """Normalize either a canonical 4-class slot or legacy S01..S13 / triage token."""
    token = (raw_slot or "").strip().upper()
    if token == "EXCLUDE":
        return "EXCLUDE"
    if token in VALID_SLOT_SET:
        return token
    return LEGACY_SLOT_TO_NATURAL.get(token)


class InputBuildError(RuntimeError):
    """Fail-closed error raised when PDF building, OCR, or PIT validation fails."""


class OcrAdapterProtocol(Protocol):
    """Abstract targeted OCR adapter (e.g. MinerU Official API v4 with language='vi')."""

    async def ocr_pdf_pages(
        self,
        pdf_bytes: bytes,
        page_numbers: list[int],
        *,
        language: str = "vi",
    ) -> dict[int, str]: ...


@dataclass(slots=True)
class TocSectionSpan:
    """A section range mapped to one of the 4 natural classes (or `EXCLUDE` for BCTN noise)."""

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
    """Optional 1-call semantic BCTN 3-Way Triage Router (`KEEP` / `PR` / `DROP` / `BCTC_AUDITED`)."""

    async def route_toc(
        self,
        *,
        ticker: str,
        doc_type: str,
        toc_or_outline_text: str,
        total_pdf_pages: int,
    ) -> list[TocSectionSpan]: ...


@dataclass(slots=True)
class DocumentCoverageAudit:
    """Transparent per-filing coverage audit proving 0%-drop on BCTC Notes & BCTN noise reduction."""

    total_pdf_pages: int = 0
    digital_clean_pages: int = 0
    scan_pages: int = 0
    bad_font_pages: int = 0
    bctn_core_pages: int = 0
    bctn_pr_pages: int = 0
    bctc_audited_pages: int = 0
    agm_or_quarter_pages: int = 0
    dropped_pages: int = 0
    noise_reduction_pct: float = 0.0
    embedded_bctc_start_page: int | None = None
    bctc_total_pages: int = 0
    bctc_kept_pages: int = 0
    bctc_notes_coverage_pct: float = 100.0
    tt200_policy_pages: list[int] = field(default_factory=list)
    running_lines_stripped_count: int = 0
    normalized_headings_count: int = 0
    sample_headings: list[str] = field(default_factory=list)
    dropped_by_reason: dict[str, int] = field(default_factory=dict)
    audited_fs_missing_warning: bool = False


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
    localization_method: str = "NATIVE_4CLASS_DETERMINISTIC"
    page_calibration: PageOffsetCalibration = field(default_factory=PageOffsetCalibration)
    section_spans: list[TocSectionSpan] = field(default_factory=list)
    missing_core_slots: list[str] = field(default_factory=list)
    coverage_audit: DocumentCoverageAudit = field(default_factory=DocumentCoverageAudit)


# ── Pattern Bank (Strictly scoped to 3-Way Triage & Native Structure) ────────

_PR_HEADING_RE = re.compile(
    r"(?:thư\s+(?:của\s+)?(?:chủ\s+tịch|tổng\s+giám\s+đốc|người\s+đứng\s+đầu|gửi\s+(?:quý\s+)?cổ\s+đông)|"
    r"kính\s+thưa\s+(?:quý\s+)?cổ\s+đông|"
    r"message\s+from\s+(?:the\s+)?(?:chairman|ceo|board)|"
    r"thông\s+điệp\s+(?:của\s+)?(?:chủ\s+tịch|ban\s+lãnh\s+đạo|tổng\s+giám\s+đốc|hđqt))",
    re.IGNORECASE,
)

_ESG_HEADING_RE = re.compile(
    r"(?:báo\s+cáo\s+(?:phát\s+triển\s+bền\s+vững|esg|môi\s+trường\s*[–\-]\s*xã\s+hội)|"
    r"phát\s+triển\s+bền\s+vững(?:\s+và\s+trách\s+nhiệm|\s+năm\s+20\d{2}|$)|"
    r"trách\s+nhiệm\s+xã\s+hội|hoạt\s+động\s+(?:từ\s+thiện|cộng\s+đồng|an\s+sinh\s+xã\s+hội)|"
    r"phát\s+thải\s+khí\s+nhà\s+kính|bảo\s+vệ\s+môi\s+trường\s+và\s+cộng\s+đồng|\bbáo\s+cáo\s+esg\b)",
    re.IGNORECASE,
)

_BCTN_FILLER_HEADING_RE = re.compile(
    r"(?:danh\s+mục\s+(?:chữ\s+)?viết\s+tắt|giải\s+thưởng\s+(?:tiêu\s+biểu|và\s+danh\s+hiệu)|"
    r"thông\s+tin\s+liên\s+hệ\s+báo\s+cáo|phản\s+hồi\s+của\s+độc\s+giả|bảng\s+tham\s+chiếu\s+gri)",
    re.IGNORECASE,
)

_AGM_PROCEDURAL_RE = re.compile(
    r"(?:biên\s+bản\s+kiểm\s+phiếu|thể\s+lệ\s+biểu\s+quyết|danh\s+sách\s+ban\s+kiểm\s+phiếu|"
    r"chương\s+trình\s+đại\s+hội|giấy\s+ủy\s+quyền\s+tham\s+dự|phiếu\s+biểu\s+quyết)",
    re.IGNORECASE,
)

_TT200_POLICY_RE = re.compile(
    r"(?:tóm\s+tắt\s+các\s+chính\s+sách\s+kế\s+toán\s+chủ\s+yếu|"
    r"các\s+chính\s+sách\s+kế\s+toán\s+(?:chủ\s+yếu|áp\s+dụng)|"
    r"chuẩn\s+mực\s+kế\s+toán\s+việt\s+nam|cơ\s+sở\s+lập\s+báo\s+cáo\s+tài\s+chính)",
    re.IGNORECASE,
)

_POLICY_CHANGE_RE = re.compile(
    r"(?:thay\s+đổi\s+chính\s+sách\s+kế\s+toán|áp\s+dụng\s+hồi\s+tố|điều\s+chỉnh\s+hồi\s+tố|sai\s+sót\s+trọng\s+yếu)",
    re.IGNORECASE,
)

# Financial statements & Notes signatures across TT200 (Corporates), TT49 (Banks), TT334 (Securities)
_FINANCIAL_SHIELD_RE = re.compile(
    r"(?:bảng\s+cân\s+đối\s+kế\s+toán|báo\s+cáo\s+tình\s+hình\s+tài\s+chính|"
    r"báo\s+cáo\s+kết\s+quả\s+hoạt\s+động|báo\s+cáo\s+lưu\s+chuyển\s+tiền\s+tệ|"
    r"báo\s+cáo\s+tình\s+hình\s+biến\s+động\s+vốn|"
    r"\bmẫu\s+số\s+b\s*0[1-9]\b|\bb\s*0[1-9][a-z]?\s*-\s*(?:dn|ctck)\b|\bb\s*0[1-5]\s*/\s*tctd\b|"
    r"thuyết\s+minh\s+báo\s+cáo\s+tài\s+chính|thuyết\s+minh\s+(?:số\s*)?\d+|"
    r"báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập|ý\s+kiến\s+của\s+kiểm\s+toán\s+viên)",
    re.IGNORECASE,
)

_BCTC_SECTION_CUE_RE = re.compile(
    r"(?:báo\s+cáo\s+tài\s+chính(?:\s+hợp\s+nhất)?(?:\s+đã\s+(?:được\s+)?kiểm\s+toán)?|"
    r"báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập|báo\s+cáo\s+của\s+ban\s+(?:tổng\s+)?giám\s+đốc|"
    r"bảng\s+cân\s+đối\s+kế\s+toán|báo\s+cáo\s+tình\s+hình\s+tài\s+chính|"
    r"thuyết\s+minh\s+báo\s+cáo\s+tài\s+chính|"
    r"\bmẫu\s+số\s+b\s*0[1-9]\b|\bb\s*0[1-9][a-z]?\s*-\s*(?:dn|ctck)\b|\bb\s*0[1-5]\s*/\s*tctd\b)",
    re.IGNORECASE,
)

_CORE_BCTN_EXIT_RE = re.compile(
    r"(?:tổng\s+quan|kết\s+quả\s+(?:hoạt\s+động\s+)?kinh\s+doanh|tình\s+hình\s+hoạt\s+động|"
    r"kế\s+hoạch\s+(?:kinh\s+doanh|năm\s+20\d{2}|đầu\s+tư)|quản\s+trị\s+(?:công\s+ty|rủi\s+ro)|"
    r"báo\s+cáo\s+của\s+(?:hđqt|hội\s+đồng\s+quản\s+trị|ban\s+tổng\s+giám\s+đốc|ban\s+kiểm\s+soát)|"
    r"đánh\s+giá\s+kqkd|vị\s+thế\s+và\s+quy\s+mô|tình\s+hình\s+tài\s+chính|"
    r"chương\s+[ivx\d]+|phần\s+[ivx\d]+)",
    re.IGNORECASE,
)

_ACCOUNTING_NUMBER_RE = re.compile(r"(?<!\d)(?:\d{1,3}(?:[.,]\d{3}){2,})(?!\d)")
_TOC_PAGE_CUE_RE = re.compile(r"(?:mục\s+lục|table\s+of\s+contents|\bcontents\b)", re.IGNORECASE)
_TOC_LINE_RE = re.compile(r"^(?P<title>.{4,140}?)(?:\.{2,}|\s{2,}|\s+)(?P<page>\d{1,3})\s*$")
_AUDIT_FIRM_SIG_RE = re.compile(
    r"(?:deloitte|pwc|pricewaterhousecoopers|ernst\s*&\s*young|\bey\b|kpmg|aasc|a&c|moore|vacpa)",
    re.IGNORECASE,
)

# Native Markdown Heading Promotion patterns for SAG v2 Tree Builder
_MAJOR_FS_HEADING_RE = re.compile(
    r"^(?:báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập|báo\s+cáo\s+của\s+ban\s+(?:tổng\s+)?giám\s+đốc|"
    r"bảng\s+cân\s+đối\s+kế\s+toán(?:\s+hợp\s+nhất)?|báo\s+cáo\s+tình\s+hình\s+tài\s+chính(?:\s+hợp\s+nhất)?|"
    r"báo\s+cáo\s+kết\s+quả\s+hoạt\s+động(?:\s+kinh\s+doanh)?(?:\s+hợp\s+nhất)?|"
    r"báo\s+cáo\s+lưu\s+chuyển\s+tiền\s+tệ(?:\s+hợp\s+nhất)?|"
    r"báo\s+cáo\s+tình\s+hình\s+biến\s+động\s+vốn\s+chủ\s+sở\s+hữu|"
    r"thuyết\s+minh\s+báo\s+cáo\s+tài\s+chính(?:\s+hợp\s+nhất)?)$",
    re.IGNORECASE,
)

_NUMBERED_NOTE_HEADING_RE = re.compile(
    r"^(?:(?:thuyết\s+minh\s+(?:số\s*)?)?(?:\d{1,2}|[IVX]{1,4})(?:\.\d{1,2})?[.)]\s+\S.{2,110})$",
    re.IGNORECASE,
)

_DIGITAL_SIG_JUNK_RE = re.compile(
    r"(?:\boid\.\d|\bdn:\s*c=|\bcn=|\bserialnumber=|digitally\s+signed|signed\s+by|"
    r"ký\s+bởi:|ngày\s+ký:)",
    re.IGNORECASE,
)

_BOOKMARK_FILENAME_JUNK_RE = re.compile(
    r"(?:_[a-z0-9]{1,8}$|\.(?:pdf|docx?|indd)$|\bsmall\b|\ball_s\b|^p\d+-\d+|_vie_|_eng_|co\s+dau)",
    re.IGNORECASE,
)

_REAL_BOOKMARK_TITLE_RE = re.compile(
    r"(?:chương|phần|tổng\s+quan|kết\s+quả|kinh\s+doanh|quản\s+trị|kế\s+hoạch|rủi\s+ro|"
    r"báo\s+cáo|thuyết\s+minh|kiểm\s+toán|thông\s+điệp|thư\s+chủ\s+tịch|phát\s+triển\s+bền\s+vững|\besg\b)",
    re.IGNORECASE,
)

_TABLE_HEADER_NON_NOTE_RE = re.compile(
    r"^(?:chỉ\s+tiêu|mã\s+số|thuyết\s+minh|ghi\s+chú|stt|đơn\s+vị(?:\s+tính)?|vnd|vnđ|usd|"
    r"số\s+cuối\s+(?:năm|kỳ)|số\s+đầu\s+(?:năm|kỳ)|năm\s+nay|năm\s+trước|kỳ\s+này|kỳ\s+trước|"
    r"tổng\s+cộng|cộng|tài\s+sản|nguồn\s+vốn|nguyên\s+giá|giá\s+trị\s+còn\s+lại|giá\s+gốc|"
    r"mẫu\s+số\s+b\s*0\d|ban\s+hành\s+theo\s+thông\s+tư|trang\s+\d+)$",
    re.IGNORECASE,
)

_ACCOUNTING_NOTE_KEYWORDS_RE = re.compile(
    r"(?:tiền\s+và\s+các\s+khoản|đầu\s+tư\s+tài\s+chính|phải\s+thu|dự\s+phòng|hàng\s+tồn\s+kho|"
    r"tài\s+sản\s+cố\s+định|xây\s+dựng\s+cơ\s+bản|bất\s+động\s+sản\s+đầu\s+tư|lợi\s+thế\s+thương\s+mại|"
    r"vay\s+và\s+nợ|trái\s+phiếu|thuê\s+(?:tài\s+chính|hoạt\s+động)|vốn\s+chủ\s+sở\s+hữu|cổ\s+tức|"
    r"doanh\s+thu|giá\s+vốn|chi\s+phí\s+(?:tài\s+chính|bán\s+hàng|quản\s+lý|sản\s+xuất)|"
    r"thuế\s+thu\s+nhập|lãi\s+cơ\s+bản\s+trên\s+cổ\s+phiếu|báo\s+cáo\s+bộ\s+phận|bộ\s+phận\s+theo|"
    r"giao\s+dịch\s+với\s+các\s+bên\s+liên\s+quan|bên\s+liên\s+quan|công\s+ty\s+(?:con|liên\s+kết)|"
    r"cam\s+kết|nợ\s+tiềm\s+tàng|kiện\s+tụng|rủi\s+ro\s+(?:tài\s+chính|tín\s+dụng|tỷ\s+giá|lãi\s+suất|thanh\s+khoản)|"
    r"sự\s+kiện\s+phát\s+sinh\s+sau|khoản\s+mục\s+ngoài\s+bảng|"
    r"cho\s+vay\s+khách\s+hàng|chất\s+lượng\s+nợ|tiền\s+gửi\s+của\s+khách\s+hàng|chứng\s+khoán\s+đầu\s+tư|"
    r"fvtpl|afs|htm\b|cho\s+vay\s+ký\s+quỹ|tài\s+sản\s+tài\s+chính|an\s+toàn\s+vốn|"
    r"đặc\s+điểm\s+hoạt\s+động|chính\s+sách\s+kế\s+toán)",
    re.IGNORECASE,
)


def _extract_heading_zone(text: str, *, max_lines: int = 8, max_chars: int = 480) -> str:
    """Return top heading zone of a page so casual body mentions do not hijack triage."""
    non_empty = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    if not non_empty:
        return ""
    return "\n".join(non_empty[:max_lines])[:max_chars]


def _page_has_financial_table_shield(text: str) -> bool:
    """True when page carries audited statement headers or dense accounting figures (>= 6)."""
    if _FINANCIAL_SHIELD_RE.search(text or ""):
        return True
    return len(_ACCOUNTING_NUMBER_RE.findall(text or "")) >= 6


def _normalize_running_line_key(line: str) -> str:
    """Normalize a line for repeating header/footer detection (collapsing standalone page numbers)."""
    s = re.sub(r"\s+", " ", (line or "").strip()).lower()
    s = re.sub(r"\b\d{1,3}\b", "#", s)
    return s


def detect_repeating_running_lines(
    page_texts: dict[int, str],
    *,
    edge_lines: int = 5,
    min_ratio: float = 0.22,
    min_pages: int = 4,
) -> set[str]:
    """Zero-hardcode detector for repeating top/bottom running headers & footers across pages.

    Catches patterns like `THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT (Tiếp theo)`,
    `MẪU SỐ B 09-DN/HN`, `Ban hành theo Thông tư số 202/2014/TT-BTC`, or `PVTRANS | BCTN 2025`
    without hardcoding company names or form codes.
    """
    n_non_empty = sum(1 for t in page_texts.values() if len((t or "").strip()) >= 30)
    if n_non_empty < min_pages:
        return set()

    line_page_freq: Counter[str] = Counter()
    for txt in page_texts.values():
        lines = [ln.strip() for ln in (txt or "").splitlines() if ln.strip()]
        if len(lines) < 3:
            continue
        edge_subset = lines[:edge_lines] + lines[-edge_lines:]
        seen_on_page: set[str] = set()
        for ln in edge_subset:
            if len(ln) > 120 or len(ln) < 3:
                continue
            if len(_ACCOUNTING_NUMBER_RE.findall(ln)) >= 2:
                continue
            key = _normalize_running_line_key(ln)
            if key and key not in ("#", "# #") and key not in seen_on_page:
                seen_on_page.add(key)
                line_page_freq[key] += 1

    threshold = max(min_pages, int(n_non_empty * min_ratio))
    return {k for k, cnt in line_page_freq.items() if cnt >= threshold}


def strip_running_lines(text: str, running_keys: set[str]) -> tuple[str, int]:
    """Strip detected repeating header/footer lines while preserving all real headings & tables."""
    if not running_keys or not text:
        return text, 0
    kept: list[str] = []
    removed = 0
    for raw_ln in text.splitlines():
        s = raw_ln.strip()
        if s and len(s) <= 120 and len(_ACCOUNTING_NUMBER_RE.findall(s)) < 2:
            if _normalize_running_line_key(s) in running_keys:
                removed += 1
                continue
        kept.append(raw_ln)
    return "\n".join(kept).strip(), removed


def classify_page_boilerplate(text: str, *, doc_type: str) -> str | None:
    """Heading-aware boilerplate triage with strict 0%-drop guard for BCTC & Financial Tables.

    - For `BCTC` / `LATEST_QUARTER` / `GIAI_TRINH`: NEVER drops financial notes or statements
      (only flags empty/cover `< 30` chars).
    - For `NQ_DHDCD`: drops only pure procedural ballot-counting pages (`AGM_PROCEDURAL`).
    - For `BCTN`: separates `PR_LETTER` and drops `ESG_CHARITY` / `FILLER_NON_CORE` ONLY when
      the top title zone matches AND the page is NOT shielded by financial tables/codes.
    """
    raw = (text or "").strip()
    if len(raw) < 30:
        return "EMPTY_OR_COVER"

    canonical = normalize_doc_type(doc_type)
    if canonical in ("BCTC", "LATEST_QUARTER", "GIAI_TRINH"):
        # 0%-drop invariant on BCTC / Quarter Notes: never drop content pages here.
        return None

    title_zone = _extract_heading_zone(raw, max_lines=3, max_chars=260)
    pr_zone = _extract_heading_zone(raw, max_lines=6, max_chars=520)
    has_fin_shield = _page_has_financial_table_shield(raw)

    if canonical == "NQ_DHDCD":
        if _AGM_PROCEDURAL_RE.search(title_zone) and not has_fin_shield:
            return "AGM_PROCEDURAL"
        return None

    # BCTN / CAO_BACH narrative triage (shielded by financial tables & core business headings)
    if has_fin_shield:
        return None
    if _PR_HEADING_RE.search(pr_zone):
        return "PR_LETTER"
    if _ESG_HEADING_RE.search(title_zone):
        return "ESG_CHARITY"
    if _BCTN_FILLER_HEADING_RE.search(title_zone):
        return "FILLER_NON_CORE"
    return None


def classify_text_to_slot(
    text: str,
    *,
    doc_type: str,
    previous_slot: str | None = None,
) -> str | None:
    """Route a page's text cleanly into one of the 4 Natural Document Classes."""
    canonical = normalize_doc_type(doc_type)
    if canonical == "NQ_DHDCD":
        return "AGM_TARGETS"
    if canonical in ("LATEST_QUARTER", "GIAI_TRINH"):
        return "QUARTER_NOTES"
    if canonical == "BCTC":
        return "BCTC_AUDITED"

    title_zone = _extract_heading_zone(text or "", max_lines=3, max_chars=260)
    if _BCTC_SECTION_CUE_RE.search(title_zone) and _page_has_financial_table_shield(text or ""):
        return "BCTC_AUDITED"
    if _PR_HEADING_RE.search(title_zone) and not _page_has_financial_table_shield(text or ""):
        return "BCTN_PR"
    if previous_slot in ("BCTC_AUDITED", "BCTN_CORE"):
        return previous_slot
    return "BCTN_CORE"


def detect_embedded_bctc_start_page(
    page_texts: dict[int, str],
    total_pages: int,
) -> int | None:
    """Locate the first PDF page where an embedded Audited Financial Statements section begins
    inside a `BCTN` (e.g., FPT p.161, CTG p.135, PVT p.143, VCI p.71).

    Requires sustained financial-statement/notes evidence in the following pages so casual MD&A
    summary tables in the first half of a BCTN never trigger a false BCTC split.
    """
    if total_pages < 20 or not page_texts:
        return None

    min_start = max(10, int(total_pages * 0.35))
    sorted_pages = [p for p in sorted(page_texts) if p >= min_start]
    if not sorted_pages:
        return None

    # Score each page in the latter portion for strong audited FS / Notes signatures
    def _is_strong_fs_page(txt: str) -> bool:
        low = (txt or "").lower()
        if not low:
            return False
        if re.search(
            r"(?:báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập|mẫu\s+số\s+b\s*0[1-9]|"
            r"\bb\s*0[1-9][a-z]?\s*-\s*(?:dn|ctck)\b|\bb\s*0[1-5]\s*/\s*tctd\b|"
            r"thuyết\s+minh\s+báo\s+cáo\s+tài\s+chính)",
            low,
        ):
            return True
        if (
            re.search(
                r"(?:bảng\s+cân\s+đối\s+kế\s+toán|báo\s+cáo\s+tình\s+hình\s+tài\s+chính|"
                r"báo\s+cáo\s+kết\s+quả\s+hoạt\s+động|báo\s+cáo\s+lưu\s+chuyển\s+tiền\s+tệ)",
                low,
            )
            and len(_ACCOUNTING_NUMBER_RE.findall(txt)) >= 6
        ):
            return True
        return False

    strong_pages = [p for p in sorted_pages if _is_strong_fs_page(page_texts.get(p, ""))]
    # Embedded full BCTC + Notes has >= 6 strong FS/Notes pages (distinct from 2-page summaries)
    if len(strong_pages) < 6:
        return None

    # Find earliest strong page where at least 4 of the next 10 pages are also strong FS/Notes pages
    strong_set = set(strong_pages)
    for cand in strong_pages:
        window_hits = sum(1 for p in range(cand, min(total_pages, cand + 12) + 1) if p in strong_set)
        if window_hits >= 4:
            # Step back 1–2 pages if immediately preceded by a management/FS divider page
            start_p = cand
            for back in (1, 2):
                prev_p = cand - back
                prev_txt = page_texts.get(prev_p, "")
                if prev_p >= min_start and re.search(
                    r"(?:báo\s+cáo\s+của\s+ban\s+(?:tổng\s+)?giám\s+đốc|báo\s+cáo\s+tài\s+chính\s+hợp\s+nhất|"
                    r"báo\s+cáo\s+kiểm\s+toán\s+độc\s+lập)",
                    prev_txt,
                    re.IGNORECASE,
                ):
                    start_p = prev_p
            return start_p
    return None


def _is_tt200_policy_only_page(text: str) -> bool:
    """True when a BCTC page contains boilerplate TT200/TT49 policy text without tables or policy changes."""
    low = (text or "").lower()
    if not low or _POLICY_CHANGE_RE.search(low):
        return False
    if len(_ACCOUNTING_NUMBER_RE.findall(text or "")) >= 6:
        return False
    return bool(_TT200_POLICY_RE.search(low))


def extract_page_bold_headings(page: fitz.Page, *, left_sidebar_x: float = 0.0) -> set[str]:
    """Extract normalized bold or larger-than-body candidate heading lines from a digital PDF page."""
    rect = page.rect
    w, h = float(rect.width), float(rect.height)
    clip = (
        fitz.Rect(left_sidebar_x, 0.0, w, h)
        if (w > h * 1.25 and 0.0 < left_sidebar_x < w * 0.30)
        else None
    )
    page_dict = page.get_text("dict", clip=clip) if clip is not None else page.get_text("dict")
    if not isinstance(page_dict, dict):
        return set()

    sizes: Counter[int] = Counter()
    line_candidates: list[tuple[float, bool, str]] = []
    for blk in page_dict.get("blocks", []):
        if blk.get("type") != 0:
            continue
        for line in blk.get("lines", []):
            spans = line.get("spans", [])
            if not spans:
                continue
            max_sz = max(float(sp.get("size", 0.0)) for sp in spans)
            is_bold = any(
                (int(sp.get("flags", 0)) & 16) != 0 or "bold" in str(sp.get("font", "")).lower()
                for sp in spans
            )
            txt = " ".join(str(sp.get("text", "")).strip() for sp in spans).strip()
            txt = normalize_ctg_font_quirks(txt)
            if len(txt) < 4:
                continue
            sizes[round(max_sz)] += 1
            if len(txt) <= 130:
                line_candidates.append((max_sz, is_bold, txt))

    if not sizes:
        return set()
    body_sz = sizes.most_common(1)[0][0]
    bold_set: set[str] = set()
    for sz, is_bold, txt in line_candidates:
        if is_bold or sz >= max(body_sz * 1.15, body_sz + 1.5):
            bold_set.add(re.sub(r"\s+", " ", txt).strip().lower())
    return bold_set


def normalize_financial_markdown_headings(
    text: str,
    *,
    bold_lines: set[str] | None = None,
    is_bctc: bool = True,
) -> tuple[str, list[str]]:
    """Promote real document/note headings into Markdown `#` / `##` for SAG v2's native Hierarchy Tree.

    Handles both digital PyMuPDF text (using bold/ALL-CAPS/numbered Note signals) and MinerU
    Markdown (preserving existing `#/##` headings while avoiding false promotion of table rows).
    """
    if not text:
        return "", []
    bold_lookup = bold_lines or set()
    out_lines: list[str] = []
    headings: list[str] = []

    for raw_ln in text.splitlines():
        stripped = raw_ln.strip()
        if not stripped:
            out_lines.append("")
            continue
        # Preserve existing Markdown headings (e.g. from MinerU OCR)
        if stripped.startswith("#"):
            clean_h = stripped.lstrip("#").strip()
            if clean_h and len(clean_h) <= 140:
                headings.append(clean_h)
            out_lines.append(raw_ln)
            continue
        # Never promote HTML tables, page anchors, or numeric table rows
        if stripped.startswith("<") or stripped.startswith("|") or len(stripped) > 130 or len(stripped) < 4:
            out_lines.append(raw_ln)
            continue
        if len(_ACCOUNTING_NUMBER_RE.findall(stripped)) >= 2:
            out_lines.append(raw_ln)
            continue
        if _DIGITAL_SIG_JUNK_RE.search(stripped):
            out_lines.append(raw_ln)
            continue
        if _TABLE_HEADER_NON_NOTE_RE.match(stripped):
            out_lines.append(raw_ln)
            continue

        norm_key = re.sub(r"\s+", " ", stripped).strip().lower()
        if _MAJOR_FS_HEADING_RE.match(stripped):
            out_lines.append(f"# {stripped}")
            headings.append(stripped)
            continue

        is_bold = norm_key in bold_lookup
        alpha_chars = [c for c in stripped if c.isalpha()]
        is_all_caps = len(alpha_chars) >= 6 and all(c.isupper() for c in alpha_chars)

        if is_bctc:
            # Promote numbered BCTC notes (e.g. "25. Doanh thu...", "V.1 Tiền...", "12. Hàng tồn kho")
            if _NUMBERED_NOTE_HEADING_RE.match(stripped) and (
                is_bold or is_all_caps or _ACCOUNTING_NOTE_KEYWORDS_RE.search(stripped)
            ):
                out_lines.append(f"## {stripped}")
                headings.append(stripped)
                continue
            # Promote standalone ALL-CAPS or bold accounting note titles (e.g. PVT InDesign notes)
            if (
                (is_all_caps or is_bold)
                and _ACCOUNTING_NOTE_KEYWORDS_RE.search(stripped)
                and not stripped.endswith((".", ",", ";"))
            ):
                out_lines.append(f"## {stripped}")
                headings.append(stripped)
                continue
        else:
            # BCTN chapter/section headings
            if (is_all_caps and len(stripped) <= 95 and not stripped.endswith((".", ","))) or (
                is_bold and re.match(r"^(?:chương|phần|\d{1,2}\.)\s+\S", stripped, re.IGNORECASE)
            ):
                out_lines.append(f"## {stripped}")
                headings.append(stripped)
                continue

        out_lines.append(raw_ln)

    return "\n".join(out_lines), headings


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
                        spread_offsets.append(float(2 * pdf_no - rv))
                        matched_spread = True
                        break
                if matched_spread:
                    break
            if matched_spread:
                continue

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


# ── Layer 1A/1B/1C: Bookmarks, TOC/Page-Digest, Optional 1-Call 3-Way BCTN Router ──


def _classify_toc_title_to_natural(title: str) -> str:
    """Map a TOC/bookmark title cleanly into `BCTN_PR`, `EXCLUDE`, `BCTC_AUDITED`, or `BCTN_CORE`."""
    if _PR_HEADING_RE.search(title):
        return "BCTN_PR"
    if _ESG_HEADING_RE.search(title) or _BCTN_FILLER_HEADING_RE.search(title):
        return "EXCLUDE"
    if _BCTC_SECTION_CUE_RE.search(title):
        return "BCTC_AUDITED"
    return "BCTN_CORE"


def extract_pdf_outline_spans(
    doc: fitz.Document,
    *,
    doc_type: str,
) -> list[TocSectionSpan]:
    """Layer 1A (0đ): Parse native PDF bookmarks (`doc.get_toc()`) into natural-class spans."""
    raw_toc = doc.get_toc(simple=True) or []
    n_pages = len(doc)
    if len(raw_toc) < 3 or n_pages == 0:
        return []
    canonical = normalize_doc_type(doc_type)
    if canonical in ("BCTC", "NQ_DHDCD", "LATEST_QUARTER", "GIAI_TRINH"):
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

    # Reject Adobe Acrobat merged-filename artifacts (e.g. `VIE_all_S`, `P1 20 4c - BCTN25 small small`, `p2-7_S`)
    junk_hits = sum(1 for _, t, _ in entries if _BOOKMARK_FILENAME_JUNK_RE.search(t))
    real_hits = sum(1 for _, t, _ in entries if _REAL_BOOKMARK_TITLE_RE.search(t))
    if junk_hits >= len(entries) * 0.4 or real_hits == 0:
        return []

    spans: list[TocSectionSpan] = []
    for idx, (_lvl, title, start_p) in enumerate(entries):
        end_p = n_pages
        for nxt_idx in range(idx + 1, len(entries)):
            if entries[nxt_idx][2] > start_p:
                end_p = max(start_p, entries[nxt_idx][2] - 1)
                break
        slot = _classify_toc_title_to_natural(title)
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
    max_chars: int = 5000,
) -> tuple[str, bool]:
    """Return `(digest_text, is_explicit_toc)` for optional 1-call BCTN 3-Way Triage Router.

    Prefers explicit TOC pages found in the first 12 pages; falls back to a compact 1-line-per-page
    title digest (so even BCTNs with image-only TOCs like FPT p.2 are triaged cleanly).
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

    # Compact 1-line-per-page digest using cleaned digital text (~120 chars/page)
    outline_lines: list[str] = []
    for pno in sorted(page_texts):
        txt = (page_texts.get(pno) or "").strip()
        if len(txt) < 30:
            continue
        head = " | ".join(ln.strip() for ln in txt.splitlines() if ln.strip())[:115]
        outline_lines.append(f"P{pno}: {head}")
    return "\n".join(outline_lines)[:max_chars], False


def parse_toc_section_spans(
    toc_text: str,
    *,
    max_pages: int,
    doc_type: str = "BCTN",
) -> list[TocSectionSpan]:
    """Layer 1C: Deterministic TOC parser producing natural-class `[printed_start..printed_end]` spans."""
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
        slot = _classify_toc_title_to_natural(title)
        spans.append(
            TocSectionSpan(
                slot=slot,
                title=title,
                printed_start=start_p,
                printed_end=end_p,
                confidence=0.78,
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
    """Parse target 1-based PDF page numbers from TOC text for 2-Pass TOC-First OCR on scanned BCTNs."""
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
        max_slice = 60 if sp.slot == "BCTC_AUDITED" else 16
        p_end = min(max_pages, p_end, p_start + max_slice)
        targets.update(range(p_start, p_end + 1))

    if targets:
        return sorted(targets)

    relevant_line_re = re.compile(
        r"(?:kết\s+quả|kinh\s+doanh|kế\s+hoạch|rủi\s+ro|bộ\s+phận|doanh\s+thu|giá\s+vốn|"
        r"kiểm\s+toán|thuyết\s+minh|tài\s+sản|vay\s+nợ|công\s+ty\s+con|vốn\s+chủ|cổ\s+tức|"
        r"nghị\s+quyết|chỉ\s+tiêu|capex|báo\s+cáo\s+tài\s+chính)",
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
    """Layer 1B (Optional): 1-call cheap 3-Way BCTN Triage Router (`DeepSeek-V4-Flash`).

    ONLY used on BCTN filings when `--enable-llm-toc` is enabled. Never called on BCTC/AGM/Quarter.
    Maps BCTN TOC or 1-line-per-page digest into just 4 natural classes (`BCTN_CORE`, `BCTN_PR`,
    `BCTC_AUDITED`, `EXCLUDE`) in 1 single call (~0.0002 USD/BCTN). Fails softly to deterministic.
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
            "Bạn là bộ lọc 3 trạng thái cho Báo cáo Thường niên (BCTN) doanh nghiệp HOSE.\n"
            "Phân loại các chương/khoảng trang sang đúng 1 trong 4 nhãn sau:\n"
            "- BCTN_CORE: KQKD, phân tích mảng/sản phẩm, quy mô vận hành, kế hoạch & CAPEX, quản trị rủi ro.\n"
            "- BCTN_PR: Thư Chủ tịch / Thông điệp Ban lãnh đạo / TGĐ (lời hứa PR, tách riêng để đối chiếu).\n"
            "- BCTC_AUDITED: Phần Báo cáo tài chính đã kiểm toán & Thuyết minh BCTC đính kèm cuối BCTN.\n"
            "- EXCLUDE: Báo cáo ESG / Phát triển bền vững / Từ thiện / An sinh xã hội, Giải thưởng, Ảnh bìa.\n"
            "Nguyên tắc an toàn: Nghi ngờ thì giữ `BCTN_CORE`, tuyệt đối KHÔNG `EXCLUDE` phần số liệu tài chính.\n"
            'Chỉ trả về JSON: {"sections":[{"slot":"BCTN_CORE","title":"...","printed_start":10,"printed_end":35}]}'
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
                        "max_tokens": 1500,
                        "response_format": {"type": "json_object"},
                        "messages": [
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                    },
                )
            if resp.status_code != 200:
                log.warning(
                    "LLM BCTN triage HTTP %s for %s; falling back to deterministic",
                    resp.status_code,
                    ticker,
                )
                return []
            content = resp.json()["choices"][0]["message"]["content"]
            return parse_llm_toc_json(content)
        except Exception as exc:  # noqa: BLE001
            log.warning("LLM BCTN triage failed for %s (%s); falling back", ticker, type(exc).__name__)
            return []


def parse_llm_toc_json(raw_json: str) -> list[TocSectionSpan]:
    """Parse and validate JSON returned by the 1-call BCTN triage router (accepts legacy aliases too)."""
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
        slot = normalize_natural_slot(str(item.get("slot") or ""))
        if slot is None:
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
            existing = page_map.get(pno)
            # Never let EXCLUDE overwrite BCTC_AUDITED or BCTN_CORE on a boundary page
            if existing is None or (existing.slot == "EXCLUDE" and sp.slot != "EXCLUDE") or (
                sp.slot == "BCTC_AUDITED" and existing.slot != "BCTC_AUDITED"
            ):
                page_map[pno] = sp
    return page_map


def _extract_digital_page_text(page: fitz.Page, *, left_sidebar_x: float) -> str:
    """Extract clean reading-order text from a digital PDF page, handling sidebars and A3 spreads."""
    rect = page.rect
    w, h = float(rect.width), float(rect.height)
    # Apply sidebar cutoff ONLY on matching widescreen pages (never chop a portrait page in hybrid PDFs!)
    effective_sidebar = left_sidebar_x if (w > h * 1.25 and 0.0 < left_sidebar_x < w * 0.30) else 0.0
    if w > h * 1.25 and effective_sidebar == 0.0:
        words = page.get_text("words")
        mid = w / 2.0
        crossing = sum(1 for wd in words if wd[0] < mid - 4 and wd[2] > mid + 4)
        if crossing <= 2 and len(words) > 10:
            left_txt = page.get_text("text", clip=fitz.Rect(0.0, 0.0, mid, h)) or ""
            right_txt = page.get_text("text", clip=fitz.Rect(mid, 0.0, w, h)) or ""
            return normalize_ctg_font_quirks(f"{left_txt.strip()}\n\n{right_txt.strip()}".strip())
    if effective_sidebar > 0.0:
        txt = page.get_text("text", clip=fitz.Rect(effective_sidebar, 0.0, w, h)) or ""
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


def _detect_trailing_scan_block(scan_pages: list[int], total_pages: int) -> list[int]:
    """Detect a contiguous trailing scanned BCTC attached to a digital BCTN (e.g. HPG p.90–141)."""
    if len(scan_pages) < 15 or total_pages < 30:
        return []
    ordered = sorted(set(scan_pages))
    if ordered[-1] < total_pages - 2:
        return []
    block = [ordered[-1]]
    for pno in reversed(ordered[:-1]):
        if block[-1] - pno <= 2:
            block.append(pno)
        else:
            break
    block.reverse()
    return block if len(block) >= 15 else []


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
    max_targeted_ocr_pages: int = 35,
    max_bctc_ocr_pages: int = 120,
    keep_all_bctc_notes: bool = True,
) -> ResearchInputBuildResult:
    """Build 4 Natural Document Classes (`BCTN_CORE`, `BCTN_PR`, `BCTC_AUDITED`, `AGM_TARGETS`/`QUARTER_NOTES`)
    with 0%-drop BCTC guarantee, running header removal, native Markdown heading normalization,
    and full `DocumentCoverageAudit`.
    """
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
        build_res.coverage_audit = DocumentCoverageAudit(
            total_pdf_pages=inspection.pages,
            digital_clean_pages=inspection.digital_clean_pages,
            scan_pages=inspection.scan_pages,
            bad_font_pages=inspection.bad_font_pages,
        )
        return build_res

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    page_texts: dict[int, str] = {}
    page_bold_headings: dict[int, set[str]] = {}
    ocr_pages_used = 0
    calibration = PageOffsetCalibration()
    section_spans: list[TocSectionSpan] = []
    spans_already_pdf = False
    localization_method = "NATIVE_4CLASS_DETERMINISTIC"
    trailing_scanned_bctc_pages: list[int] = []

    try:
        n_pages = len(doc)
        triage_by_page = {pt.page_no: pt for pt in inspection.page_triage}
        calibration = calibrate_printed_to_pdf_mapping(doc)

        if inspection.routed_branch == "C":
            if ocr_adapter is None:
                raise InputBuildError(
                    f"Scan PDF ({ticker}, {n_pages}p, {canonical_doc_type}) requires OcrAdapterProtocol; "
                    "silent MarkItDown fallback is forbidden."
                )

            if canonical_doc_type in ("BCTC", "NQ_DHDCD", "LATEST_QUARTER", "GIAI_TRINH"):
                # 0%-drop OCR for scanned BCTC / AGM / Quarter filings (up to max_bctc_ocr_pages=120)
                ocr_cap = max_bctc_ocr_pages if canonical_doc_type == "BCTC" else max(max_targeted_ocr_pages, 60)
                all_targets = list(range(1, min(n_pages, ocr_cap) + 1))
                try:
                    full_ocr = await ocr_adapter.ocr_pdf_pages(
                        _slice_pdf_pages(pdf_bytes, all_targets),
                        all_targets,
                        language="vi",
                    )
                except Exception as exc:
                    raise InputBuildError(f"Full {canonical_doc_type} OCR failed for {ticker}: {exc}") from exc
                ocr_pages_used += len(all_targets)
                page_texts.update(full_ocr)
                localization_method = f"FULL_{canonical_doc_type}_OCR"
            else:
                # 2-Pass TOC-First OCR for 100% scanned BCTNs (avoids OCRing 150 pages of ESG/photos)
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
                ocr_budget = (
                    max_bctc_ocr_pages
                    if any(sp.slot == "BCTC_AUDITED" for sp in section_spans)
                    else max_targeted_ocr_pages
                )
                remaining_targets = [p for p in target_pages if p not in page_texts][:ocr_budget]
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
            # Digital / Hybrid / BadFont: extract clean digital pages at 0đ, route BAD_FONT / scanned BCTC to OCR
            ocr_needed: list[int] = []
            for pno in range(1, n_pages + 1):
                t_info = triage_by_page.get(pno)
                if t_info and t_info.triage_class == "DIGITAL_CLEAN":
                    pg = doc[pno - 1]
                    page_texts[pno] = _extract_digital_page_text(
                        pg, left_sidebar_x=inspection.auto_sidebar_x
                    )
                    page_bold_headings[pno] = extract_page_bold_headings(
                        pg, left_sidebar_x=inspection.auto_sidebar_x
                    )
                else:
                    ocr_needed.append(pno)

            trailing_scanned_bctc_pages = _detect_trailing_scan_block(
                inspection.scan_page_numbers, n_pages
            )

            if ocr_needed:
                bad_font_set = set(inspection.bad_font_page_numbers)
                if bad_font_set:
                    must_ocr = [p for p in ocr_needed if p in bad_font_set]
                elif canonical_doc_type == "BCTC":
                    must_ocr = list(ocr_needed)
                elif trailing_scanned_bctc_pages:
                    must_ocr = list(trailing_scanned_bctc_pages)
                elif inspection.routed_branch in ("B", "D"):
                    must_ocr = list(ocr_needed)
                else:
                    must_ocr = []

                ocr_cap = (
                    max_bctc_ocr_pages
                    if (canonical_doc_type == "BCTC" or bool(trailing_scanned_bctc_pages))
                    else max_targeted_ocr_pages
                )
                must_ocr = must_ocr[:ocr_cap]
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

            # Layer 1A -> 1B -> 1C localization (BCTN / CAO_BACH only — BCTC/AGM/Quarter never need TOC routing!)
            if canonical_doc_type in ("BCTN", "CAO_BACH"):
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

    # Auto-strip repeating running headers/footers across all extracted pages (zero-hardcode)
    running_keys = detect_repeating_running_lines(page_texts)
    running_lines_stripped = 0
    cleaned_page_texts: dict[int, str] = {}
    for pno, raw_txt in page_texts.items():
        cleaned_txt, stripped_n = strip_running_lines(raw_txt, running_keys)
        cleaned_page_texts[pno] = cleaned_txt
        running_lines_stripped += stripped_n

    # Detect embedded Audited Financial Statements inside BCTN filings (e.g. FPT, CTG, PVT, VCI, HPG)
    embedded_bctc_start: int | None = None
    if canonical_doc_type == "BCTN":
        embedded_bctc_start = detect_embedded_bctc_start_page(cleaned_page_texts, n_pages_total)
        if embedded_bctc_start is None and trailing_scanned_bctc_pages:
            # Only set if those trailing scanned pages were actually OCR'd into cleaned_page_texts
            if any(p in cleaned_page_texts for p in trailing_scanned_bctc_pages):
                embedded_bctc_start = trailing_scanned_bctc_pages[0]

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
    tt200_policy_pages: list[int] = []
    all_normalized_headings: list[str] = []
    bctc_total_pages = 0
    bctc_kept_pages = 0
    # Stateful chapter mode for BCTN multi-page ESG / PR continuation
    sticky_bctn_mode: Literal["CORE", "PR", "ESG"] = "CORE"
    pr_streak = 0

    for pno in sorted(cleaned_page_texts):
        txt = (cleaned_page_texts[pno] or "").strip()
        if not txt:
            continue

        # ── Fast Path 1: Standalone BCTC or Embedded BCTC section inside BCTN (0%-Drop Guarantee) ──
        mapped_span = span_by_pdf_page.get(pno)
        in_bctc_zone = (
            canonical_doc_type == "BCTC"
            or (embedded_bctc_start is not None and pno >= embedded_bctc_start)
            or (mapped_span is not None and mapped_span.slot == "BCTC_AUDITED")
        )
        if in_bctc_zone:
            if len(txt) < 30:
                build_res.excluded_pages[pno] = "EMPTY_OR_COVER"
                continue
            bctc_total_pages += 1
            if _is_tt200_policy_only_page(txt):
                tt200_policy_pages.append(pno)
                if not keep_all_bctc_notes:
                    build_res.excluded_pages[pno] = "TT200_POLICY_BOILERPLATE"
                    continue
            norm_txt, page_h = normalize_financial_markdown_headings(
                txt,
                bold_lines=page_bold_headings.get(pno),
                is_bctc=True,
            )
            all_normalized_headings.extend(page_h)
            slot_chunks.setdefault("BCTC_AUDITED", []).append((pno, norm_txt))
            bctc_kept_pages += 1
            continue

        # ── Fast Path 2: AGM / Quarterly / Explanation filings ──
        if canonical_doc_type in ("NQ_DHDCD", "LATEST_QUARTER", "GIAI_TRINH"):
            bp_reason = classify_page_boilerplate(txt, doc_type=canonical_doc_type)
            if bp_reason is not None:
                build_res.excluded_pages[pno] = bp_reason
                continue
            target_slot = "AGM_TARGETS" if canonical_doc_type == "NQ_DHDCD" else "QUARTER_NOTES"
            norm_txt, page_h = normalize_financial_markdown_headings(
                txt,
                bold_lines=page_bold_headings.get(pno),
                is_bctc=(canonical_doc_type != "NQ_DHDCD"),
            )
            all_normalized_headings.extend(page_h)
            slot_chunks.setdefault(target_slot, []).append((pno, norm_txt))
            continue

        # ── Path 3: BCTN / CAO_BACH 3-Way Triage (`BCTN_CORE` / `BCTN_PR` / `EXCLUDE`) ──
        has_fin_shield = _page_has_financial_table_shield(txt)
        if mapped_span is not None:
            if mapped_span.slot == "EXCLUDE" and not has_fin_shield:
                build_res.excluded_pages[pno] = f"TOC_EXCLUDE:{mapped_span.title[:48]}"
                sticky_bctn_mode = "ESG"
                continue
            if mapped_span.slot == "BCTN_PR" and not has_fin_shield:
                build_res.excluded_pages[pno] = f"PR_LETTER:{mapped_span.title[:48]}"
                pr_chunks.append(f"<!-- PAGE {pno} -->\n{txt}")
                slot_chunks.setdefault("BCTN_PR", []).append((pno, txt))
                sticky_bctn_mode = "PR"
                continue
            # Otherwise BCTN_CORE
            norm_txt, page_h = normalize_financial_markdown_headings(
                txt,
                bold_lines=page_bold_headings.get(pno),
                is_bctc=False,
            )
            all_normalized_headings.extend(page_h)
            slot_chunks.setdefault("BCTN_CORE", []).append((pno, norm_txt))
            sticky_bctn_mode = "CORE"
            pr_streak = 0
            continue

        # Deterministic 3-Way Triage with sticky ESG/PR continuation & Financial-Table Shield
        bp_reason = classify_page_boilerplate(txt, doc_type=canonical_doc_type)
        if bp_reason == "EMPTY_OR_COVER":
            build_res.excluded_pages[pno] = bp_reason
            continue
        if bp_reason == "PR_LETTER":
            build_res.excluded_pages[pno] = bp_reason
            pr_chunks.append(f"<!-- PAGE {pno} -->\n{txt}")
            slot_chunks.setdefault("BCTN_PR", []).append((pno, txt))
            sticky_bctn_mode = "PR"
            pr_streak = 1
            continue
        if bp_reason in ("ESG_CHARITY", "FILLER_NON_CORE"):
            build_res.excluded_pages[pno] = bp_reason
            if bp_reason == "ESG_CHARITY":
                sticky_bctn_mode = "ESG"
            continue

        title_zone = _extract_heading_zone(txt, max_lines=4, max_chars=300)
        exits_non_core = has_fin_shield or bool(_CORE_BCTN_EXIT_RE.search(title_zone))
        if sticky_bctn_mode == "ESG" and not exits_non_core:
            build_res.excluded_pages[pno] = "ESG_CONTINUATION"
            continue
        if sticky_bctn_mode == "PR" and not exits_non_core and pr_streak < 5:
            build_res.excluded_pages[pno] = "PR_LETTER_CONTINUATION"
            pr_chunks.append(f"<!-- PAGE {pno} -->\n{txt}")
            slot_chunks.setdefault("BCTN_PR", []).append((pno, txt))
            pr_streak += 1
            continue

        sticky_bctn_mode = "CORE"
        pr_streak = 0
        norm_txt, page_h = normalize_financial_markdown_headings(
            txt,
            bold_lines=page_bold_headings.get(pno),
            is_bctc=False,
        )
        all_normalized_headings.extend(page_h)
        slot_chunks.setdefault("BCTN_CORE", []).append((pno, norm_txt))

    # Build final ordered SubDocumentBundles (4 Natural Classes)
    for slot in ALL_SUB_DOC_SLOTS:
        chunks = slot_chunks.get(slot)
        if not chunks:
            continue
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
                title=title or f"{ticker.upper()} {canonical_doc_type} FY{resolved_fy}",
            )
        )

    # Flag missing BCTC warning when a file tagged `BCTC` has zero financial statement signatures (e.g. mis-bundled AGM)
    audited_fs_missing_warning = False
    if canonical_doc_type == "BCTC":
        bctc_bundle = next((b for b in build_res.sub_documents if b.sub_doc_slot == "BCTC_AUDITED"), None)
        if bctc_bundle is None or not _FINANCIAL_SHIELD_RE.search(bctc_bundle.markdown_content):
            audited_fs_missing_warning = True
            build_res.missing_core_slots.append("BCTC_AUDITED")
    elif canonical_doc_type == "BCTN" and "BCTC_AUDITED" not in slot_chunks:
        build_res.missing_core_slots.append("BCTC_AUDITED")

    # Compute transparent Coverage Audit metrics
    dropped_counter: Counter[str] = Counter()
    pure_dropped_pages = 0
    for reason in build_res.excluded_pages.values():
        short_reason = reason.split(":")[0]
        dropped_counter[short_reason] += 1
        if not short_reason.startswith("PR_LETTER"):
            pure_dropped_pages += 1

    bctn_core_n = len(slot_chunks.get("BCTN_CORE", []))
    bctn_pr_n = len(slot_chunks.get("BCTN_PR", []))
    agm_q_n = len(slot_chunks.get("AGM_TARGETS", [])) + len(slot_chunks.get("QUARTER_NOTES", []))
    bctn_narrative_total = bctn_core_n + bctn_pr_n + pure_dropped_pages
    noise_red_pct = (
        round((pure_dropped_pages / bctn_narrative_total) * 100.0, 1)
        if (canonical_doc_type == "BCTN" and bctn_narrative_total > 0)
        else 0.0
    )
    bctc_cov_pct = (
        round((bctc_kept_pages / bctc_total_pages) * 100.0, 2)
        if bctc_total_pages > 0
        else 100.0
    )

    dedup_headings: list[str] = []
    seen_h: set[str] = set()
    for h in all_normalized_headings:
        hk = h.lower()
        if hk not in seen_h:
            seen_h.add(hk)
            dedup_headings.append(h)

    build_res.pr_narrative_markdown = "\n\n".join(pr_chunks)
    build_res.ocr_pages_used = ocr_pages_used
    build_res.localization_method = localization_method
    build_res.page_calibration = calibration
    build_res.section_spans = section_spans
    build_res.coverage_audit = DocumentCoverageAudit(
        total_pdf_pages=n_pages_total,
        digital_clean_pages=inspection.digital_clean_pages,
        scan_pages=inspection.scan_pages,
        bad_font_pages=inspection.bad_font_pages,
        bctn_core_pages=bctn_core_n,
        bctn_pr_pages=bctn_pr_n,
        bctc_audited_pages=bctc_kept_pages,
        agm_or_quarter_pages=agm_q_n,
        dropped_pages=pure_dropped_pages,
        noise_reduction_pct=noise_red_pct,
        embedded_bctc_start_page=embedded_bctc_start,
        bctc_total_pages=bctc_total_pages,
        bctc_kept_pages=bctc_kept_pages,
        bctc_notes_coverage_pct=bctc_cov_pct,
        tt200_policy_pages=tt200_policy_pages,
        running_lines_stripped_count=running_lines_stripped,
        normalized_headings_count=len(dedup_headings),
        sample_headings=dedup_headings[:25],
        dropped_by_reason=dict(dropped_counter),
        audited_fs_missing_warning=audited_fs_missing_warning,
    )
    return build_res
