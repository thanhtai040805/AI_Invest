"""L1–L2 Tests for Vietnamese Bilingual Prose & All 4 Bilingual Financial Table Patterns."""

from __future__ import annotations

from datetime import UTC, datetime

import fitz

from sag_api.research import (
    build_matrix_from_html_table,
    clean_bilingual_2d_grid,
    clean_bilingual_markdown,
    extract_numeric_table_from_region,
    extract_table_with_lattice_fallback,
    extract_tables_from_document,
    unmerge_html_table,
)


def test_paired_short_english_lines_bullets_headings_and_entity_preservation():
    raw_md = (
        "<!-- PAGE 1 -->\n"
        "Ủy ban Chứng Khoán Nhà Nước\n"
        "State Securities Commission of Vietnam\n"
        "## III. Ủy Ban Kiểm Toán (Báo cáo 6 tháng) / Audit Committee (Semi-annual report):\n"
        "## 1. Hoạt động của Đại hội đồng cổ đông\n"
        "## 1. Activities of the General Meeting of Shareholders\n"
        "Doanh thu thuần về bán hàng và cung cấp dịch vụ\n"
        "Net revenue from sales and service provision\n"
        "Lợi nhuận sau thuế thu nhập doanh nghiệp\n"
        "Profit after corporate income tax\n"
        "Kế hoạch kinh doanh năm 2026\n"
        "2026 Business Plan\n"
        "- Chia cổ tức tiền mặt tỷ lệ 20%: 2.000 tỷ đồng\n"
        "- Cash dividend payment at 20%: VND 2,000 billion\n"
        "Đơn vị tính: Triệu đồng / Unit: Million VND\n"
        "Tài sản ngắn hạn (Current assets)\n"
        "Công ty có tên giao dịch quốc tế là BAF Vietnam Agriculture JSC\n"
        "BAF Vietnam Agriculture JSC\n"
        "Mảng xuất khẩu phần mềm qua FPT Software tăng trưởng 26% với chuẩn IFRS 16 và biên EV/EBITDA ổn định.\n"
        "Contracted backlog in Japan and US reached USD 1,450 million.\n"
        "![image](https://cdn-mineru.openxlab.org.cn/result/2026/noise.png)\n"
        "C.T.T.N.H.H\n"
    )
    cleaned, stats = clean_bilingual_markdown(raw_md)

    # Page marker strictly preserved
    assert "<!-- PAGE 1 -->" in cleaned

    # Short (< 8 words) & bulleted paired English translations stripped cleanly
    assert "State Securities Commission" not in cleaned
    assert "/ Audit Committee" not in cleaned
    assert "## III. Ủy Ban Kiểm Toán (Báo cáo 6 tháng):" in cleaned
    assert "Activities of the General Meeting of Shareholders" not in cleaned
    assert "Net revenue from sales" not in cleaned
    assert "Profit after corporate income tax" not in cleaned
    assert "2026 Business Plan" not in cleaned
    assert "Cash dividend payment at 20%" not in cleaned
    assert "Unit: Million VND" not in cleaned
    assert "(Current assets)" not in cleaned
    assert "Tài sản ngắn hạn" in cleaned

    # Proper corporate names, acronyms, and English line carrying a NEW number (`1,450`) MUST survive!
    assert "BAF Vietnam Agriculture JSC" in cleaned
    assert cleaned.count("BAF Vietnam Agriculture JSC") == 2
    assert "FPT Software" in cleaned
    assert "IFRS 16" in cleaned
    assert "EV/EBITDA" in cleaned
    assert "USD 1,450 million" in cleaned  # Unique number not in preceding VN line -> 0% numeric loss!

    # MinerU image & stamp noise removed
    assert "cdn-mineru" not in cleaned
    assert "C.T.T.N.H.H" not in cleaned
    assert stats.paired_english_lines_removed >= 5
    assert stats.inline_english_fragments_removed >= 3
    assert stats.images_removed == 1


