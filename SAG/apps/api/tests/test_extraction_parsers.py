from sag_api.extraction.parsers.accounting_taxonomy import (
    classify_statement_line,
    detect_unit_scale,
    detect_unit_scale_context,
)
from sag_api.extraction.parsers.governance_table_parser import GovernanceTableParser
from sag_api.extraction.parsers.rpt_table_parser import RptTableParser
from sag_api.extraction.parsers.statement_table_parser import StatementTableParser
from sag_api.extraction.parsers.statement_table_parser import assess_financial_document_completeness
from sag_api.services.extraction_v2_service import _classify_table_fact
from sag_api.services.extraction_v2_service import _infer_accounting_scope


def test_statement_parser_does_not_map_cash_flow_codes_to_income_facts() -> None:
    markdown = """
BẢNG CÂN ĐỐI KẾ TOÁN
| Mã số | TÀI SẢN | Thuyết minh | Số cuối năm | Số đầu năm |
| :--- | :--- | :--- | :--- | :--- |
| 270 | TỔNG CỘNG TÀI SẢN |  | 100 | 90 |
| 400 | Vốn chủ sở hữu | 22 | 241 | 220 |

BÁO CÁO LƯU CHUYỂN TIỀN TỆ
| Mã số | CHỈ TIÊU | Thuyết minh | Năm nay | Năm trước |
| :--- | :--- | :--- | :--- | :--- |
| 01 | Lợi nhuận kế toán trước thuế |  | 10 | 9 |
"""
    facts = StatementTableParser(markdown).parse_statement_facts()
    assert [(fact["semantic_key"], fact["evidence"]["line_start"]) for fact in facts] == [
        ("total_assets", 5),
        ("total_equity", 6),
    ]
    assert facts[1]["value_numeric"] == 241


def test_statement_parser_keeps_rows_when_ocr_drops_opening_pipe() -> None:
    markdown = """
|  | Mã số | 30/06/2026 |
| :--- | :--- | :--- |
Các khoản phải thu ngắn hạn | 130 | 5.456.609.883.328 |
TỔNG TÀI SẢN | 270 | 49.460.067.445.387 |
"""
    facts = StatementTableParser(markdown, "TEST").parse_statement_facts()
    assert {fact["semantic_key"] for fact in facts} >= {"total_receivables", "total_assets"}


def test_statement_parser_classifies_english_statement_rows() -> None:
    markdown = """
## BALANCE SHEET
| Item | Current period | Previous period |
| :--- | ---: | ---: |
| TOTAL ASSETS | 100 | 90 |
| TOTAL EQUITY | 40 | 35 |
"""
    facts = StatementTableParser(markdown, "TEST").parse_statement_facts()
    assert {fact["semantic_key"] for fact in facts} == {"total_assets", "total_equity"}


def test_source_completeness_distinguishes_a_truncated_financial_source() -> None:
    result = assess_financial_document_completeness(
        "# TIX\n## BALANCE SHEET\n| TOTAL ASSETS | 100 |",
        "ANNUAL_BACKBONE",
    )
    assert result["status"] == "INCOMPLETE"
    assert set(result["missing"]) == {"income_statement", "cash_flow", "notes"}


def test_source_completeness_recognizes_english_securities_statement_headings() -> None:
    markdown = "\n".join(
        [
            "## STATEMENT OF FINANCIAL POSITION",
            "## STATEMENT OF COMPREHENSIVE INCOME",
            "## STATEMENT OF CASH FLOWS",
            "## NOTES TO THE INTERIM FINANCIAL STATEMENTS",
        ]
    )
    result = assess_financial_document_completeness(markdown, "LATEST_QUARTER")
    assert result["status"] == "COMPLETE"
    assert result["missing"] == []


def test_source_completeness_rejects_numericless_balance_sheet_total() -> None:
    result = assess_financial_document_completeness(
        "\n".join(
            [
                "## BALANCE SHEET",
                "TỔNG CỘNG TÀI SẢN (280 = 100 + 200)",
                "280",
                "## INCOME STATEMENT",
                "## CASH FLOW",
                "## NOTES TO FINANCIAL STATEMENTS",
            ]
        ),
        "LATEST_QUARTER",
    )
    assert result["status"] == "INCOMPLETE"
    assert result["data_issues"] == ["balance_sheet.total_assets_value"]


