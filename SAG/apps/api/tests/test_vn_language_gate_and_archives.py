"""L1–L2 Tests for Vietnamese Language Gate, Per-Page/2-Column Bilingual Filter & .zip/.rar Archives."""

from __future__ import annotations

import io
import zipfile
from datetime import date

import fitz
import pytest

from sag_api.research import (
    ArchiveExtractionError,
    InputBuildError,
    ResearchCandidateDocument,
    ZipExtractionError,
    assess_pdf_language,
    build_research_sub_documents,
    detect_bilingual_two_column_crop,
    inspect_pdf_bytes,
    is_standalone_english_metadata,
    safe_extract_pdf_from_archive,
    safe_extract_pdf_from_zip,
    select_research_documents,
)
from sag_api.research.vn_language_gate import _validate_archive_member_name


def _make_utf8_pdf(pages_html: list[str], *, width: float = 595.0, height: float = 842.0) -> bytes:
    doc = fitz.open()
    for html in pages_html:
        page = doc.new_page(width=width, height=height)
        if html:
            page.insert_htmlbox(fitz.Rect(42, 42, width - 42, height - 42), html)
    raw = doc.tobytes()
    doc.close()
    return raw


def test_layer1_metadata_gate_rejects_pure_english_but_keeps_bilingual_and_core_db():
    # Pure English titles / filenames -> rejected at 0 download bytes
    assert is_standalone_english_metadata("Consolidated Financial Statements Q4 2025")[0] is True
    assert is_standalone_english_metadata("Annual Report 2025", url="https://cdn.vn/FPT_2025.pdf")[0] is True
    assert is_standalone_english_metadata("BCTC 2025", url="https://cdn.vn/FPT_FS_2025_EN.pdf")[0] is True
    assert is_standalone_english_metadata("Resolution of the Annual General Meeting 2026")[0] is True

    # Vietnamese, Bilingual (`Song ngữ` / `VI-EN`), and Core DB synthetic `BCTC CONSOLIDATED` -> KEPT!
    assert is_standalone_english_metadata("Báo cáo tài chính hợp nhất năm 2025")[0] is False
    assert is_standalone_english_metadata(
        "Annual Report 2025 (Bản song ngữ Việt - Anh)", url="https://cdn.vn/AR_VI_EN.pdf"
    )[0] is False
    assert is_standalone_english_metadata("BCTC CONSOLIDATED FPT FY2025")[0] is False


@pytest.mark.asyncio
async def test_full_english_digital_pdf_rejected_even_under_3_pages():
    """Fixes legacy `page_classifier.py` `if lo < 3: return total` & `is_clean_english` pass-through."""
    en_page = (
        "<h2>CONSOLIDATED STATEMENT OF FINANCIAL POSITION</h2>"
        "<p>As at 31 December 2025. Cash and cash equivalents of the Corporation amounted to "
        "VND 12,450 billion. Notes to the consolidated financial statements form an integral part.</p>"
    )
    for n_pages in (2, 18):
        en_pdf = _make_utf8_pdf([en_page] * n_pages)
        insp = inspect_pdf_bytes(en_pdf, source_url="fs_2025.pdf", doc_type="BCTC")
        assert insp.doc_class == "EXCLUDED_ENGLISH"
        assert insp.doc_language == "FULL_ENGLISH"
        assert insp.error == "EXCLUDED_FULL_ENGLISH_DOCUMENT"

        with pytest.raises(InputBuildError, match="EXCLUDED_FULL_ENGLISH_DOCUMENT"):
            await build_research_sub_documents(
                ticker="FPT",
                pdf_bytes=en_pdf,
                doc_type="BCTC",
                published_date=date(2026, 3, 28),
            )

    # Also verify `select_research_documents` rejects full-English payload cleanly
    en_pdf_16 = _make_utf8_pdf([en_page] * 16)
    vn_pdf_16 = _make_utf8_pdf(
        [
            "<h2>BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT</h2>"
            "<p>Tại ngày 31 tháng 12 năm 2025. Tiền và các khoản tương đương tiền đạt 12.450 tỷ đồng. "
            "Thuyết minh báo cáo tài chính hợp nhất là bộ phận hợp thành của báo cáo này.</p>"
        ]
        * 16
    )
    sel = await select_research_documents(
        "FPT",
        [
            ResearchCandidateDocument(
                doc_id="en_hidden",
                ticker="FPT",
                doc_type="BCTC",
                title="BCTC Q1 2026",  # Disguised VN title (post-audited FY2025) but 100% English PDF body!
                published_date="2026-04-25",
                raw_bytes=en_pdf_16,
            ),
            ResearchCandidateDocument(
                doc_id="vn_valid",
                ticker="FPT",
                doc_type="BCTC",
                title="Báo cáo tài chính hợp nhất năm 2025",
                statement_scope="CONSOLIDATED",
                published_date="2026-03-28",
                raw_bytes=vn_pdf_16,
            ),
        ],
        as_of_date=date(2026, 6, 30),
        inspect_payloads=True,
    )
    assert [s.candidate.doc_id for s in sel.selected] == ["vn_valid"]
    rejected_map = {r.doc_id: r.reason for r in sel.rejected}
    assert rejected_map["en_hidden"] == "EXCLUDED_FULL_ENGLISH_DOCUMENT"


