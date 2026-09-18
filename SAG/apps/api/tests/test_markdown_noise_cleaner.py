"""Kiểm tra bộ lọc nhiễu Markdown OCR BCTC."""

from __future__ import annotations

from sag_api.parsing.markdown_noise_cleaner import clean_markdown


def test_html_comments_are_removed():
    markdown = "<!-- page:start 3 -->\n# Tiêu đề\n<!-- page:end 3 -->\n"
    cleaned, stats = clean_markdown(markdown)
    assert "<!--" not in cleaned
    assert cleaned == "# Tiêu đề\n"
    assert stats.html_comments == 2


def test_repeated_headings_keep_first_occurrence():
    markdown = "## CÔNG TY CỔ PHẦN FPT\n# Nội dung\n## CÔNG TY CỔ PHẦN FPT\n"
    cleaned, stats = clean_markdown(markdown)
    assert cleaned.count("## CÔNG TY CỔ PHẦN FPT") == 1
    assert stats.repeated_headings == 1


def test_repeated_heading_keeps_lowest_level():
    markdown = "## CÔNG TY\n# CÔNG TY\n"
    cleaned, _stats = clean_markdown(markdown)
    assert cleaned == "# CÔNG TY\n"


def test_numbered_headings_are_not_deduplicated():
    markdown = "## 1. THÔNG TIN CHUNG\n## 1. THÔNG TIN CHUNG\n"
    cleaned, stats = clean_markdown(markdown)
    assert cleaned.count("## 1. THÔNG TIN CHUNG") == 2
    assert stats.repeated_headings == 0


def test_muc_luc_block_is_removed():
    markdown = "# BÁO CÁO\n\n## MỤC LỤC\n1. A\n2. B\n\n## 1. NỘI DUNG\n"
    cleaned, stats = clean_markdown(markdown)
    assert "MỤC LỤC" not in cleaned
    assert "## 1. NỘI DUNG" in cleaned
    assert stats.toc_blocks == 1