def test_rpt_parser_extracts_aggregate_related_party_balances_outside_note() -> None:
    markdown = """
## BALANCE SHEET
| Item | 30/06/2026 |
| :--- | ---: |
| Phải thu khách hàng là các bên liên quan | 199.077.377 |
| Phải trả người bán là các bên liên quan | 921.330.974 |
"""
    frames = RptTableParser("TIX").parse_rpt_tables(markdown)
    assert {(frame.flow_kind, frame.amount_vnd) for frame in frames} == {
        ("receivable_balance", 199077377.0),
        ("payable_balance", 921330974.0),
    }


def test_rpt_parser_drops_non_related_balance_subtotal() -> None:
    markdown = """
## BALANCE SHEET
| Item | 30/06/2026 |
| :--- | ---: |
| Phải thu ngắn hạn của khách hàng không phải là bên liên quan | 800.000.000 |
| Phải thu ngắn hạn của khách hàng là các bên liên quan | 200.000.000 |
"""
    frames = RptTableParser("TEST").parse_rpt_tables(markdown)
    assert [(frame.flow_kind, frame.amount_vnd) for frame in frames] == [("receivable_balance", 200_000_000.0)]


def test_explicit_report_scope_overrides_ambiguous_ocr() -> None:
    from sag_api.db.models import Document

    document = Document(filename="report.md")
    assert _infer_accounting_scope("BCTC rieng va hop nhat", document, "CONSOLIDATED") == "CONSOLIDATED"


def test_unit_scale_handles_ocr_glued_date_and_unit() -> None:
    assert detect_unit_scale_context("| 30/6/2026Triệu VND | 31/12/2025Triệu VND |") == (1_000_000.0, True)


def test_generic_table_facts_do_not_semanticize_cash_flow_rows() -> None:
    _, semantic_key, taxonomy = _classify_table_fact(
        "Báo cáo lưu chuyển tiền tệ", "Lợi nhuận trước thuế"
    )
    assert semantic_key.startswith("table:")
    assert taxonomy == "unmapped_table_line_item"


def test_bank_total_resources_is_not_classified_as_equity() -> None:
    result = classify_statement_line("Tổng nợ phải trả và vốn chủ sở hữu", is_bank=True)
    assert result is not None
    assert result[1] == "total_resources"


def test_income_statement_code_22_is_financial_income() -> None:
    result = classify_statement_line("Doanh thu hoạt động tài chính", "22")
    assert result is not None
    assert result[1] == "financial_income"


def test_governance_parser_requires_person_columns_and_header_owned_percentage() -> None:
    markdown = """
### Thành viên Hội đồng quản trị
| STT | Họ và tên | Chức vụ | Tỷ lệ sở hữu (%) |
| :--- | :--- | :--- | :--- |
| 1 | Nguyen Van A Mr. Nguyen Van A | Chủ tịch HĐQT | 12,5 |

| STT | Số Nghị quyết | Tỷ lệ thông qua |
| :--- | :--- | :--- |
| 1 | Nghị quyết số 01 | 100% |
"""
    frames = GovernanceTableParser("TEST").parse_governance_tables(markdown)
    assert len(frames) == 1
    assert frames[0].person_name == "Nguyen Van A"
    assert frames[0].ownership_pct == 12.5


def test_statement_parser_extracts_numeric_note_table_from_section_context() -> None:
    markdown = """
## 17. DOANH THU BÁN HÀNG VÀ CUNG CẤP DỊCH VỤ
| Từ ngày 01/01/2026 đến ngày 30/06/2026 | Từ ngày 01/01/2025 đến ngày 30/06/2025 |
| :--- | :--- |
| VND | VND |
| 354.589.335.568 | 400.471.612.217 |
| 354.589.335.568 | 400.471.612.217 |
"""
    facts = StatementTableParser(markdown, "TEST").parse_statement_facts()
    assert [(fact["semantic_key"], fact["value_numeric"]) for fact in facts] == [
        ("revenue", 354589335568.0)
    ]


def test_rpt_parser_rejects_accounting_labels_as_counterparties() -> None:
    markdown = """
## Các bên liên quan - giao dịch
| Đối tượng | Nội dung giao dịch | Giá trị |
| :--- | :--- | :--- |
| Công ty Cổ phần ABC | Cho vay | 1.000 |
| Tổng doanh thu thuần | Khấu hao | 2.000 |
"""
    frames = RptTableParser("TEST").parse_rpt_tables(markdown)
    assert [frame.object for frame in frames] == ["Công ty Cổ phần ABC"]


