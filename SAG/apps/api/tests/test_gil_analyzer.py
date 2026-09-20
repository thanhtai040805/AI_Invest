from types import SimpleNamespace

import pytest
from sag_api.services.gil_service import GILGraphAnalyzer, _denominator_exposure_buckets, _insider_metrics, _parse_vn_number
from sag_api.services.extraction_v2_service import _classify_table_fact, _table_value_scale


def test_table_value_scale_normalizes_million_vnd_context():
    markdown = "Đơn vị tính: triệu VND\n| A | B |\n|---|---|\n| Vốn chủ sở hữu | 103.843.473 |"
    assert _table_value_scale(markdown, 4, "B", "Vốn chủ sở hữu") == 1_000_000.0


def test_vietnamese_number_separators():
    assert _parse_vn_number("1,5") == 1.5
    assert _parse_vn_number("1.000") == 1000.0
    assert _parse_vn_number("1.234,5") == 1234.5


def test_cash_flow_loan_payment_is_not_cash_balance():
    _fact_type, semantic_key, _taxonomy = _classify_table_fact(
        "", "Tien chi cho vay, mua cong cu no cua don vi khac"
    )
    assert semantic_key != "cash_and_equivalents"


def test_gil_analyzer_clean_company():
    """Kiểm thử công ty bình thường, dòng vốn sản xuất minh bạch (như HPG)."""
    equity = 100_000_000_000_000  # 100 nghìn tỷ VCSH
    analyzer = GILGraphAnalyzer(ticker="HPG", equity_vnd=equity)

    nodes = [
        {"id": "HPG", "name": "CTCP Tập đoàn Hòa Phát", "entity_type": "COMPANY"},
        {"id": "DQ", "name": "CTCP Thép Hòa Phát Dung Quất", "entity_type": "SUBSIDIARY"},
        {"id": "HD", "name": "CTCP Thép Hòa Phát Hải Dương", "entity_type": "SUBSIDIARY"},
    ]

    edges = [
        {"source": "HPG", "target": "DQ", "relation_type": "OWNS", "ownership_pct": 100.0},
        {"source": "HPG", "target": "HD", "relation_type": "OWNS", "ownership_pct": 100.0},
        {"source": "HPG", "target": "DQ", "relation_type": "LOANS_TO", "amount_vnd": 7_154_000_000_000},  # ~7.1 nghìn tỷ
    ]

    analyzer.build_graph(nodes, edges)
    result = analyzer.evaluate()

    assert result.gil_flag == "PASS"
    assert result.risk_level == "LOW"
    assert result.cycles_detected == 0
    assert result.rpt_ratio < 0.25
    assert len(result.cycle_paths) == 0
    print("PASS clean company test:", result.summary)


def test_gil_denominators_use_flow_kind_and_keep_unmapped_flows_out():
    relations = [
        SimpleNamespace(relation_type="transacts_with", amount_vnd=100.0, metadata_json={"flow_kind": "service_revenue"}),
        SimpleNamespace(relation_type="transacts_with", amount_vnd=200.0, metadata_json={"flow_kind": "purchase"}),
        SimpleNamespace(relation_type="invests_in", amount_vnd=300.0, metadata_json={"flow_kind": "capital_contribution"}),
        SimpleNamespace(relation_type="creditor_of", amount_vnd=400.0, metadata_json={"flow_kind": "receivable"}),
        SimpleNamespace(relation_type="lends_to", amount_vnd=500.0, metadata_json={"flow_kind": "loan"}),
    ]
    buckets = _denominator_exposure_buckets(relations)
    assert buckets["service_revenue_vnd"] == 100.0
    assert buckets["capital_allocation_vnd"] == 300.0
    assert buckets["receivable_vnd"] == 400.0
    assert buckets["loan_vnd"] == 500.0
    assert buckets["purchase_vnd"] == 200.0
    assert buckets["non_ratio_flow_vnd"] == 0.0


def test_gil_quantifies_insider_ownership_without_fabricating_transaction_value():
    observations = [
        SimpleNamespace(statement="Chủ tịch và vợ cùng sở hữu 32.68% cổ phần."),
        SimpleNamespace(statement="Chuyển nhượng 5% vốn cho cán bộ quản lý nội bộ."),
    ]
    metrics = _insider_metrics(observations, 100_000.0)
    assert metrics["ownership_pct"] == 32.68
    assert metrics["ownership_exposure_vnd"] == 32680.0
    assert metrics["transaction_exposure_vnd"] is None
    assert metrics["basis"] == "OWNERSHIP_PERCENTAGE"