@pytest.mark.asyncio
async def test_scan_canary_aborts_full_english_scan_saving_ocr_budget():
    """Scanned 20-page English PDF runs ONLY 2-page canary OCR and aborts before OCRing 18 remaining pages."""
    scan_pdf_20 = _make_utf8_pdf([""] * 20)

    class _EnglishScanOcr:
        def __init__(self) -> None:
            self.calls: list[list[int]] = []

        async def ocr_pdf_pages(
            self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"
        ) -> dict[int, str]:
            self.calls.append(list(page_numbers))
            return {
                p: (
                    "CONSOLIDATED INCOME STATEMENT FOR THE YEAR ENDED 31 DECEMBER 2025\n"
                    "Net revenue from sales of goods and rendering of services: 45,200,000 million VND.\n"
                    "The accompanying notes are an integral part of these consolidated financial statements."
                )
                for p in page_numbers
            }

    ocr_bctc = _EnglishScanOcr()
    with pytest.raises(InputBuildError, match="EXCLUDED_FULL_ENGLISH_SCAN"):
        await build_research_sub_documents(
            ticker="VNM",
            pdf_bytes=scan_pdf_20,
            doc_type="BCTC",
            published_date=date(2026, 3, 30),
            ocr_adapter=ocr_bctc,
        )
    assert len(ocr_bctc.calls) == 1
    assert len(ocr_bctc.calls[0]) == 2  # Saved 90% of OCR pages!

    # Scanned BCTN also aborts right after Pass-1 TOC canary without running Pass-2
    ocr_bctn = _EnglishScanOcr()
    with pytest.raises(InputBuildError, match="EXCLUDED_FULL_ENGLISH_SCAN"):
        await build_research_sub_documents(
            ticker="VNM",
            pdf_bytes=scan_pdf_20,
            doc_type="BCTN",
            published_date=date(2026, 4, 15),
            ocr_adapter=ocr_bctn,
        )
    assert len(ocr_bctn.calls) == 1
    assert len(ocr_bctn.calls[0]) == 5


@pytest.mark.asyncio
async def test_tail_half_and_alternating_page_english_translations_stripped():
    """Covers BOTH tail half-EN (with numeric continuation) AND alternating p1=VI, p2=EN, ..., p5=VI."""
    vi_fs = (
        "<h2>BẢNG CÂN ĐỐI KẾ TOÁN HỢP NHẤT</h2>"
        "<p>Đơn vị tính: Triệu đồng. Tài sản ngắn hạn tại ngày 31/12/2025 đạt 25.400.000. "
        "Thuyết minh số 5 trình bày chi tiết tiền và các khoản tương đương tiền.</p>"
    )
    en_fs = (
        "<h2>CONSOLIDATED BALANCE SHEET</h2>"
        "<p>Unit: Million VND. Current assets as at 31 December 2025 reached 25,400,000. "
        "Note 5 sets out cash and cash equivalents of the Group in detail.</p>"
    )
    en_numeric_cont = "<p>110  25,400,000  21,300,000\n120  8,100,000  7,500,000</p>"

    # Case 1: Pages 1..2 VI + Page 3 EN + Page 4 EN numeric continuation + Page 5 VI appendix!
    # (Legacy binary search failed 100% whenever last page was VI!)
    mixed_pdf = _make_utf8_pdf([vi_fs, vi_fs, en_fs, en_numeric_cont, vi_fs])
    built_mixed = await build_research_sub_documents(
        ticker="MSN",
        pdf_bytes=mixed_pdf,
        doc_type="BCTC",
        published_date=date(2026, 3, 29),
    )
    bctc_bundle = built_mixed.sub_documents[0]
    assert bctc_bundle.page_numbers == [1, 2, 5]
    assert built_mixed.excluded_pages[3] == "ENGLISH_TRANSLATION_PAGE"
    assert built_mixed.excluded_pages[4] == "ENGLISH_TRANSLATION_PAGE"
    assert "CONSOLIDATED BALANCE SHEET" not in bctc_bundle.markdown_content

    # Case 2: Strictly Alternating Page-by-Page (p1=VI, p2=EN, p3=VI, p4=EN, p5=VI, p6=EN)
    alt_pdf = _make_utf8_pdf([vi_fs, en_fs, vi_fs, en_fs, vi_fs, en_fs])
    built_alt = await build_research_sub_documents(
        ticker="VCB",
        pdf_bytes=alt_pdf,
        doc_type="BCTC",
        published_date=date(2026, 3, 29),
    )
    assert built_alt.sub_documents[0].page_numbers == [1, 3, 5]
    assert built_alt.excluded_pages[2] == "ENGLISH_TRANSLATION_PAGE"
    assert built_alt.excluded_pages[4] == "ENGLISH_TRANSLATION_PAGE"
    assert built_alt.excluded_pages[6] == "ENGLISH_TRANSLATION_PAGE"


