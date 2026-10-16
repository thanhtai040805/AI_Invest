from app.domain.rules.thesis_financial_evidence import (
    INDUSTRY_GUIDANCE,
    SECTOR_GUIDANCE,
    build_core_metrics,
    build_financial_evidence,
    latest_aligned_package,
    normalize_label,
    sector_guidance,
)
from app.domain.repositories.financial_repository import FinancialRepository
from datetime import datetime, timezone


DB_INDUSTRIES = {
    "Chế biến Thủy sản", "Nông - Lâm - Ngư", "Ngân hàng", "Bán buôn", "Khai khoáng",
    "Sản phẩm cao su", "Vật liệu xây dựng", "SX Nhựa - Hóa chất", "Dịch vụ tư vấn, hỗ trợ",
    "Xây dựng", "Xây dựng điện & Hạ tầng", "Dịch vụ lưu trú, ăn uống, giải trí", "Bảo hiểm",
    "Chứng khoán", "Tài chính khác", "Thực phẩm - đồ uống", "Chăm sóc sức khỏe", "Dệt may",
    "SX Hàng gia dụng", "SX Phụ trợ", "SX Thiết bị, máy móc", "Thiết bị điện", "Tiện ích",
    "Truyền thông & Quảng cáo", "Bất động sản", "Bán lẻ", "Công nghệ và thông tin",
    "Truyền thông số & Giải trí", "Vận tải - kho bãi",
}
DB_SECTORS = {
    "AGRICULTURE", "BANKS", "BASIC_RESOURCES", "CHEMICALS", "CONSTRUCTION",
    "CONSTRUCTION_MATERIALS", "CONSUMER_SERVICES", "FINANCIAL_SERVICES",
    "FOOD_BEVERAGE", "HEALTHCARE", "INDUSTRIAL_GOODS", "OIL_GAS",
    "OTHER_INDUSTRIALS", "REAL_ESTATE", "RETAIL_TRADE", "TECHNOLOGY",
    "TRANSPORTATION", "UTILITIES",
}


def row(statement_type, period_end, data, frequency="quarterly", source="alphastock", fetched_at="2026-10-10T10:00:00+07:00"):
    return {
        "statement_type": statement_type, "period_end": period_end, "frequency": frequency,
        "data": data, "source": source, "fetched_at": fetched_at, "published_date": "2026-10-10",
    }


def test_sector_and_industry_guidance_covers_current_database_taxonomy():
    assert set(SECTOR_GUIDANCE) == DB_SECTORS
    assert {normalize_label(value) for value in DB_INDUSTRIES} <= set(INDUSTRY_GUIDANCE)
    assert "Ngành chưa phân loại trong DB" in sector_guidance(None, "")


def test_evidence_uses_latest_statement_rows_but_only_aligned_package_for_cross_statement_context():
    statements = [
        row("IS", "2026-03-31", {"3. Doanh thu thuần": 8_000_000_000, "18. Lợi nhuận sau thuế": 1_000_000_000}),
        row("BS", "2026-03-31", {"1. Tiền": 1_000_000_000, "Vay và nợ thuê tài chính ngắn hạn": 500_000_000, "Vay và nợ thuê tài chính dài hạn": 1_000_000_000}),
        row("CF", "2026-03-31", {
            "tiền thuần từ hđkd": 1_000_000_000,
            "1. Tiền chi để mua sắm, xây dựng TSCĐ và các tài sản dài hạn khác": -500_000_000,
        }),
        row("BS", "2026-06-30", {"1. Tiền": 3_000_000_000, "Vay và nợ thuê tài chính ngắn hạn": 2_000_000_000, "Vay và nợ thuê tài chính dài hạn": 1_000_000_000}),
    ]
    result = build_financial_evidence({
        "sector": "TECHNOLOGY", "industry": "Công nghệ và thông tin", "as_of": "2026-10-10",
        "observed_at": "2026-10-10T09:45:00+07:00", "statements": statements,
    })
    assert latest_aligned_package(statements)["IS"]["period_end"] == "2026-03-31"
    assert result["aligned"] is False
    assert result["latest_by_type"]["BS"]["period_end"] == "2026-06-30"
    assert result["metrics"]["cfo"]["source_key"] == "tiền thuần từ hđkd"
    assert result["metrics"]["capex"]["value"] == -500_000_000
    assert result["metrics"]["fcf"]["value"] == 500_000_000
    assert result["metrics"]["net_debt"]["value"] == 0
    assert "BCTC mới nhất BS: kỳ=2026-06-30" in result["formatted"]
    assert "Bộ IS/BS/CF đồng kỳ gần nhất: 2026-03-31 quarterly" in result["formatted"]
    assert "BCTC mới nhất theo từng loại lệch kỳ" in result["formatted"]