def test_gil_analyzer_capital_tunneling_cycle():
    """Kiểm thử phát hiện chu trình rút ruột vốn A -> B -> C -> A (FLC / Vạn Thịnh Phát style)."""
    equity = 10_000_000_000_000  # 10 nghìn tỷ VCSH
    analyzer = GILGraphAnalyzer(ticker="BAD_CORP", equity_vnd=equity)

    nodes = [
        {"id": "BAD_CORP", "name": "Công ty Cổ phần Mẹ Rủi ro", "entity_type": "COMPANY"},
        {"id": "SUB_B", "name": "Công ty Con B", "entity_type": "SUBSIDIARY"},
        {"id": "SHELL_C", "name": "Công ty Sân sau C", "entity_type": "SHELL"},
    ]

    edges = [
        # Mẹ cho Con B vay tiền
        {"source": "BAD_CORP", "target": "SUB_B", "relation_type": "LOANS_TO", "amount_vnd": 2_000_000_000_000},
        # Con B chuyển tiền cho Sân sau C dưới dạng hợp tác đầu tư / công nợ
        {"source": "SUB_B", "target": "SHELL_C", "relation_type": "RECEIVABLE_FROM", "amount_vnd": 1_800_000_000_000},
        # Sân sau C dùng tiền đó mua cổ phần / tăng vốn ảo tại Mẹ BAD_CORP (Tạo chu trình)
        {"source": "SHELL_C", "target": "BAD_CORP", "relation_type": "OWNS", "ownership_pct": 25.0},
    ]

    analyzer.build_graph(nodes, edges)
    result = analyzer.evaluate()

    assert result.gil_flag == "CATASTROPHIC"
    assert result.risk_level == "CRITICAL"
    assert result.cycles_detected >= 1
    assert any("BAD_CORP" in p and "SUB_B" in p and "SHELL_C" in p for p in result.cycle_paths)
    print("PASS cycle detection test:", result.summary)


def test_gil_analyzer_high_exposure_without_cycle():
    """Exposure cao nhưng không có cycle chỉ yêu cầu review, không catastrophic."""
    equity = 10_000_000_000_000  # 10 nghìn tỷ
    analyzer = GILGraphAnalyzer(ticker="OVER_LEVERAGED", equity_vnd=equity)

    nodes = [
        {"id": "OVER_LEVERAGED", "name": "Công ty Mẹ", "entity_type": "COMPANY"},
        {"id": "SUB_1", "name": "Công ty Con 1", "entity_type": "SUBSIDIARY"},
    ]

    edges = [
        {"source": "OVER_LEVERAGED", "target": "SUB_1", "relation_type": "OWNS", "ownership_pct": 100.0},
        # Đem 6 nghìn tỷ đi bảo lãnh nợ (chiếm 60% vốn chủ sở hữu)
        {"source": "OVER_LEVERAGED", "target": "SUB_1", "relation_type": "GUARANTEES_FOR", "amount_vnd": 6_000_000_000_000},
    ]

    analyzer.build_graph(nodes, edges)
    result = analyzer.evaluate()

    assert result.gil_flag == "WARNING"
    assert result.risk_level == "HIGH"
    assert result.rpt_ratio == 0.60
    assert result.cycles_detected == 0
    print("PASS high exposure test:", result.summary)


def test_gil_missing_equity_is_data_insufficient():
    analyzer = GILGraphAnalyzer(ticker="HPG", equity_vnd=0)
    analyzer.build_graph(
        [{"id": "HPG", "name": "HPG", "entity_type": "TICKER"}],
        [{"source": "HPG", "target": "SUB", "relation_type": "LOANS_TO", "amount_vnd": 1_000_000_000, "verified": True}],
    )

    result = analyzer.evaluate()

    assert result.gil_flag == "DATA_INSUFFICIENT"
    assert result.analysis_status == "DATA_INSUFFICIENT"


def test_gil_transaction_and_guarantee_do_not_create_capital_cycle():
    analyzer = GILGraphAnalyzer(ticker="AAA", equity_vnd=10_000_000_000)
    analyzer.build_graph(
        [{"id": "AAA"}, {"id": "BBB"}],
        [
            {"source": "AAA", "target": "BBB", "relation_type": "TRANSACTS_WITH", "amount_vnd": 1_000_000_000, "verified": True},
            {"source": "BBB", "target": "AAA", "relation_type": "GUARANTEES_FOR", "amount_vnd": 1_000_000_000, "verified": True},
        ],
    )

    result = analyzer.evaluate()

    assert result.cycles_detected == 0
    assert result.gil_flag == "PASS"


def test_gil_unverified_edges_ignored():
    analyzer = GILGraphAnalyzer(ticker="AAA", equity_vnd=10_000_000_000)
    analyzer.build_graph(
        [{"id": "AAA"}, {"id": "BBB"}],
        [
            {"source": "AAA", "target": "BBB", "relation_type": "LOANS_TO", "amount_vnd": 9_000_000_000, "verified": False},
            {"source": "BBB", "target": "AAA", "relation_type": "OWNS", "ownership_pct": 30.0, "verified": False},
        ],
    )

    result = analyzer.evaluate()

    assert result.cycles_detected == 0
    assert result.total_rpt_exposure_vnd == 0
    assert result.gil_flag == "PASS"