def test_statement_parser_zero_current_period_does_not_leak_previous_period() -> None:
    markdown = """
BẢNG CÂN ĐỐI KẾ TOÁN
| Mã số | CHỈ TIÊU | Thuyết minh | Số cuối năm | Số đầu năm |
| :--- | :--- | :--- | :--- | :--- |
| 130 | Các khoản phải thu ngắn hạn | 5 | - | 500.000.000 |
| 400 | Vốn chủ sở hữu | 22 | 1.000.000.000 | 800.000.000 |
"""
    facts = StatementTableParser(markdown, "TEST").parse_statement_facts()
    receivables = next(f for f in facts if f["semantic_key"] == "total_receivables")
    assert receivables["value_numeric"] == 0.0


def test_unit_scale_uses_statement_unit_not_narrative_unit() -> None:
    markdown = """
Đvt: Tỷ đồng
Kết quả kinh doanh tăng 10 tỷ đồng.

BÁO CÁO TÌNH HÌNH TÀI CHÍNH
Ngàn VND
| TÀI SẢN | Mã số | Số cuối kỳ | Số đầu kỳ |
| :--- | :--- | ---: | ---: |
| TỔNG CỘNG TÀI SẢN | 270 | 16.074.777.484 | 15.526.747.182 |
"""
    facts = StatementTableParser(markdown, "TEST").parse_statement_facts()
    assets = next(f for f in facts if f["semantic_key"] == "total_assets")
    assert assets["value_numeric"] == 16_074_777_484_000.0


def test_unit_scale_recognizes_thousand_vnd() -> None:
    assert detect_unit_scale("Đơn vị: Ngàn VND") == 1_000.0


def test_rpt_parser_zero_current_period_does_not_leak_previous_period() -> None:
    markdown = """
## Số dư với các bên liên quan
| Bên liên quan | Nội dung | Số cuối năm | Số đầu năm |
| :--- | :--- | :--- | :--- |
| Công ty Con ABC | Phải thu về cho vay | - | 50.000.000.000 |
"""
    frames = RptTableParser("TEST").parse_rpt_tables(markdown)
    assert len(frames) == 1
    assert frames[0].amount_vnd == 0.0


def test_rpt_parser_classifies_deposit_or_investment_advance() -> None:
    markdown = """
## Các bên liên quan - giao dịch
| Bên liên quan | Nội dung giao dịch | Số tiền |
| :--- | :--- | :--- |
| Công ty Cổ phần Dự Án X | Đặt cọc hợp đồng hợp tác kinh doanh BCC dự án | 250.000.000.000 |
| Công ty TNHH Đầu Tư Y | Tạm ứng mua cổ phần | 120.000.000.000 |
"""
    frames = RptTableParser("TEST").parse_rpt_tables(markdown)
    assert len(frames) == 2
    assert frames[0].flow_kind == "deposit_or_investment_advance"
    assert frames[1].flow_kind == "deposit_or_investment_advance"


def test_bank_commitments_not_classified_as_rpt_guarantee() -> None:
    result = classify_statement_line("Bảo lãnh vay vốn và các cam kết tín dụng khác", is_bank=True)
    assert result is not None
    fact_type, semantic_key = result
    assert semantic_key == "bank_commitments"
    assert fact_type.value != "guarantee_balance"


def test_rpt_parser_does_not_create_residual_amount_from_breakdown() -> None:
    markdown = """
## 39. Nghiệp vụ và số dư với các bên liên quan
|  | Kỳ này | Kỳ trước |
| :--- | :--- | :--- |
| Cung cấp hàng hóa và dịch vụ |  |  |
| Công ty liên doanh | 61.416.695 | 8.776.343 |
| Trong đó: |  |  |
| - Công ty A | 17.623.979 | 4.627.703 |
| - Công ty B | 16.060.490 | 427.782 |
"""
    frames = RptTableParser("TEST").parse_rpt_tables(markdown)
    amounts = {frame.amount_vnd for frame in frames}
    assert 20_704_454.0 not in amounts
    assert {17_623_979.0, 16_060_490.0} <= amounts


def test_statement_parser_rejects_ocr_glued_numeric_columns() -> None:
    markdown = """
## BẢNG CÂN ĐỐI KẾ TOÁN
| Mã số | CHỈ TIÊU | Số cuối kỳ | Số đầu kỳ |
| :--- | :--- | :--- | :--- |
| 421 | Lợi nhuận sau thuế chưa phân phối | 101.115.680.938411.573.641.082 | 84.871.551.076318.663.100.501 |
"""
    facts = StatementTableParser(markdown, "TEST").parse_statement_facts()
    assert not any(fact["semantic_key"] == "cost_of_goods_sold" for fact in facts)