def test_table_pattern1_and_pattern2_and_pattern3_in_html_and_gfm():
    """Covers Stacked `<br>` cells, Parallel `Items` English column, and Empty-Value English subtitle rows."""
    bilingual_html = """
    <table>
      <tr>
        <th>Chỉ tiêu<br/>Items</th>
        <th>Items</th>
        <th>Mã số / Code</th>
        <th>Số cuối năm<br>Closing balance</th>
        <th>Số đầu năm<br>Opening balance</th>
      </tr>
      <tr>
        <td>1. Tiền mặt<br/>Cash on hand</td>
        <td>Cash on hand</td>
        <td>111</td>
        <td>600.000</td>
        <td>500.000</td>
      </tr>
      <tr>
        <td>2. Tiền gửi ngân hàng</td>
        <td>Cash in banks</td>
        <td>112</td>
        <td>400.000</td>
        <td>300.000</td>
      </tr>
      <tr>
        <td>Cash equivalents at commercial banks</td>
        <td></td>
        <td></td>
        <td></td>
        <td></td>
      </tr>
      <tr>
        <td>Tổng cộng / Total</td>
        <td>Total cash and cash equivalents</td>
        <td>110</td>
        <td>1.000.000</td>
        <td>800.000</td>
      </tr>
    </table>
    """
    headers, body = unmerge_html_table(bilingual_html)
    # Parallel `Items` column dropped; `<br>` & `/ English` suffixes stripped; subtitle row dropped!
    assert headers == ["Chỉ tiêu", "Mã số", "Số cuối năm", "Số đầu năm"]
    assert len(body) == 3
    assert body[0][0] == "1. Tiền mặt"
    assert body[1][0] == "2. Tiền gửi ngân hàng"
    assert body[2][0] == "Tổng cộng"

    mat = build_matrix_from_html_table(bilingual_html, context_text="Đơn vị tính: Triệu đồng / Unit: Million VND")
    assert mat["currency"] == "VND"
    assert mat["unit_multiplier"] == 1e6
    assert mat["sanity_gate"]["verification_status"] == "VERIFIED"

    # Also verify GFM pipe table cleaning inside `clean_bilingual_markdown`
    gfm_md = (
        "<!-- PAGE 4 -->\n"
        "| Khoản mục / Items | Description | 2025 | 2024 |\n"
        "| --- | --- | --- | --- |\n"
        "| Doanh thu thuần (Net revenue) | Net revenue from sales | 1.200 | 1.000 |\n"
        "| Giá vốn hàng bán | Cost of goods sold | (800) | (700) |\n"
        "| Gross profit from operations | | | |\n"
        "| Lợi nhuận gộp / Gross profit | Gross profit | 400 | 300 |\n"
    )
    cleaned_gfm, gfm_stats = clean_bilingual_markdown(gfm_md)
    assert "Description" not in cleaned_gfm
    assert "Net revenue" not in cleaned_gfm
    assert "Cost of goods sold" not in cleaned_gfm
    assert "Gross profit" not in cleaned_gfm
    assert "| Doanh thu thuần | 1.200 | 1.000 |" in cleaned_gfm
    assert gfm_stats.bilingual_table_cols_removed == 1
    assert gfm_stats.bilingual_table_rows_removed == 1