def test_financial_statement_content_is_preserved_for_analysis():
    markdown = (
        "## 1. THÔNG TIN CHUNG\n"
        "## BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n"
        "doanh thu\nlợi nhuận\n"
        "## BÁO CÁO LƯU CHUYỂN TIỀN TỆ\n"
        "dòng tiền\n"
        "## 21. TRÌNH BÀY TRONG BÁO CÁO TÌNH HÌNH TÀI CHÍNH\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH" in cleaned
    assert "BÁO CÁO LƯU CHUYỂN TIỀN TỆ" in cleaned
    assert "doanh thu" in cleaned and "dòng tiền" in cleaned
    assert "## 21. TRÌNH BÀY TRONG BÁO CÁO TÌNH HÌNH TÀI CHÍNH" in cleaned
    assert stats.statement_sections == 0


def test_supplementary_notes_heading_survives_statement_filter():
    markdown = "## V. THÔNG TIN BỔ SUNG CHO CÁC KHOẢN MỤC TRÌNH BÀY TRÊN BẢNG BÁO CÁO TÌNH HÌNH TÀI CHÍNH\nnội dung\n"
    cleaned, stats = clean_markdown(markdown)
    assert "THÔNG TIN BỔ SUNG" in cleaned
    assert stats.statement_sections == 0


def test_governance_tables_and_financial_tables_are_not_deleted_by_cleaner():
    markdown = (
        "## II. HỘI ĐỒNG QUẢN TRỊ\n"
        "<table><tr><th>Thành viên</th><th>Chức vụ</th></tr>"
        "<tr><td>Nguyễn A</td><td>Chủ tịch</td></tr></table>\n"
        "## BÁO CÁO TÌNH HÌNH TÀI CHÍNH\n"
        "<table><tr><th>Tài sản</th><th>Cuối kỳ</th></tr>"
        "<tr><td>Tổng tài sản</td><td>100</td></tr></table>\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "Nguyễn A" in cleaned
    assert "Tổng tài sản" in cleaned
    assert stats.tables_converted == 2


def test_trailing_signature_block_is_stripped():
    markdown = (
        "## 8. CÁC KHOẢN ĐẦU TƯ\n"
        "nội dung\n"
        "Người lập bảng\n"
        "Kế toán trưởng\n"
        "TỔNG GIÁM ĐỐC\n"
        "Hà Nội, Việt Nam\n"
        "Ngày 15 tháng 4 năm 2026\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "Kế toán trưởng" not in cleaned
    assert "TỔNG GIÁM ĐỐC" not in cleaned
    assert "nội dung" in cleaned
    assert stats.signature_lines == 5


def test_latex_junk_lines_are_removed():
    markdown = "## 4. CHÍNH SÁCH KẾ TOÁN\n$x_1 \\delta g_{AB}$\nnội dung\n"
    cleaned, stats = clean_markdown(markdown)
    assert "\\delta" not in cleaned
    assert stats.latex_junk == 1


def test_blank_lines_are_collapsed_and_output_trimmed():
    markdown = "# A\n\n\n\nnội dung\n\n\n\n"
    cleaned, _stats = clean_markdown(markdown)
    assert cleaned == "# A\n\nnội dung\n"


def test_accounting_policy_is_preserved_for_analysis():
    markdown = (
        "## 1. THÔNG TIN CHUNG\n"
        "nội dung công ty\n"
        "## 4. TÓM TẮT CÁC CHÍNH SÁCH KẾ TOÁN CHỦ YẾU\n"
        "Tiền mặt gồm tiền tại quỹ\n"
        "TSCĐ khấu hao đường thẳng 10 năm\n"
        "## 5. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN\n"
        "Tiền mặt: 100 tỷ\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "TÓM TẮT CÁC CHÍNH SÁCH KẾ TOÁN CHỦ YẾU" in cleaned
    assert "TSCĐ khấu hao đường thẳng" in cleaned
    assert "## 1. THÔNG TIN CHUNG" in cleaned
    assert "## 5. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN" in cleaned
    assert stats.accounting_policy_sections == 0


def test_accounting_policy_with_change_keywords_is_preserved():
    markdown = (
        "## 4. TÓM TẮT CÁC CHÍNH SÁCH KẾ TOÁN CHỦ YẾU\n"
        "Áp dụng mới Thông tư 99/2025/TT-BTC từ ngày 01/01/2026\n"
        "Có sự thay đổi chính sách khấu hao tài sản\n"
        "## 5. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "TÓM TẮT CÁC CHÍNH SÁCH KẾ TOÁN CHỦ YẾU" in cleaned
    assert "Thông tư 99/2025/TT-BTC" in cleaned
    assert stats.accounting_policy_sections == 0


def test_images_and_cdn_urls_are_removed():
    markdown = (
        "# Thuyết minh BCTC\n"
        "![image](https://cdn-mineru.openxlab.org.cn/result/2026/test.jpg)\n"
        "Dòng chữ có ![inline](http://example.com/logo.png) inline image\n"
        "![](https://mineru.net/img.png)\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "cdn-mineru" not in cleaned
    assert "http://example.com/logo.png" not in cleaned
    assert "Dòng chữ có inline image" in cleaned
    assert stats.images_removed == 3


def test_common_english_mirror_lines_are_removed_but_unique_english_is_kept():
    markdown = (
        "Ủy ban Chứng Khoán Nhà Nước\n"
        "State Securities Commission of Vietnam\n"
        "Công ty có tên giao dịch quốc tế là BAF Vietnam Agriculture JSC\n"
        "BAF Vietnam Agriculture JSC\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "State Securities Commission" not in cleaned
    assert "BAF Vietnam Agriculture JSC" in cleaned
    assert stats.bilingual_duplicates_removed == 1


def test_financial_roles_can_drop_core_statement_sections_explicitly():
    markdown = (
        "## BÁO CÁO TÌNH HÌNH TÀI CHÍNH\n"
        "| Tài sản | 100 |\n"
        "## THUYẾT MINH\n"
        "Nợ vay và kỳ hạn\n"
        "## BÁO CÁO LƯU CHUYỂN TIỀN TỆ\n"
        "| Dòng tiền | 50 |\n"
        "## QUẢN TRỊ RỦI RO\n"
        "Rủi ro thanh khoản\n"
    )
    cleaned, stats = clean_markdown(markdown, doc_role="LATEST_QUARTER")
    assert "Tài sản" not in cleaned
    assert "Dòng tiền" not in cleaned
    assert "Nợ vay và kỳ hạn" in cleaned
    assert "Rủi ro thanh khoản" in cleaned
    assert stats.statement_sections == 2


def test_financial_report_wrapper_drops_the_three_statements_before_notes():
    markdown = (
        "# BÁO CÁO TÀI CHÍNH RIÊNG QUÝ II/2026\n"
        "| Tài sản | 100 |\n"
        "# BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n"
        "| Doanh thu | 200 |\n"
        "# BÁO CÁO LƯU CHUYỂN TIỀN TỆ\n"
        "| Tiền cuối kỳ | 300 |\n"
        "# BẢN THUYẾT MINH BÁO CÁO TÀI CHÍNH\n"
        "## 1. Tiền và tương đương tiền\n"
        "Tiền gửi ngân hàng.\n"
    )
    cleaned, stats = clean_markdown(markdown, doc_role="LATEST_QUARTER")
    assert "Tài sản" not in cleaned
    assert "Doanh thu" not in cleaned
    assert "Tiền cuối kỳ" not in cleaned
    assert "Tiền gửi ngân hàng" in cleaned
    assert stats.statement_sections >= 3


def test_form_codes_and_audit_stamp_noise_are_stripped():
    markdown = (
        "302-C.\n"
        "TY\n"
        "H YOUN NAM\n"
        "B09-DN/HN\n"
        "# Công ty Cổ phần Nhựa An Phát Xanh\n"
        "13\n"
        "## 1. THÔNG TIN VỀ CÔNG TY\n"
        "Nội dung công ty hợp lệ\n"
        "C.T.T.N.H.H\n"
        "ERNST & YOUNG VIETNAM\n"
        "Z.H.H. ★\n"
        "## 2. DOANH THU\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "B09-DN/HN" not in cleaned
    assert "302-C." not in cleaned
    assert "ERNST & YOUNG" not in cleaned
    assert "Z.H.H." not in cleaned
    assert "13\n" not in cleaned
    assert "# Công ty Cổ phần Nhựa An Phát Xanh" in cleaned
    assert "## 2. DOANH THU" in cleaned
    assert stats.stamps_removed >= 5


def test_html_tables_are_converted_to_gfm_markdown_tables():
    markdown = (
        "## 5. TIỀN VÀ TƯƠNG ĐƯƠNG TIỀN\n\n"
        '<table class="tb-note">\n'
        "  <thead>\n"
        "    <tr><th>Khoản mục</th><th>Cuối năm<br>(VND)</th><th>Đầu năm</th></tr>\n"
        "  </thead>\n"
        "  <tbody>\n"
        "    <tr><td>Tiền mặt tại quỹ</td><td>15.000.000.000</td><td>10.000.000.000</td></tr>\n"
        "    <tr><td>Tiền gửi ngân hàng | không kỳ hạn</td><td>200.000.000.000</td><td>150.000.000.000</td></tr>\n"
        "  </tbody>\n"
        "</table>\n\n"
        "Nội dung sau bảng\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "<table" not in cleaned
    assert "</table>" not in cleaned
    assert "<tbody>" not in cleaned
    assert "| Khoản mục | Cuối năm (VND) | Đầu năm |" in cleaned
    assert "| :--- | :--- | :--- |" in cleaned
    assert "| Tiền mặt tại quỹ | 15.000.000.000 | 10.000.000.000 |" in cleaned
    assert "| Tiền gửi ngân hàng \\| không kỳ hạn | 200.000.000.000 | 150.000.000.000 |" in cleaned
    assert "Nội dung sau bảng" in cleaned
    assert stats.tables_converted == 1


def test_html_table_rowspan_and_colspan_preserve_cell_positions():
    markdown = (
        '<table><tr><th rowspan="2">STT</th><th rowspan="2">Thành viên</th>'
        '<th colspan="2">Ngày</th></tr>'
        '<tr><th>Bổ nhiệm</th><th>Miễn nhiệm</th></tr>'
        '<tr><td>1</td><td>Nguyễn A</td><td>01/01/2026</td><td></td></tr></table>'
    )
    cleaned, _stats = clean_markdown(markdown)
    assert "| STT | Thành viên | Ngày |  |" in cleaned
    assert "| STT | Thành viên | Bổ nhiệm | Miễn nhiệm |" in cleaned
    assert "| 1 | Nguyễn A | 01/01/2026 |  |" in cleaned
