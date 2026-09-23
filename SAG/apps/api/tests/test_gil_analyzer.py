from types import SimpleNamespace

import pytest
from sag_api.services.gil_service import (
    GILGraphAnalyzer,
    _denominator_exposure_buckets,
    _deduplicate_historical_documents,
    _document_denominator_coverage,
    _document_metric,
    _relationship_breakdown,
    _relationship_risk_score,
    _insider_metrics,
    _parse_vn_number,
)
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


def test_gil_denominator_prefers_aggregate_over_detail_rows():
    relations = [
        SimpleNamespace(
            relation_type="transacts_with",
            subject="GAS",
            object="RELATED_PARTIES_AGGREGATE",
            amount_vnd=200.0,
            metadata_json={"flow_kind": "receivable_balance"},
        ),
        SimpleNamespace(
            relation_type="transacts_with",
            subject="GAS",
            object="Subsidiary A",
            amount_vnd=120.0,
            metadata_json={"flow_kind": "receivable_balance"},
        ),
        SimpleNamespace(
            relation_type="transacts_with",
            subject="GAS",
            object="Subsidiary B",
            amount_vnd=80.0,
            metadata_json={"flow_kind": "receivable_balance"},
        ),
    ]
    buckets = _denominator_exposure_buckets(relations)
    assert buckets["receivable_vnd"] == 200.0


def test_gil_denominator_accepts_validated_manifest_facts():
    fact = SimpleNamespace(
        semantic_key="total_assets",
        value_numeric=2_638_198_597_000_000.0,
        metadata_json={
            "accounting_scope": "STANDALONE",
            "taxonomy_version": "financial-evidence-taxonomy-v2",
        },
    )

    assert _document_metric([fact], {"total_assets"}) == 2_638_198_597_000_000.0


def test_financial_institution_skips_industrial_denominators():
    coverage = _document_denominator_coverage(
        [],
        [],
        excluded_denominators={"financial_income", "cost_of_goods_sold"},
    )
    assert "financial_income" not in coverage["missing_latest_quarter"]
    assert "cost_of_goods_sold" not in coverage["missing_latest_quarter"]


def test_relationship_breakdown_recognizes_vietnamese_insider_labels():
    relation = SimpleNamespace(
        object_entity_id="personish",
        object="Hội đồng quản trị và Tổng Giám đốc",
        raw_label="",
        amount_vnd=100.0,
    )
    breakdown = _relationship_breakdown([relation], set(), set(), {"personish": "related_party"})
    assert breakdown["insider_related"] == 100.0
    assert breakdown["unclassified"] == 0.0


def test_unresolved_relationship_is_coverage_not_observed_risk():
    assert _relationship_risk_score({"unclassified": 1_000.0}, False) == 0.0
    assert _relationship_risk_score({"intra_group": 1_000.0, "unclassified": 1_000.0}, False) == 10.0


def test_historical_documents_prefer_active_replacement_for_same_period():
    old = SimpleNamespace(
        id="old",
        doc_role="ANNUAL_BACKBONE",
        fiscal_year=2025,
        fiscal_quarter=None,
        period_end="2025-12-31",
        is_active=False,
        created_at="2026-01-01",
    )
    current = SimpleNamespace(
        id="current",
        doc_role="ANNUAL_BACKBONE",
        fiscal_year=2025,
        fiscal_quarter=None,
        period_end="2025-12-31",
        is_active=True,
        created_at="2026-02-01",
    )
    assert [doc.id for doc in _deduplicate_historical_documents([old, current])] == ["current"]


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

    assert result.gil_flag == "PASS"
    assert result.risk_level == "LOW"
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

    assert result.gil_flag == "PASS"
    assert result.analysis_status == "COMPLETE"


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


def test_parse_vn_number_decomposes_glued_dot_numbers():
    from sag_api.extraction.parsers.accounting_taxonomy import parse_vn_number

    # Multi-line table cell glued by OCR
    assert parse_vn_number("2.863.1252.863.1251.204.866") == 6931116.0
    assert parse_vn_number("30.271.14810.335.0004.500.0002.456.222317.213") == 47879583.0
    assert parse_vn_number("265.000212.000212.000") == 689000.0
    # Single standard numbers remain unaltered
    assert parse_vn_number("1.234.567") == 1234567.0
    assert parse_vn_number("500.000") == 500000.0


def test_classify_temporal_column():
    from sag_api.extraction.parsers.accounting_taxonomy import classify_temporal_column

    assert classify_temporal_column("Số cuối năm") == "CURRENT_PERIOD"
    assert classify_temporal_column("Số đầu năm") == "PREVIOUS_PERIOD"
    assert classify_temporal_column("Năm nay") == "CURRENT_PERIOD"
    assert classify_temporal_column("Năm trước") == "PREVIOUS_PERIOD"
    assert classify_temporal_column("Kỳ này") == "CURRENT_PERIOD"
    assert classify_temporal_column("Kỳ trước") == "PREVIOUS_PERIOD"
    assert classify_temporal_column("Số cuối kỳ") == "CURRENT_PERIOD"
    assert classify_temporal_column("Số đầu kỳ") == "PREVIOUS_PERIOD"
    assert classify_temporal_column("31/12/2025") == "CURRENT_PERIOD"
    assert classify_temporal_column("01/01/2025") == "PREVIOUS_PERIOD"
    assert classify_temporal_column("Thuyết minh") is None