@pytest.mark.asyncio
async def test_two_column_bilingual_page_crops_english_column_but_keeps_a3_vn_spread():
    """2-column Left=VI | Right=EN crops out Right EN column; Left=VI | Right=VI A3 spread keeps both!"""
    doc = fitz.open()
    pg = doc.new_page(width=595.0, height=842.0)
    pg.insert_htmlbox(
        fitz.Rect(30, 100, 280, 760),
        (
            "<p>Nghị quyết Đại hội đồng cổ đông thường niên năm 2026 thông qua kế hoạch kinh doanh "
            "với doanh thu thuần 55.000 tỷ đồng và lợi nhuận sau thuế 6.800 tỷ đồng, chia cổ tức "
            "bằng tiền mặt tỷ lệ 20% vốn điều lệ cho cổ đông hiện hữu.</p>"
        ),
    )
    pg.insert_htmlbox(
        fitz.Rect(315, 100, 565, 760),
        (
            "<p>Resolution of the 2026 Annual General Meeting of Shareholders approving the business "
            "plan with net revenue of VND 55,000 billion and profit after corporate income tax of "
            "VND 6,800 billion, paying cash dividend at 20% of charter capital to shareholders.</p>"
        ),
    )
    two_col_pdf = doc.tobytes()
    doc.close()

    with fitz.open(stream=two_col_pdf, filetype="pdf") as check_doc:
        crop = detect_bilingual_two_column_crop(check_doc[0])
        assert crop is not None
        assert crop[0] == 0.0 and 280.0 <= crop[1] <= 320.0

    built = await build_research_sub_documents(
        ticker="MWG",
        pdf_bytes=two_col_pdf,
        doc_type="NQ_DHDCD",
        published_date=date(2026, 4, 20),
    )
    md = built.sub_documents[0].markdown_content
    assert "doanh thu thuần 55.000 tỷ đồng" in md
    assert "Annual General Meeting of Shareholders" not in md

    # Contrast: A3 2-page Vietnamese spread (Left=VI, Right=VI) -> MUST NOT crop out either side!
    spread_doc = fitz.open()
    sp = spread_doc.new_page(width=1190.0, height=842.0)
    sp.insert_htmlbox(
        fitz.Rect(40, 100, 560, 760),
        "<p>Trang trái Tiếng Việt: Doanh thu mảng bán lẻ tăng trưởng 18% trong năm tài chính 2025.</p>" * 3,
    )
    sp.insert_htmlbox(
        fitz.Rect(630, 100, 1150, 760),
        "<p>Trang phải Tiếng Việt: Lợi nhuận gộp cải thiện mạnh nhờ tối ưu chi phí vận hành kho bãi.</p>" * 3,
    )
    spread_pdf = spread_doc.tobytes()
    spread_doc.close()
    assessment = assess_pdf_language(spread_pdf)
    assert assessment.two_col_crops == {}


