"""HOSE ResearchInputBuilder: 11+2 Logical Sub-Documents (S01..S13) & Hybrid Page Builder.

Productionizes Stage 1 & Master Blueprint §2.2 / §4.1:
- Builds 11 Core (`S01..S11`, half_life=365d) + 2 Conditional (`S12` 180d, `S13` 90d) Logical Sub-Documents.
- Preserves original PDF page anchors (`<!-- PAGE N -->`) across Digital (PyMuPDF) and Scan/BadFont (OCR) lanes.
- Implements 2-Pass TOC-First OCR routing (Pass 1 TOC pages -> Pass 2 targeted slice via `OcrAdapterProtocol`,
  `language="vi"`, fail-closed on OCR failure — NEVER silent MarkItDown fallback on scan/bad-font pages).
- Excludes boilerplate (PR Chair/CEO letters, ESG/charity, generic TT200 accounting policies unless policy change,
  procedural AGM minutes/ballot sheets), while optionally surfacing PR narrative text for DiscrepancyDetector.
- Enforces strict PIT (`published_date` / `valid_from`) propagation; fails closed if missing.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal, Protocol

import fitz

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
            r"hoạt\s+động\s+(?:từ\s+thiện|cộng\s+đồng|an\s+sinh)|phát\s+thải\s+khí\s+nhà\s+kính|esg)",
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
            r"rủi\s+ro\s+tỷ\s+giá|an\s+toàn\s+vốn|car\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "S11_CAPITAL_POST_EVT",
        re.compile(
            r"(?:vốn\s+chủ\s+sở\s+hữu|tăng\s+vốn|cổ\s+tức|esop\b|"
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
            r"(?:quản\s+trị\s+rủi\s+ro|rủi\s+ro\s+kinh\s+doanh|swot|điểm\s+mạnh|điểm\s+yếu|cơ\s+hội|thách\s+thức)",
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


def classify_page_boilerplate(text: str, *, doc_type: str) -> str | None:
    """Return boilerplate exclusion reason if the page should be dropped from MOAT slots."""
    low = (text or "").lower()
    if len(low.strip()) < 30:
        return "EMPTY_OR_COVER"

    # Keep policy changes even inside accounting policy pages
    has_policy_change = bool(
        re.search(r"thay\s+đổi\s+chính\s+sách\s+kế\s+toán", low, re.IGNORECASE)
    )
    for reason, pat in _BOILERPLATE_RULES:
        if reason == "TT200_POLICY_BOILERPLATE" and has_policy_change:
            continue
        if reason == "AGM_PROCEDURAL" and doc_type != "NQ_DHDCD":
            continue
        if pat.search(low):
            # Only drop as boilerplate if it does NOT also contain core financial tables/metrics
            if reason == "PR_LETTER" and "bảng cân đối" in low:
                continue
            return reason
    return None


def classify_text_to_slot(
    text: str,
    *,
    doc_type: str,
) -> str | None:
    """Deterministically route a page's text to one of S01..S13."""
    canonical = normalize_doc_type(doc_type)
    if canonical == "NQ_DHDCD":
        return "S12_AGM_TARGETS"
    if canonical in ("LATEST_QUARTER", "GIAI_TRINH"):
        return "S13_QUARTER_NOTES"

    for slot, pat in _SLOT_MATCHERS:
        if pat.search(text or ""):
            return slot

    if canonical == "BCTN":
        return "S01_MDNA_SEGMENT"
    if canonical == "BCTC":
        return "S07_REV_COGS_DETAIL"
    if canonical == "CAO_BACH":
        return "S11_CAPITAL_POST_EVT"
    return "S01_MDNA_SEGMENT"


def parse_toc_target_pages(toc_text: str, *, max_pages: int) -> list[int]:
    """Parse target page numbers from TOC text for 2-Pass TOC-First OCR."""
    targets: set[int] = set()
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
            pno = int(m_page.group(1))
            if 1 <= pno <= max_pages:
                targets.update(range(pno, min(max_pages, pno + 2) + 1))
    return sorted(targets)


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
    toc_scan_limit: int = 5,
    max_targeted_ocr_pages: int = 25,
) -> ResearchInputBuildResult:
    """Build S01..S13 Logical Sub-Documents with `<!-- PAGE N -->` anchors and strict PIT provenance."""
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
    try:
        n_pages = len(doc)
        triage_by_page = {pt.page_no: pt for pt in inspection.page_triage}

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
            target_pages = parse_toc_target_pages(toc_joined, max_pages=n_pages)
            if not target_pages:
                # Fallback to bounded substantive window if TOC didn't expose page numbers
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
                # For Branch A/B with only minor scan pages (e.g. cover/signatures), OCR only if BAD_FONT
                # or if digital clean pages didn't already cover all text; fail-closed if BAD_FONT has no OCR.
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
    finally:
        doc.close()

    slot_chunks: dict[str, list[tuple[int, str]]] = {}
    pr_chunks: list[str] = []

    for pno in sorted(page_texts):
        txt = (page_texts[pno] or "").strip()
        if not txt:
            continue
        bp_reason = classify_page_boilerplate(txt, doc_type=canonical_doc_type)
        if bp_reason is not None:
            build_res.excluded_pages[pno] = bp_reason
            if bp_reason == "PR_LETTER":
                pr_chunks.append(f"<!-- PAGE {pno} -->\n{txt}")
            continue
        slot = classify_text_to_slot(txt, doc_type=canonical_doc_type)
        if slot is None:
            continue
        slot_chunks.setdefault(slot, []).append((pno, txt))

    for slot in ALL_SUB_DOC_SLOTS:
        chunks = slot_chunks.get(slot)
        if not chunks:
            continue
        page_nums = [p for p, _ in chunks]
        md_parts = [f"<!-- PAGE {p} -->\n{body}" for p, body in chunks]
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
    return build_res