def test_financial_sector_does_not_receive_generic_gpm_or_cash_flow_metrics():
    result = build_financial_evidence({
        "sector": "BANKS", "industry": "Ngân hàng", "as_of": "2026-10-10",
        "observed_at": "2026-10-10T09:45:00+07:00",
        "statements": [
            row("IS", "2026-06-30", {
                "I. Thu nhập lãi thuần": 3_000_000_000,
                "X. Chi phí dự phòng rủi ro tín dụng": 200_000_000,
                "18. Lợi nhuận sau thuế thu nhập doanh nghiệp": 1_000_000_000,
            }),
            row("BS", "2026-06-30", {"VI. Cho vay khách hàng": 50_000_000_000, "VII. Tiền gửi khách hàng": 60_000_000_000}),
            row("CF", "2026-06-30", {"Lưu chuyển tiền thuần từ hoạt động kinh doanh": 10_000_000_000}),
        ],
    })
    assert set(result["metrics"]) == {"net_interest_income", "credit_provisions", "net_income", "customer_loans", "customer_deposits"}
    assert "Không dùng GPM" in result["guidance"]
    metrics = build_core_metrics({}, {}, {
        "sector": "BANKS", "ratios": {"gross_margin": 0.4, "roe": 0.12},
        "ratio_meta": {"ratio_date": "2026-06-30", "frequency": "quarterly"},
    })
    assert metrics["gpm"] is None
    assert metrics["_gpm_not_applicable"] is True
    assert metrics["roe"] == 12.0


def test_broker_and_insurance_use_their_own_available_bctc_lines():
    broker = build_financial_evidence({
        "sector": "FINANCIAL_SERVICES", "industry": "Chứng khoán", "statements": [
            row("IS", "2026-06-30", {
                "1.6. Doanh thu môi giới chứng khoán": 100_000_000_000,
                "1.1. Lãi từ các tài sản tài chính ghi nhận thông qua lãi/lỗ (FVTPL)": 20_000_000_000,
                "2.1. Lỗ các tài sản tài chính ghi nhận thông qua lỗ (FVTPL)": 3_000_000_000,
                "Lợi nhuận sau thuế": 10_000_000_000,
            }),
            row("BS", "2026-06-30", {"4. Các khoản cho vay": 50_000_000_000}),
        ],
    })
    insurance = build_financial_evidence({
        "sector": "FINANCIAL_SERVICES", "industry": "Bảo hiểm", "statements": [
            row("IS", "2026-06-30", {"Thu phí bảo hiểm gốc": 200_000_000_000, "Chi bồi thường": 80_000_000_000, "Lợi nhuận sau thuế": 8_000_000_000}),
        ],
    })
    assert {"brokerage_revenue", "proprietary_trading_gains", "proprietary_trading_losses", "broker_loans", "net_income"} <= set(broker["metrics"])
    assert "margin_lending" not in broker["metrics"]
    assert {"insurance_premiums", "insurance_claims", "net_income"} <= set(insurance["metrics"])
    assert "cfo" not in broker["metrics"] and "cfo" not in insurance["metrics"]


def test_database_ratio_fractions_are_converted_and_report_values_take_precedence():
    evidence = {
        "sector": "INDUSTRIAL_GOODS",
        "ratios": {"pe": 12.0, "pb": 1.1, "roe": 0.062, "gross_margin": 0.24, "yoy_earnings_growth": -0.15},
        "ratio_meta": {"ratio_date": "2026-06-30", "frequency": "quarterly", "updated_at": "2026-10-01"},
    }
    metrics = build_core_metrics({"roe": 18.0}, {}, evidence)
    assert metrics["pe"] == 12.0 and metrics["pb"] == 1.1
    assert metrics["roe"] == 18.0
    assert metrics["gpm"] == 24.0 and metrics["earnings_growth"] == -15.0
    assert "financial_ratios" in metrics["_sources"]["gpm"]
    assert metrics["_sources"]["roe"] == "Agent-03/event input"


