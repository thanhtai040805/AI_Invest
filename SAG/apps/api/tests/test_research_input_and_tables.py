"""Unit tests for Worker Track 2: HOSE Input Layer & Dual-Representation Financial Table Engine."""

from __future__ import annotations

import io
import zipfile
from datetime import UTC, date, datetime

import fitz
import pytest

from sag_api.research import (
    ALL_SUB_DOC_SLOTS,
    ExtractedTableRecord,
    FinancialAnomalyContext,
    InMemoryTableRepository,
    InputBuildError,
    MissingPublishedDateError,
    ResearchCandidateDocument,
    ZipExtractionError,
    build_matrix_from_html_table,
    build_research_sub_documents,
    build_row_linearized_cards,
    classify_document_category,
    compute_vn_readability_score,
    detect_repeating_left_sidebar_x,
    detect_table_locale,
    detect_unit_and_currency,
    extract_numeric_table_from_region,
    extract_table_with_lattice_fallback,
    fetch_document_with_fallback,
    inspect_pdf_bytes,
    join_wrapped_prefix_and_label,
    normalize_ctg_font_quirks,
    parse_fiscal_year_from_meta,
    parse_vn_number,
    resolve_effective_published_at,
    run_arithmetic_sanity_gate,
    run_hierarchical_identities_sanity_gate,
    safe_extract_pdf_from_zip,
    select_research_documents,
    stitch_cross_page_tables,
    table_safe_jaccard_should_merge,
    to_hydrated_table,
    unmerge_html_table,
    vn_dot_bm25_tokenize,
)


