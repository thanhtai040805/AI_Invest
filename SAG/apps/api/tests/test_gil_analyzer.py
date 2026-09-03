import pytest
from sag_api.services.gil_service import GILGraphAnalyzer


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
    """Kiểm thử công ty không có chu trình nhưng nợ bảo lãnh vượt 50% vốn CSH."""
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

    assert result.gil_flag == "CATASTROPHIC"
    assert result.risk_level == "CRITICAL"
    assert result.rpt_ratio == 0.60
    assert result.cycles_detected == 0
    print("PASS high exposure test:", result.summary)