def test_nonfinancial_evidence_includes_interest_and_ratio_inputs_without_faking_net_debt_ebitda():
    result = build_financial_evidence({
        "sector": "UTILITIES", "industry": "Tiện ích", "statements": [
            row("IS", "2026-06-30", {
                "Doanh thu thuần": 20_000_000_000,
                "Lợi nhuận sau thuế": 2_000_000_000,
                "2. Chi phí lãi vay": 500_000_000,
            }),
            row("BS", "2026-06-30", {
                "Tiền và các khoản tương đương tiền": 1_000_000_000,
                "Vay và nợ thuê tài chính ngắn hạn": 2_000_000_000,
                "Vay và nợ thuê tài chính dài hạn": 3_000_000_000,
                "Tài sản ngắn hạn": 8_000_000_000,
                "Nợ ngắn hạn": 6_000_000_000,
            }),
            row("CF", "2026-06-30", {
                "Tiền thuần từ HĐKD": 3_000_000_000,
                "Tiền chi để mua sắm, xây dựng TSCĐ và các tài sản dài hạn khác": -1_000_000_000,
                "Tiền lãi vay đã trả": 450_000_000,
            }),
        ],
        "ratios": {
            "ratio_date": "2026-06-30", "frequency": "quarterly", "updated_at": "2026-10-01",
            "current_ratio": 1.4, "debt_equity": 0.5, "ev_ebitda": 5.5,
        },
    })
    assert result["metrics"]["interest_expense"]["value"] == 500_000_000
    assert result["metrics"]["interest_paid"]["value"] == 450_000_000
    assert result["metrics"]["net_debt"]["value"] == 4_000_000_000
    assert "Current ratio theo financial_ratios: 1.40x" in result["formatted"]
    assert "Debt/equity theo financial_ratios: 0.50x" in result["formatted"]
    assert "Định nghĩa debt/equity không được lưu" in result["formatted"]
    assert "5.50x" not in result["formatted"]
    assert "không tính Net Debt/EBITDA" in result["formatted"]


def test_empty_point_in_time_snapshot_still_supplies_sector_guidance_and_observed_ratios():
    result = build_financial_evidence({
        "sector": "BANKS", "industry": "Ngân hàng", "as_of": "2026-06-30",
        "observed_at": "2026-07-01T09:45:00+07:00", "statements": [],
        "ratios": {"ratio_date": "2026-06-30", "frequency": "quarterly", "updated_at": "2026-07-01T08:00:00+07:00", "roe": 0.12},
    })
    assert result["metrics"] == {}
    assert "Hướng phân tích ngành: Ngân hàng" in result["formatted"]
    assert "BCTC mới nhất IS: Chưa có trong dữ liệu." in result["formatted"]
    assert result["ratio_meta"]["ratio_date"] == "2026-06-30"


def test_consulting_industry_does_not_report_inventory_or_project_advances_as_missing():
    result = build_financial_evidence({
        "sector": "CONSTRUCTION", "industry": "Dịch vụ tư vấn, hỗ trợ", "statements": [],
    })
    missing_line = next(line for line in result["formatted"].splitlines() if line.startswith("Chỉ tiêu thuộc hồ sơ ngành"))
    assert "Hàng tồn kho" not in missing_line
    assert "Khách hàng trả tiền trước" not in missing_line


def test_repository_snapshot_filters_by_database_observation_time_not_statutory_deadlines():
    class Storage:
        def __init__(self):
            self.calls = []

        def fetch_all(self, query, params):
            self.calls.append((query, params))
            if "FROM stocks" in query:
                return [("TECHNOLOGY", "Công nghệ và thông tin")]
            return []

    storage = Storage()
    cutoff = datetime(2026, 10, 10, 2, 45, tzinfo=timezone.utc)
    snapshot = FinancialRepository(storage=storage).get_thesis_financial_snapshot("fpt", cutoff)
    statement_query, ratio_query = storage.calls[1][0], storage.calls[2][0]
    assert "fetched_at <= %s" in statement_query and "period_end <= %s" in statement_query
    assert "updated_at <= %s" in ratio_query and "ratio_date <= %s" in ratio_query
    assert "published_date" not in statement_query.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert "published_date" not in ratio_query.split("WHERE", 1)[1].split("ORDER BY", 1)[0]
    assert storage.calls[1][1][:2] == ("FPT", datetime(2026, 10, 10).date())
    assert storage.calls[1][1][2].astimezone(timezone.utc) == cutoff
    assert datetime.fromisoformat(snapshot["observed_at"]).astimezone(timezone.utc) == cutoff