def _make_pdf_bytes(
    pages_text: list[str],
    *,
    width: float = 595.0,
    height: float = 842.0,
    utf8_html: bool = False,
) -> bytes:
    doc = fitz.open()
    for text in pages_text:
        page = doc.new_page(width=width, height=height)
        if text:
            if utf8_html:
                page.insert_htmlbox(
                    fitz.Rect(50, 50, width - 50, height - 50),
                    f"<p>{text}</p>",
                )
            else:
                page.insert_text((72, 72), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


def _make_widescreen_sidebar_pdf(n_pages: int = 10) -> bytes:
    doc = fitz.open()
    for idx in range(n_pages):
        page = doc.new_page(width=1058.0, height=595.0)
        # Repeating left sidebar navigation (x0=24..160)
        page.insert_text((24, 100), "MUC LUC CHINH", fontsize=10)
        page.insert_text((24, 130), "TONG QUAN CONG TY", fontsize=10)
        page.insert_text((24, 160), "BAO CAO TAI CHINH", fontsize=10)
        # Multiple body lines crossing mid=529 so it is detected as single widescreen (not A3 spread)
        page.insert_text((470, 75), "BAO_CAO_KET_QUA_KINH_DOANH_HOP_NHAT_2025", fontsize=11)
        for line_i in range(4):
            y = 105 + line_i * 25
            page.insert_text(
                (260 + line_i * 11, y),
                (
                    f"Trang noi dung {idx + 1}.{line_i}: Doanh thu thuan va loi nhuan sau thue "
                    "cua tap doan trong nam tai chinh 2025 tang truong ben vung theo ke hoach."
                ),
                fontsize=11,
            )
    data = doc.tobytes()
    doc.close()
    return data


# ── Input Selector & PIT Trap Tests ──────────────────────────────────────────


def test_document_category_classification_3core_2cond_4excl():
    assert classify_document_category("BCTC", statement_scope="CONSOLIDATED", has_consolidated=True)[0] == "CORE"
    assert classify_document_category("BCTN")[0] == "CORE"
    assert classify_document_category("NQ_DHDCD")[0] == "CORE"
    assert classify_document_category("CAO_BACH")[0] == "CONDITIONAL"
    assert classify_document_category("GIẢI_TRÌNH")[0] == "CONDITIONAL"
    assert classify_document_category("LATEST_QUARTER")[0] == "CONDITIONAL"
    assert classify_document_category("TIN_TUC")[0] == "EXCLUDED"
    assert classify_document_category("GIAO_DICH_NOI_BO")[0] == "EXCLUDED"
    assert classify_document_category("CBTT_DINH_KY_NGAN")[0] == "EXCLUDED"
    assert classify_document_category("BAO_CAO_PHAN_TICH_BEN_NGOAI")[0] == "EXCLUDED"
    # Parent SEPARATE excluded when CONSOLIDATED exists; retained for standalone companies
    assert classify_document_category("BCTC", statement_scope="SEPARATE", has_consolidated=True)[0] == "EXCLUDED"
    assert classify_document_category("BCTC", statement_scope="SEPARATE", has_consolidated=False)[0] == "CORE"


def test_published_date_trap_parser_and_fail_closed():
    # Priority: announcement -> signing -> filing -> published
    res = resolve_effective_published_at(
        published_date="2026-04-20",
        filing_date="2026-04-18",
        signing_date="2026-04-15",
        announcement_date="2026-04-14",
        title="Báo cáo thường niên năm 2025",
    )
    assert res.effective_published_at == date(2026, 4, 14)
    assert res.date_source == "announcement_date"
    assert res.parsed_fiscal_year == 2025
    assert res.is_trapped is False

    # Trap: 2016 report stamped 2026
    trapped = resolve_effective_published_at(
        published_date="2026-03-01",
        title="Báo cáo tài chính hợp nhất năm 2016 (đã kiểm toán)",
        url="https://example.com/2016/VCI_16CN.pdf",
    )
    assert trapped.is_trapped is True
    assert parse_fiscal_year_from_meta("BCTC 2016", "https://example.com/2016/a.pdf") == 2016

    # Trap: published before period_end
    pre_end = resolve_effective_published_at(
        published_date="2025-12-01",
        title="BCTC năm 2025",
        period_end="2025-12-31",
    )
    assert pre_end.is_trapped is True

    # Fail-closed if all dates missing
    with pytest.raises(MissingPublishedDateError):
        resolve_effective_published_at(title="BCTC năm 2025")


@pytest.mark.asyncio
async def test_multi_source_404_fallback():
    class _FakeFetcher:
        def __init__(self, responses: dict[str, tuple[int, bytes]]) -> None:
            self._responses = responses

        async def fetch_bytes(self, url: str) -> tuple[int, bytes]:
            return self._responses[url]

    fetcher = _FakeFetcher(
        {
            "https://cafef.vn/404.pdf": (404, b""),
            "https://vnstock.vn/ok.pdf": (200, b"%PDF-1.4\n..."),
        }
    )
    ok_res = await fetch_document_with_fallback(
        ["https://cafef.vn/404.pdf", "https://vnstock.vn/ok.pdf"],
        fetcher=fetcher,
    )
    assert ok_res.ok is True
    assert ok_res.resolved_url == "https://vnstock.vn/ok.pdf"
    assert len(ok_res.tried_urls) == 2

    fail_res = await fetch_document_with_fallback(
        ["https://cafef.vn/404.pdf"],
        fetcher=fetcher,
    )
    assert fail_res.ok is False
    assert fail_res.error == "ALL_CANDIDATE_URLS_FAILED"


@pytest.mark.asyncio
async def test_select_research_documents_pit_and_bctn_bctc_dedup():
    clean_bctn_pages = [
        (
            "Bao cao thuong nien nam 2025. Doanh thu thuan va loi nhuan sau thue tang truong. "
            "Ke hoach kinh doanh nam 2026 va Thuyet minh bao cao tai chinh B09-DN."
        )
        * 2
    ] * 16
    # Use Vietnamese text with proper diacritics via PyMuPDF HTML/text or enough VN words
    # Note: default Helvetica strips some diacritics; test dedup + PIT with inspect_payloads=False
    bctn_pdf = _make_pdf_bytes(clean_bctn_pages)
    bctc_pdf = _make_pdf_bytes(["BCTC kiem toan 2025"] * 16)

    candidates = [
        ResearchCandidateDocument(
            doc_id="doc_bctn_2025",
            ticker="PVT",
            doc_type="annual_report",
            title="Báo cáo thường niên năm 2025",
            published_date="2026-04-13",
            raw_bytes=bctn_pdf,
        ),
        ResearchCandidateDocument(
            doc_id="doc_bctc_2025",
            ticker="PVT",
            doc_type="financial_statement",
            title="BCTC hợp nhất kiểm toán năm 2025",
            statement_scope="CONSOLIDATED",
            published_date="2026-03-30",
            raw_bytes=bctc_pdf,
        ),
        ResearchCandidateDocument(
            doc_id="doc_future_2026",
            ticker="PVT",
            doc_type="latest_quarter_notes",
            title="Thuyết minh BCTC Quý 3/2026",
            published_date="2026-10-30",
            trigger_reason="ANOMALY",
            raw_bytes=bctc_pdf,
        ),
        ResearchCandidateDocument(
            doc_id="doc_news",
            ticker="PVT",
            doc_type="TIN_TUC",
            title="Tin tuc co phieu",
            published_date="2026-05-01",
        ),
        ResearchCandidateDocument(
            doc_id="doc_no_date",
            ticker="PVT",
            doc_type="BCTC",
            title="BCTC thieu ngay cong bo",
            published_date=None,
        ),
    ]

    # PIT cutoff before 2026-10-30 -> future Q3 doc MUST be rejected!
    sel = await select_research_documents(
        "PVT",
        candidates,
        as_of_date=date(2026, 6, 30),
        anomaly_context=FinancialAnomalyContext(yoy_revenue_change_pct=30.0),
        inspect_payloads=False,
    )
    selected_ids = {s.candidate.doc_id for s in sel.selected}
    rejected_reasons = {r.doc_id: r.reason for r in sel.rejected}
    assert "doc_bctn_2025" in selected_ids
    assert "doc_bctc_2025" in selected_ids
    assert "doc_future_2026" not in selected_ids
    assert rejected_reasons["doc_future_2026"].startswith("FUTURE_PIT_EXCLUDED")
    assert rejected_reasons["doc_news"].startswith("EXCLUDED_DOC_TYPE")
    assert rejected_reasons["doc_no_date"].startswith("MISSING_PIT_DATE")


@pytest.mark.asyncio
async def test_bctn_embedded_notes_deduplicates_standalone_bctc():
    vn_bctn_page = (
        "Báo cáo thường niên năm 2025. Doanh thu thuần và lợi nhuận sau thuế của công ty "
        "tăng trưởng ổn định. Kế hoạch kinh doanh năm 2026 và Thuyết minh báo cáo tài chính B09-DN."
    )
    bctn_pdf = _make_pdf_bytes([vn_bctn_page] * 16, utf8_html=True)
    bctc_pdf = _make_pdf_bytes([vn_bctn_page] * 16, utf8_html=True)

    sel = await select_research_documents(
        "FPT",
        [
            ResearchCandidateDocument(
                doc_id="fpt_bctn_2025",
                ticker="FPT",
                doc_type="BCTN",
                title="Báo cáo thường niên năm 2025",
                published_date="2026-04-10",
                raw_bytes=bctn_pdf,
            ),
            ResearchCandidateDocument(
                doc_id="fpt_bctc_2025",
                ticker="FPT",
                doc_type="BCTC",
                title="Báo cáo tài chính hợp nhất năm 2025",
                statement_scope="CONSOLIDATED",
                published_date="2026-03-30",
                raw_bytes=bctc_pdf,
            ),
        ],
        as_of_date=date(2026, 6, 30),
        inspect_payloads=True,
    )
    assert sel.bctn_deduplicated_bctc is True
    assert [s.candidate.doc_id for s in sel.selected] == ["fpt_bctn_2025"]
    assert sel.selected[0].inspection is not None
    assert sel.selected[0].inspection.routed_branch == "A"
    assert any(r.reason.startswith("DEDUPLICATED_BY_BCTN") for r in sel.rejected)


@pytest.mark.asyncio
async def test_bad_font_pdf_fails_closed_without_ocr():
    corrupt_page = "B\ufffd\ufffdo c\ufffdo (cid:22)(cid:23)(cid:24)(cid:25) " * 20
    bad_font_pdf = _make_pdf_bytes([corrupt_page] * 16, utf8_html=True)
    insp = inspect_pdf_bytes(bad_font_pdf, source_url="bad.pdf")
    assert insp.doc_class == "BAD_FONT"
    assert insp.routed_branch == "D"

    with pytest.raises(InputBuildError):
        await build_research_sub_documents(
            ticker="HPG",
            pdf_bytes=bad_font_pdf,
            doc_type="BCTC",
            published_date=date(2026, 4, 15),
            ocr_adapter=None,
        )


# ── Input Inspector, ZIP Security, NOTICE_ONLY & BAD_FONT Tests ──────────────


def test_safe_zip_extraction_and_zip_slip_guard():
    # Normal ZIP containing ballot + resolution -> picks resolution PDF
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("01_bien_ban_kiem_phieu.pdf", b"%PDF-1.4\n" + b"0" * 5000)
        zf.writestr("02_nghi_quyet_dhdcd.pdf", b"%PDF-1.4\n" + b"1" * 1000)
    pdf_bytes, chosen, entries = safe_extract_pdf_from_zip(buf.getvalue())
    assert chosen == "02_nghi_quyet_dhdcd.pdf"
    assert pdf_bytes.startswith(b"%PDF-1.4")
    assert len(entries) == 2

    # Zip-Slip traversal -> MUST fail closed
    slip_buf = io.BytesIO()
    with zipfile.ZipFile(slip_buf, "w") as zf:
        zf.writestr("../../evil.pdf", b"%PDF-1.4\n")
    with pytest.raises(ZipExtractionError):
        safe_extract_pdf_from_zip(slip_buf.getvalue())

    # Size guard (Zip-bomb) -> MUST fail closed
    with pytest.raises(ZipExtractionError):
        safe_extract_pdf_from_zip(buf.getvalue(), max_uncompressed_bytes=100)


def test_inspector_notice_only_bad_font_and_sidebar():
    notice_pdf = _make_pdf_bytes(
        ["Thong bao dang tai BCTN tai https://example.com/bctn_2025.pdf"] * 2
    )
    insp_notice = inspect_pdf_bytes(notice_pdf, source_url="notice.pdf")
    assert insp_notice.doc_class == "NOTICE_ONLY"
    assert insp_notice.routed_branch == "D"
    assert "https://example.com/bctn_2025.pdf" in insp_notice.embedded_urls

    # Direct readability test for BAD_FONT & clean VN
    clean_vn = (
        "Báo cáo thường niên năm 2025. Doanh thu thuần và lợi nhuận sau thuế "
        "của công ty tăng trưởng mạnh theo kế hoạch cổ đông."
    )
    sc_ok, _ = compute_vn_readability_score(clean_vn)
    assert sc_ok >= 0.80

    corrupt = "B\ufffd\ufffdo c\ufffdo (cid:22)(cid:23)(cid:24)(cid:25) \x01\x02\x03 " * 12
    sc_bad, _ = compute_vn_readability_score(corrupt)
    assert sc_bad < 0.72

    # Auto-sidebar detector on widescreen PDF
    sidebar_pdf = _make_widescreen_sidebar_pdf(10)
    sx = detect_repeating_left_sidebar_x(sidebar_pdf)
    assert sx > 50.0


def test_ctg_font_quirk_repair():
    assert normalize_ctg_font_quirks("Nhóm 1 - Nợ ‚ủ tiêu chuẩn") == "Nhóm 1 - Nợ đủ tiêu chuẩn"
    assert normalize_ctg_font_quirks("II Lãi thuần từ hoạt Èộng dịch vụ") == "II Lãi thuần từ hoạt động dịch vụ"
    assert normalize_ctg_font_quirks("Người ¢ược ủy quyền") == "Người được ủy quyền"
    assert normalize_ctg_font_quirks("chênh lệch tỷ giá hối ½oái") == "chênh lệch tỷ giá hối đoái"


# ── Input Builder (11+2 Slots, <!-- PAGE N -->, Fail-Closed OCR) ──────────────


@pytest.mark.asyncio
async def test_input_builder_slots_page_anchors_and_fail_closed_ocr():
    assert ALL_SUB_DOC_SLOTS == (
        "BCTN_CORE",
        "BCTN_PR",
        "BCTC_AUDITED",
        "AGM_TARGETS",
        "QUARTER_NOTES",
    )

    # Create a 16-page scan PDF (empty text layer) -> Branch C
    scan_pdf = _make_pdf_bytes([""] * 16)

    # Fail-closed when ocr_adapter is None (no silent MarkItDown fallback!)
    with pytest.raises(InputBuildError):
        await build_research_sub_documents(
            ticker="DCM",
            pdf_bytes=scan_pdf,
            doc_type="BCTN",
            published_date=date(2026, 4, 15),
            ocr_adapter=None,
        )

    # With fake OCR adapter -> 2-pass TOC-First OCR, preserves <!-- PAGE N --> and PIT dates
    class _FakeOcrAdapter:
        def __init__(self) -> None:
            self.calls: list[tuple[list[int], str]] = []

        async def ocr_pdf_pages(
            self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"
        ) -> dict[int, str]:
            self.calls.append((list(page_numbers), language))
            out: dict[int, str] = {}
            for p in page_numbers:
                if p == 2:
                    out[p] = "Mục lục\nĐánh giá kết quả kinh doanh ...... 8\nKế hoạch kinh doanh 2026 ...... 12"
                elif p in (8, 9, 10):
                    out[p] = "Đánh giá kết quả hoạt động kinh doanh theo mảng sản phẩm."
                elif p in (12, 13):
                    out[p] = "Kế hoạch kinh doanh năm 2026 và ngân sách CAPEX."
                elif p == 3:
                    out[p] = "Thư Chủ tịch HĐQT: năm 2025 bứt phá vượt bậc."
                else:
                    out[p] = "Thông tin chung trang bìa."
            return out

    ocr = _FakeOcrAdapter()
    built = await build_research_sub_documents(
        ticker="DCM",
        pdf_bytes=scan_pdf,
        doc_type="BCTN",
        published_date=date(2026, 4, 15),
        title="Báo cáo thường niên năm 2025",
        ocr_adapter=ocr,
    )
    assert built.routed_branch == "C"
    assert all(lang == "vi" for _, lang in ocr.calls)
    assert len(ocr.calls) == 2
    slots_built = {sd.sub_doc_slot: sd for sd in built.sub_documents}
    assert "BCTN_CORE" in slots_built
    assert "BCTN_PR" in slots_built
    assert "<!-- PAGE 8 -->" in slots_built["BCTN_CORE"].markdown_content
    assert "<!-- PAGE 12 -->" in slots_built["BCTN_CORE"].markdown_content
    assert slots_built["BCTN_CORE"].published_date == date(2026, 4, 15)
    assert slots_built["BCTN_CORE"].valid_from == datetime(2026, 4, 15, tzinfo=UTC)
    assert "<!-- PAGE 3 -->" in built.pr_narrative_markdown
    assert built.excluded_pages[3] == "PR_LETTER"


# ── Dual-Representation Financial Table Engine Tests ──────────────────────────


def test_vn_number_locale_and_unit_isolation():
    assert parse_vn_number("(56.500)", "VN") == -56500.0
    assert parse_vn_number("16.012.813.858.007", "VN") == 16012813858007.0
    assert parse_vn_number("15,29%", "VN") == 15.29
    assert parse_vn_number("0,435%", "VN") == pytest.approx(0.435)
    assert parse_vn_number("+12,5%", "VN") == pytest.approx(12.5)
    assert parse_vn_number("1,234,567.89", "US") == 1234567.89
    assert parse_vn_number("-", "VN") is None
    assert parse_vn_number("0", "VN") == 0.0

    assert detect_table_locale(["9.857.384.636.170", "589.704.260.295", "15,29"]) == "VN"
    assert detect_table_locale(["9,857,384.50", "589,704.25", "15.29"]) == "US"

    assert detect_unit_and_currency("Đơn vị tính: Tỷ đồng") == ("VND", 1e9, "tỷ đồng")
    assert detect_unit_and_currency("31.12.2025\nTriệu Èồng") == ("VND", 1e6, "triệu đồng")
    assert detect_unit_and_currency("Đơn vị tính: triệu USD") == ("USD", 1e6, "triệu USD")


def test_html_unmerge_and_matrix_builder():
    html = """
    <table>
      <tr><td rowspan="2">Chỉ<br/>tiêu</td><td colspan="2">Doanh thu thuần</td></tr>
      <tr><td rowspan="bad">2025</td><td>2024</td></tr>
      <tr><td>Vận tải</td><td>9.857</td><td>9.042</td></tr>
      <tr><td>Thương mại</td><td>5.156</td><td>1.705</td></tr>
      <tr><td>Tổng cộng</td><td>15.013</td><td>10.747</td></tr>
    </table>
    """
    headers, body = unmerge_html_table(html)
    assert headers == ["Chỉ tiêu", "Doanh thu thuần :: 2025", "Doanh thu thuần :: 2024"]
    assert len(body) == 3

    mat = build_matrix_from_html_table(html, context_text="Đơn vị tính: Tỷ đồng")
    assert mat["currency"] == "VND"
    assert mat["unit_multiplier"] == 1e9
    assert mat["sanity_gate"]["verification_status"] == "VERIFIED"


def test_coordinate_reconstructor_and_lattice_fallback():
    class _BrokenLatticePage:
        bbox = (0.0, 0.0, 595.0, 842.0)
        width = 595.0

        def extract_text(self):
            return "Đơn vị: Triệu đồng"

        def extract_tables(self):
            # Lattice drops the right-most column (2024 is empty!)
            return [
                [
                    ["Chỉ tiêu", "2025", "2024"],
                    ["A", "600", ""],
                    ["B", "400", ""],
                    ["Tổng cộng", "1.000", ""],
                ]
            ]

        def extract_words(self, **_kwargs):
            return [
                {"text": "Đơn", "x0": 400.0, "x1": 420.0, "top": 100.0, "bottom": 108.0},
                {"text": "vị:", "x0": 422.0, "x1": 435.0, "top": 100.0, "bottom": 108.0},
                {"text": "Triệu", "x0": 438.0, "x1": 465.0, "top": 100.0, "bottom": 108.0},
                {"text": "đồng", "x0": 467.0, "x1": 495.0, "top": 100.0, "bottom": 108.0},
                {"text": "Vận", "x0": 73.0, "x1": 95.0, "top": 140.0, "bottom": 148.0},
                {"text": "tải", "x0": 98.0, "x1": 115.0, "top": 140.0, "bottom": 148.0},
                {"text": "600", "x0": 430.0, "x1": 450.0, "top": 140.0, "bottom": 148.0},
                {"text": "500", "x0": 500.0, "x1": 520.0, "top": 140.0, "bottom": 148.0},
                {"text": "Thương", "x0": 73.0, "x1": 110.0, "top": 160.0, "bottom": 168.0},
                {"text": "mại", "x0": 113.0, "x1": 135.0, "top": 160.0, "bottom": 168.0},
                {"text": "400", "x0": 430.0, "x1": 450.0, "top": 160.0, "bottom": 168.0},
                {"text": "300", "x0": 500.0, "x1": 520.0, "top": 160.0, "bottom": 168.0},
                {"text": "Tổng", "x0": 73.0, "x1": 100.0, "top": 180.0, "bottom": 188.0},
                {"text": "cộng", "x0": 103.0, "x1": 130.0, "top": 180.0, "bottom": 188.0},
                {"text": "1.000", "x0": 422.0, "x1": 450.0, "top": 180.0, "bottom": 188.0},
                {"text": "800", "x0": 500.0, "x1": 520.0, "top": 180.0, "bottom": 188.0},
            ]

    tbl = extract_table_with_lattice_fallback(
        _BrokenLatticePage(), expected_col_headers=["2025", "2024"]
    )
    assert tbl["extraction_method"] == "coordinate_reconstructor"
    assert tbl["sanity_gate"]["verification_status"] == "VERIFIED"
    assert tbl["needs_mineru_fallback"] is False
    assert tbl["rows"][2][2]["numeric_value"] == 800.0

    # Wrapped label join sanity check
    joined = join_wrapped_prefix_and_label(
        [("Lợi nhuận thuần từ hoạt động kinh doanh trước chi phí dự", 573.6, 102.0)],
        line_top=578.0,
        label_str="IX 29",
        label_x0=73.6,
    )
    assert joined == "IX Lợi nhuận thuần từ hoạt động kinh doanh trước chi phí dự 29"

    # Extract region directly
    direct = extract_numeric_table_from_region(_BrokenLatticePage(), expected_col_headers=["2025", "2024"])
    assert len(direct["rows"]) == 3


def test_stitch_sanity_gate_hierarchical_and_dual_representation():
    t1 = {
        "virtual_seq": 0,
        "sub_doc_slot": "S07",
        "page_from": 10,
        "page_to": 10,
        "headers": ["Chỉ tiêu", "2025", "2024"],
        "context_header": "Bảng 1",
        "rows": [
            [
                {"raw_text": "Mảng A", "numeric_value": None},
                {"raw_text": "600", "numeric_value": 600.0},
                {"raw_text": "500", "numeric_value": 500.0},
            ],
        ],
    }
    t2 = {
        "virtual_seq": 1,
        "sub_doc_slot": "S07",
        "page_from": 11,
        "page_to": 11,
        "headers": ["Chỉ tiêu", "2025", "2024"],
        "context_header": "Bảng 1 (Tiếp theo)",
        "rows": [
            [
                {"raw_text": "Chỉ tiêu", "numeric_value": None},
                {"raw_text": "2025", "numeric_value": None},
                {"raw_text": "2024", "numeric_value": None},
            ],
            [
                {"raw_text": "Mảng B", "numeric_value": None},
                {"raw_text": "400", "numeric_value": 400.0},
                {"raw_text": "300", "numeric_value": 300.0},
            ],
            [
                {"raw_text": "Tổng cộng", "numeric_value": None},
                {"raw_text": "1.000", "numeric_value": 1000.0},
                {"raw_text": "800", "numeric_value": 800.0},
            ],
        ],
    }
    stitched = stitch_cross_page_tables([t1, t2])
    assert len(stitched) == 1
    assert stitched[0]["page_to"] == 11
    gate = run_arithmetic_sanity_gate(stitched[0]["headers"], stitched[0]["rows"])
    assert gate["verification_status"] == "VERIFIED"

    # Hierarchical gate test
    h_gate = run_hierarchical_identities_sanity_gate(
        stitched[0]["headers"],
        stitched[0]["rows"],
        [("Total = A + B", 2, [0, 1])],
    )
    assert h_gate["verification_status"] == "VERIFIED"

    # Fail-closed TABLE_UNVERIFIED when mismatch > 0.5%
    bad_rows = [
        [
            {"raw_text": "A", "numeric_value": None},
            {"raw_text": "600", "numeric_value": 600.0},
            {"raw_text": "500", "numeric_value": 500.0},
        ],
        [
            {"raw_text": "Tổng cộng", "numeric_value": None},
            {"raw_text": "1.000", "numeric_value": 1000.0},
            {"raw_text": "800", "numeric_value": 800.0},
        ],
    ]
    bad_gate = run_arithmetic_sanity_gate(["Chỉ tiêu", "2025", "2024"], bad_rows)
    assert bad_gate["verification_status"] == "TABLE_UNVERIFIED"


@pytest.mark.asyncio
async def test_dual_representation_cards_hydration_and_pit_repository():
    extracted = {
        "headers": ["Chỉ tiêu", "2025", "2024"],
        "rows": [
            [
                {"raw_text": "Doanh thu thuần", "numeric_value": None},
                {"raw_text": "1.000", "numeric_value": 1000.0},
                {"raw_text": "800", "numeric_value": 800.0},
            ],
            [
                {"raw_text": "Tổng cộng", "numeric_value": None},
                {"raw_text": "1.000", "numeric_value": 1000.0},
                {"raw_text": "800", "numeric_value": 800.0},
            ],
        ],
        "currency": "VND",
        "unit_raw": "tỷ đồng",
        "unit_multiplier": 1e9,
    }
    rec = ExtractedTableRecord.from_extraction(
        project_id="PVT",
        document_version_id="ver-1",
        sub_doc_slot="S07_REV_COGS_DETAIL",
        fiscal_year=2025,
        table_title="Chi tiết doanh thu",
        page_from=168,
        page_to=168,
        extracted=extracted,
        valid_from=datetime(2026, 4, 15, tzinfo=UTC),
        footnotes_text="Số liệu kiểm toán.",
    )
    assert rec.verification_status == "VERIFIED"

    cards = build_row_linearized_cards(rec.to_dict())
    assert len(cards) == 2
    assert cards[0]["table_id"] == rec.table_id
    assert "Đơn vị: tỷ đồng" in cards[0]["card_text"]

    hydrated = to_hydrated_table(rec.to_dict())
    assert hydrated["headers"] == [["Chỉ tiêu", "2025", "2024"]]
    assert hydrated["rows"][0] == {"label": "Doanh thu thuần", "values": [1000.0, 800.0]}

    repo = InMemoryTableRepository()
    await repo.upsert_table(rec)

    # Strict PIT: querying before 2026-04-15 MUST return None (0% future leak!)
    assert await repo.get_hydrated_table(rec.table_id, as_of_ts=datetime(2025, 6, 30, tzinfo=UTC)) is None
    assert await repo.get_hydrated_table(rec.table_id, as_of_ts=datetime(2026, 11, 20, tzinfo=UTC)) is not None


def test_vn_dot_bm25_and_table_safe_mmr():
    toks = vn_dot_bm25_tokenize("Doanh thu 16.012.813.858.007 VND biên 15.29% tại Mục 1.2.3")
    assert "16012813858007" in toks
    assert "15.29%" in toks
    assert "1.2.3" in toks

    t25 = {"text": "Doanh thu thuần: 70.112.825", "fiscal_year": 2025, "sub_doc_slot": "S06", "table_id": "t1"}
    t24 = {"text": "Doanh thu thuần: 62.849.000", "fiscal_year": 2024, "sub_doc_slot": "S06", "table_id": "t2"}
    assert table_safe_jaccard_should_merge(t25, t24) is False
    assert table_safe_jaccard_should_merge(t25, dict(t25)) is True