def test_multi_pdf_zip_drops_english_member_and_merges_agm_proposals():
    vn_res = _make_utf8_pdf(
        [
            "<p>Nghị quyết Đại hội đồng cổ đông thường niên năm 2026 thông qua tờ trình phân phối lợi nhuận.</p>"
        ]
    )
    vn_prop = _make_utf8_pdf(
        [
            "<p>Tờ trình Hội đồng quản trị về kế hoạch đầu tư CAPEX và chỉ tiêu kinh doanh năm 2026.</p>"
        ]
    )
    vn_ballot = _make_utf8_pdf(["<p>Biên bản kiểm phiếu biểu quyết tại đại hội cổ đông.</p>"])
    # Make English PDF 3x larger to prove size-bias never picks English over Vietnamese!
    en_large = _make_utf8_pdf(
        [
            "<p>Resolution of the Annual General Meeting of Shareholders and Consolidated Financial Statements "
            "for the fiscal year ended 31 December 2025 of the Corporation.</p>"
        ]
        * 4
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("01_bien_ban_kiem_phieu.pdf", vn_ballot)
        zf.writestr("02_to_trinh_hdqt_2026.pdf", vn_prop)
        zf.writestr("03_nghi_quyet_dhdcd_2026.pdf", vn_res)
        zf.writestr("04_AGM_Resolution_2026_EN.pdf", en_large)

    merged_bytes, chosen_name, _entries = safe_extract_pdf_from_zip(
        buf.getvalue(), doc_type="TAI_LIEU_DHDCD"
    )
    assert "04_AGM_Resolution_2026_EN.pdf" not in chosen_name
    assert "01_bien_ban_kiem_phieu.pdf" not in chosen_name
    assert chosen_name == "03_nghi_quyet_dhdcd_2026.pdf+02_to_trinh_hdqt_2026.pdf"
    with fitz.open(stream=merged_bytes, filetype="pdf") as mdoc:
        assert len(mdoc) == 2

    # Archive with ONLY English PDFs -> fails closed with EXCLUDED_FULL_ENGLISH_DOCUMENT
    en_only_buf = io.BytesIO()
    with zipfile.ZipFile(en_only_buf, "w") as zf:
        zf.writestr("FS_2025_Consolidated_EN.pdf", en_large)
    with pytest.raises(ZipExtractionError, match="EXCLUDED_FULL_ENGLISH_DOCUMENT"):
        safe_extract_pdf_from_archive(en_only_buf.getvalue(), doc_type="BCTC")

    # RAR path-traversal & invalid magic guards
    with pytest.raises(ArchiveExtractionError):
        _validate_archive_member_name("../outside.pdf")
    with pytest.raises(ZipExtractionError, match="Corrupt RAR archive"):
        safe_extract_pdf_from_archive(b"Rar!\x1a\x07\x01\x00corrupt_payload_bytes")


def test_archive_drops_english_twin_without_en_suffix_even_when_larger_and_scanned():
    """Regression for HDC (BCTN ZIP) & FTS (AGM ZIP): scanned EN twins without `_EN.pdf` suffix."""
    scan_small = _make_utf8_pdf(["", ""])
    scan_large = _make_utf8_pdf(["", "", "", "", ""])

    # Case A (HDC class): Vietnamese BCTN scan (smaller) + English Annual Report scan (larger)
    hdc_buf = io.BytesIO()
    with zipfile.ZipFile(hdc_buf, "w") as zf:
        zf.writestr("20260420 - HDC - Bao cao thuong nien nam 2025.pdf", scan_small)
        zf.writestr("20260420 - HDC - 2025 Annual Report.pdf", scan_large)

    chosen_bytes, chosen_name, _ = safe_extract_pdf_from_zip(hdc_buf.getvalue(), doc_type="BCTN")
    assert "Bao cao thuong nien" in chosen_name
    assert "Annual Report" not in chosen_name
    with fitz.open(stream=chosen_bytes, filetype="pdf") as d:
        assert len(d) == 2

    # Case B (FTS class): Vietnamese AGM scan + English AGM scan with Vietnamese CA X.509 signature stamp
    # on page 1 -> MUST ignore the CA signature overlay and drop the English twin!
    en_scan_with_vn_ca_stamp = _make_utf8_pdf(
        [
            "<p>Digitally signed by CÔNG TY CỔ PHẦN CHỨNG KHOÁN FPT<br/>"
            "DN: C=VN, S=HÀ NỘI, L=Quận Tây Hồ, CN=CÔNG TY CỔ PHẦN CHỨNG KHOÁN FPT, "
            "OID.0.9.2342.19200300.100.1.1=MST:0102324187</p>",
            "",
            "",
            "",
            "",
        ]
    )
    fts_buf = io.BytesIO()
    with zipfile.ZipFile(fts_buf, "w") as zf:
        zf.writestr("m88_123___fts___nghi_quyet_va_bien_ban_dhdcd_2026.pdf", scan_small)
        zf.writestr("m88_123___fts___resolution_and_minutes_agm_2026.pdf", en_scan_with_vn_ca_stamp)

    agm_bytes, agm_name, _ = safe_extract_pdf_from_zip(fts_buf.getvalue(), doc_type="NQ_DHDCD")
    assert "nghi_quyet" in agm_name
    assert "resolution_and_minutes_agm" not in agm_name
    with fitz.open(stream=agm_bytes, filetype="pdf") as d:
        assert len(d) == 2


@pytest.mark.asyncio
async def test_archive_candidate_fallback_avoids_premature_english_tombstone():
    """If candidate #1 in a multi-PDF neutral-named archive is a full-English scan, try candidate #2."""
    # Both have neutral filenames and scanned pages; pdf_a is larger so ranked #1, but OCR shows English!
    pdf_en_scan = _make_utf8_pdf([""] * 18)
    pdf_vi_scan = _make_utf8_pdf([""] * 16)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("01_filing_a.pdf", pdf_en_scan)
        zf.writestr("02_filing_b.pdf", pdf_vi_scan)

    class _FallbackCanaryOcr:
        def __init__(self) -> None:
            self.call_idx = 0

        async def ocr_pdf_pages(
            self, pdf_bytes: bytes, page_numbers: list[int], *, language: str = "vi"
        ) -> dict[int, str]:
            self.call_idx += 1
            if self.call_idx == 1:
                # Candidate #1 (18p) is full English
                return {
                    p: (
                        "ANNUAL REPORT 2025. Consolidated financial statements and business operations "
                        "of the Corporation for the year ended 31 December 2025."
                    )
                    for p in page_numbers
                }
            # Candidate #2 (16p) is Vietnamese!
            return {
                p: (
                    f"Báo cáo thường niên năm 2025 trang {p}. Doanh thu thuần đạt 5.200 tỷ đồng, "
                    "lợi nhuận sau thuế tăng trưởng 18% theo kế hoạch kinh doanh năm 2026."
                )
                for p in page_numbers
            }

    ocr = _FallbackCanaryOcr()
    built = await build_research_sub_documents(
        ticker="HDC",
        pdf_bytes=buf.getvalue(),
        doc_type="BCTN",
        published_date=date(2026, 4, 20),
        ocr_adapter=ocr,
    )
    assert built.inspection.chosen_entry == "02_filing_b.pdf"
    slots = {s.sub_doc_slot for s in built.sub_documents}
    assert "BCTN_CORE" in slots


def test_rar_encoding_error_taxonomy_and_7z_utf8_listing(monkeypatch):
    """Distinguish host bsdtar UTF-8 RAR5 limitation from corrupt RAR files, and verify 7z UTF-8 parsing."""
    from types import SimpleNamespace

    from sag_api.research import vn_language_gate as vlg

    fake_rar = b"Rar!\x1a\x07\x01\x00" + b"0" * 64

    # 1. When 7z/unrar absent and bsdtar reports unreadable filename -> crisp encoding error
    monkeypatch.setattr(vlg, "_resolve_7z_binary", lambda: None)
    monkeypatch.setattr(vlg, "_resolve_unrar_binary", lambda: None)
    monkeypatch.setattr(vlg, "_resolve_bsdtar_binary", lambda: "tar.exe")
    monkeypatch.setattr(
        vlg.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(
            returncode=1,
            stdout=b"",
            stderr=b"tar.exe: Archive entry has empty or unreadable filename ... skipping",
        ),
    )
    with pytest.raises(ArchiveExtractionError, match="RAR_UNPACKER_ENCODING_UNSUPPORTED"):
        vlg._read_rar_pdf_members_bounded(fake_rar)

    # 2. When 7z is present -> parses UTF-8 Vietnamese RAR5 member cleanly via -slt + bounded stream
    monkeypatch.setattr(vlg, "_resolve_7z_binary", lambda: "7z.exe")
    slt_out = (
        "Path = Báo cáo thường niên 2025_CII_VIE.pdf\n"
        "Size = 128\n"
        "Packed Size = 100\n"
        "Folder = -\n\n"
    ).encode()
    monkeypatch.setattr(
        vlg.subprocess,
        "run",
        lambda *a, **kw: SimpleNamespace(returncode=0, stdout=slt_out, stderr=b""),
    )
    monkeypatch.setattr(
        vlg,
        "_stream_subprocess_bounded",
        lambda *a, **kw: b"%PDF-1.4\nfake_cii_pdf",
    )
    extracted, desc = vlg._read_rar_pdf_members_bounded(fake_rar)
    assert len(extracted) == 1
    assert extracted[0][0] == "Báo cáo thường niên 2025_CII_VIE.pdf"
    assert extracted[0][1].startswith(b"%PDF-1.4")
    assert "Báo cáo thường niên 2025_CII_VIE.pdf" in desc[0]