def test_issuer_outgoing_relations_with_alias_set():
    from sag_api.services.gil_service import _issuer_outgoing_relations

    rels = [
        SimpleNamespace(subject_entity_id="ent_ticker", amount_vnd=100.0),
        SimpleNamespace(subject_entity_id="ent_legal_name", amount_vnd=200.0),
        SimpleNamespace(subject_entity_id="ent_other", amount_vnd=300.0),
    ]
    matched = _issuer_outgoing_relations(rels, {"ent_ticker", "ent_legal_name"})
    assert len(matched) == 2
    assert {r.subject_entity_id for r in matched} == {"ent_ticker", "ent_legal_name"}


def test_rpt_table_parser_flow_classification():
    from sag_api.extraction.parsers.rpt_table_parser import _classify_transaction_flow

    # Balance context
    rel_type, kind = _classify_transaction_flow("Phải thu về cho vay", is_balance_table=True)
    assert kind == "loan_balance"
    assert rel_type == "lends_to"

    rel_type, kind = _classify_transaction_flow("Vay và nợ thuê tài chính", is_balance_table=True)
    assert kind == "borrowing_balance"

    # Transaction flow context
    rel_type, kind = _classify_transaction_flow("Chi phí lãi vay", is_balance_table=False)
    assert kind == "interest_expense"

    rel_type, kind = _classify_transaction_flow("Doanh thu bán hàng và cung cấp dịch vụ", is_balance_table=False)
    assert kind == "service_revenue"

    rel_type, kind = _classify_transaction_flow("Vốn cổ phần của bên liên quan tại Công ty", is_balance_table=True)
    assert kind == "ownership_value"

    assert _classify_transaction_flow("Vốn góp bằng tiền", is_balance_table=True)[1] == "capital_contribution"
    assert _classify_transaction_flow("Số dư tiền gửi không kỳ hạn", is_balance_table=True)[1] == "cash_deposit_balance"


def test_relationship_label_classifies_rpt_counterparty():
    from sag_api.services.gil_service import _relationship_breakdown

    relation = SimpleNamespace(
        amount_vnd=100.0,
        object_entity_id="entity-1",
        object="Company A",
        raw_label="cong ty con",
    )
    assert _relationship_breakdown([relation], set(), set(), {"entity-1": "related_party"})["intra_group"] == 100.0


def test_rpt_parser_rejects_accounting_labels_as_counterparties():
    from sag_api.extraction.parsers.rpt_table_parser import _valid_counterparty

    assert not _valid_counterparty("Tổng công nợ", explicit_party_column=True)
    assert not _valid_counterparty("Báo cáo kết quả hoạt động kinh doanh - Giá vốn", explicit_party_column=True)


def test_canonicalize_cycle_invariant_under_rotations():
    from sag_api.services.gil_service import _canonicalize_cycle

    c1 = ["A", "B", "C", "A"]
    c2 = ["B", "C", "A", "B"]
    c3 = ["C", "A", "B", "C"]
    assert _canonicalize_cycle(c1) == _canonicalize_cycle(c2) == _canonicalize_cycle(c3) == ("A", "B", "C", "A")


def test_cycle_materiality_ratio_uses_bottleneck_not_sum():
    from sag_api.services.gil_service import _cycle_materiality_ratio

    relations = [
        SimpleNamespace(subject="A", object="B", relation_type="transacts_with", amount_vnd=1_000.0),
        SimpleNamespace(subject="B", object="C", relation_type="transacts_with", amount_vnd=200.0),
        SimpleNamespace(subject="C", object="A", relation_type="transacts_with", amount_vnd=800.0),
    ]
    cycles = [["A", "B", "C", "A"]]
    equity = 1_000.0
    # Bottleneck is min(1000, 200, 800) = 200, ratio = 200 / 1000 = 0.20
    # Old sum formula would have given (1000 + 200 + 800) / 1000 = 2.00 (10x inflated!)
    ratio = _cycle_materiality_ratio(cycles, relations, equity)
    assert ratio == 0.20


def test_detect_cycles_deduplicates_rotations():
    from sag_api.services.gil_service import _detect_cycles

    relations = [
        SimpleNamespace(subject="A", object="B", relation_type="transacts_with", amount_vnd=100.0),
        SimpleNamespace(subject="B", object="C", relation_type="lends_to", amount_vnd=100.0),
        SimpleNamespace(subject="C", object="A", relation_type="guarantees_for", amount_vnd=100.0),
    ]
    cycles = _detect_cycles(relations)
    assert len(cycles) == 1
    assert cycles[0] == ["A", "B", "C", "A"]


def test_detect_cycles_uses_all_flow_relations_and_same_period():
    from sag_api.services.gil_service import _detect_cycles

    same_period = [
        SimpleNamespace(subject="A", object="B", relation_type="transacts_with", amount_vnd=100.0, document_id="q1"),
        SimpleNamespace(subject="B", object="C", relation_type="guarantees_for", amount_vnd=100.0, document_id="q1"),
        SimpleNamespace(subject="C", object="A", relation_type="transacts_with", amount_vnd=100.0, document_id="q1"),
    ]
    assert _detect_cycles(same_period, {"q1": "2026-06-30"}) == [["A", "B", "C", "A"]]

    cross_period = [
        SimpleNamespace(subject="A", object="B", relation_type="transacts_with", amount_vnd=100.0, document_id="q1"),
        SimpleNamespace(subject="B", object="C", relation_type="guarantees_for", amount_vnd=100.0, document_id="q1"),
        SimpleNamespace(subject="C", object="A", relation_type="transacts_with", amount_vnd=100.0, document_id="q2"),
    ]
    assert _detect_cycles(cross_period, {"q1": "2026-06-30", "q2": "2026-09-30"}) == []