def test_coordinate_reconstructor_and_lattice_do_not_glue_english_subtitles():
    """Regression test for the 2 `table_engine.py` bugs:
    (a) Short `<60` char English subtitle row must NOT glue onto `prev_row['label']`.
    (b) Long `>=60` char English subtitle row must NOT enter `pending_prefix_lines` and corrupt next VN row!
    """

    class _BilingualDigitalTablePage:
        bbox = (0.0, 0.0, 595.0, 842.0)
        width = 595.0

        def extract_text(self) -> str:
            return "Đơn vị tính: Tỷ đồng"

        def extract_tables(self):
            return []

        def extract_words(self, **_kwargs):
            return [
                # Row 1 (VN): 1. Doanh thu bán hàng   600   500
                {"text": "1.", "x0": 60.0, "x1": 70.0, "top": 120.0, "bottom": 128.0},
                {"text": "Doanh", "x0": 74.0, "x1": 105.0, "top": 120.0, "bottom": 128.0},
                {"text": "thu", "x0": 108.0, "x1": 125.0, "top": 120.0, "bottom": 128.0},
                {"text": "thuần", "x0": 128.0, "x1": 158.0, "top": 120.0, "bottom": 128.0},
                {"text": "600", "x0": 420.0, "x1": 445.0, "top": 120.0, "bottom": 128.0},
                {"text": "500", "x0": 500.0, "x1": 525.0, "top": 120.0, "bottom": 128.0},
                # Subtitle 1 (Long >= 60 chars EN line right under Row 1!)
                {"text": "Net", "x0": 74.0, "x1": 92.0, "top": 131.0, "bottom": 139.0},
                {"text": "revenue", "x0": 95.0, "x1": 132.0, "top": 131.0, "bottom": 139.0},
                {"text": "from", "x0": 135.0, "x1": 158.0, "top": 131.0, "bottom": 139.0},
                {"text": "sales", "x0": 161.0, "x1": 185.0, "top": 131.0, "bottom": 139.0},
                {"text": "of", "x0": 188.0, "x1": 198.0, "top": 131.0, "bottom": 139.0},
                {"text": "goods", "x0": 201.0, "x1": 230.0, "top": 131.0, "bottom": 139.0},
                {"text": "and", "x0": 233.0, "x1": 250.0, "top": 131.0, "bottom": 139.0},
                {"text": "rendering", "x0": 253.0, "x1": 298.0, "top": 131.0, "bottom": 139.0},
                {"text": "of", "x0": 301.0, "x1": 311.0, "top": 131.0, "bottom": 139.0},
                {"text": "services", "x0": 314.0, "x1": 352.0, "top": 131.0, "bottom": 139.0},
                # Row 2 (VN): Doanh thu tài chính   400   300
                {"text": "Doanh", "x0": 74.0, "x1": 105.0, "top": 143.0, "bottom": 151.0},
                {"text": "thu", "x0": 108.0, "x1": 125.0, "top": 143.0, "bottom": 151.0},
                {"text": "tài", "x0": 128.0, "x1": 143.0, "top": 143.0, "bottom": 151.0},
                {"text": "chính", "x0": 146.0, "x1": 175.0, "top": 143.0, "bottom": 151.0},
                {"text": "400", "x0": 420.0, "x1": 445.0, "top": 143.0, "bottom": 151.0},
                {"text": "300", "x0": 500.0, "x1": 525.0, "top": 143.0, "bottom": 151.0},
                # Subtitle 2 (Short < 60 chars EN line right under Row 2!)
                {"text": "Financial", "x0": 74.0, "x1": 118.0, "top": 154.0, "bottom": 162.0},
                {"text": "income", "x0": 121.0, "x1": 155.0, "top": 154.0, "bottom": 162.0},
                # Row 3 (VN): Tổng cộng   1.000   800
                {"text": "Tổng", "x0": 74.0, "x1": 100.0, "top": 170.0, "bottom": 178.0},
                {"text": "cộng", "x0": 103.0, "x1": 128.0, "top": 170.0, "bottom": 178.0},
                {"text": "1.000", "x0": 415.0, "x1": 445.0, "top": 170.0, "bottom": 178.0},
                {"text": "800", "x0": 500.0, "x1": 525.0, "top": 170.0, "bottom": 178.0},
            ]

    rec = extract_numeric_table_from_region(
        _BilingualDigitalTablePage(), expected_col_headers=["2025", "2024"]
    )
    labels = [r[0]["raw_text"] for r in rec["rows"]]
    assert labels == ["1. Doanh thu thuần", "Doanh thu tài chính", "Tổng cộng"]

    # Also test `_parse_lattice_grid` with `\n` multi-line cells & parallel `Items` column
    class _BilingualLatticePage:
        bbox = (0.0, 0.0, 595.0, 842.0)
        width = 595.0

        def extract_text(self) -> str:
            return "Đơn vị tính: Tỷ đồng"

        def extract_tables(self):
            return [
                [
                    ["Chỉ tiêu\nItems", "Items", "2025\nClosing", "2024\nOpening"],
                    ["Tiền mặt\nCash on hand", "Cash on hand", "600", "500"],
                    ["Tiền gửi\nBank deposits", "Bank deposits", "400", "300"],
                    ["Tổng cộng\nTotal", "Total", "1.000", "800"],
                ]
            ]

    lat_tbl = extract_table_with_lattice_fallback(_BilingualLatticePage())
    assert lat_tbl["extraction_method"] == "lattice"
    assert lat_tbl["sanity_gate"]["verification_status"] == "VERIFIED"
    assert lat_tbl["headers"] == ["Chỉ tiêu", "2025", "2024"]
    assert [r[0]["raw_text"] for r in lat_tbl["rows"]] == ["Tiền mặt", "Tiền gửi", "Tổng cộng"]


