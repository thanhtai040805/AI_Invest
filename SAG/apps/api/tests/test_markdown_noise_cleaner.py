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


def test_statement_leak_is_stripped_until_numbered_heading():
    markdown = (
        "## 1. THÔNG TIN CHUNG\n"
        "## BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH\n"
        "doanh thu\nlợi nhuận\n"
        "## BÁO CÁO LƯU CHUYỂN TIỀN TỆ\n"
        "dòng tiền\n"
        "## 21. TRÌNH BÀY TRONG BÁO CÁO TÌNH HÌNH TÀI CHÍNH\n"
    )
    cleaned, stats = clean_markdown(markdown)
    assert "BÁO CÁO KẾT QUẢ HOẠT ĐỘNG KINH DOANH" not in cleaned
    assert "BÁO CÁO LƯU CHUYỂN TIỀN TỆ" not in cleaned
    assert "doanh thu" not in cleaned and "dòng tiền" not in cleaned
    assert "## 21. TRÌNH BÀY TRONG BÁO CÁO TÌNH HÌNH TÀI CHÍNH" in cleaned
    assert stats.statement_sections == 1


def test_supplementary_notes_heading_survives_statement_filter():
    markdown = "## V. THÔNG TIN BỔ SUNG CHO CÁC KHOẢN MỤC TRÌNH BÀY TRÊN BẢNG BÁO CÁO TÌNH HÌNH TÀI CHÍNH\nnội dung\n"
    cleaned, stats = clean_markdown(markdown)
    assert "THÔNG TIN BỔ SUNG" in cleaned
    assert stats.statement_sections == 0


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


def test_generic_accounting_policy_is_stripped():
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
    assert "TÓM TẮT CÁC CHÍNH SÁCH KẾ TOÁN CHỦ YẾU" not in cleaned
    assert "TSCĐ khấu hao đường thẳng" not in cleaned
    assert "## 1. THÔNG TIN CHUNG" in cleaned
    assert "## 5. TIỀN VÀ CÁC KHOẢN TƯƠNG ĐƯƠNG TIỀN" in cleaned
    assert stats.accounting_policy_sections == 1


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
    assert stats.accounting_policy_sections == 1


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