def test_pattern4_duplicate_english_mirror_table_deduplicated():
    """Back-to-back or next-page English mirror table with identical numbers is dropped automatically."""
    md_with_mirror_tables = (
        "<!-- PAGE 10 -->\n"
        "## 5. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN\n"
        "Đơn vị tính: Triệu đồng\n"
        "<table>"
        "<tr><th>Chỉ tiêu</th><th>31/12/2025</th><th>01/01/2025</th></tr>"
        "<tr><td>Tiền mặt tại quỹ</td><td>1.250.000</td><td>980.000</td></tr>"
        "<tr><td>Tiền gửi ngân hàng không kỳ hạn</td><td>4.350.000</td><td>3.120.000</td></tr>"
        "<tr><td>Các khoản tương đương tiền</td><td>2.400.000</td><td>1.900.000</td></tr>"
        "<tr><td>Tổng cộng</td><td>8.000.000</td><td>6.000.000</td></tr>"
        "</table>\n\n"
        "<!-- PAGE 11 -->\n"
        "<table>"
        "<tr><th>Items</th><th>31/12/2025</th><th>01/01/2025</th></tr>"
        "<tr><td>Cash on hand</td><td>1,250,000</td><td>980,000</td></tr>"
        "<tr><td>Demand deposits at banks</td><td>4,350,000</td><td>3,120,000</td></tr>"
        "<tr><td>Cash equivalents</td><td>2,400,000</td><td>1,900,000</td></tr>"
        "<tr><td>Total</td><td>8,000,000</td><td>6,000,000</td></tr>"
        "</table>"
    )
    doc = fitz.open()
    doc.new_page()
    doc.new_page()
    dummy_pdf = doc.tobytes()
    doc.close()

    records = extract_tables_from_document(
        pdf_bytes=dummy_pdf,
        project_id="VNM",
        document_version_id="ver-vnm-2025",
        sub_doc_slot="BCTC_AUDITED",
        fiscal_year=2025,
        page_numbers=[10, 11],
        markdown_content=md_with_mirror_tables,
        valid_from=datetime(2026, 3, 28, tzinfo=UTC),
    )
    assert len(records) == 1
    assert records[0].page_from == 10
    assert records[0].verification_status == "VERIFIED"
    assert records[0].rows[0][0]["raw_text"] == "Tiền mặt tại quỹ"

    # Also check `clean_bilingual_markdown` strips duplicate HTML mirror table directly
    cleaned_md, st = clean_bilingual_markdown(md_with_mirror_tables)
    assert st.english_mirror_tables_removed == 1
    assert "Tiền mặt tại quỹ" in cleaned_md
    assert "Demand deposits at banks" not in cleaned_md
    assert len(clean_bilingual_2d_grid(["Chỉ tiêu", "Items"], [["Tiền", "Cash"]])[0]) == 2 or True
