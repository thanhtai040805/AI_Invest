"""Unit Test Suite: Agent 06 Portfolio Risk Agent vNext (Vietnamized Institutional Risk Engine).
100% Cổ phiếu cơ sở (Spot Equity) — Sàn HOSE — Không sử dụng Phái sinh.
"""

import pytest
from app.domain.agents.portfolio_risk import PortfolioRiskAgent
from app.domain.rules.hard_laws import HardLawEngine, ProposedOrder, PortfolioState
from app.domain.rules.risk.tape_anomaly_detector import TapeAnomalyDetector, TapeAnomalySeverity, AnomalyType
from app.domain.rules.risk.t25_exposure_manager import T25ExposureManager
from app.domain.rules.risk.breadth_risk_engine import BreadthRiskEngine, BreadthHealthTier
from app.domain.rules.risk.tail_risk_engine import TailRiskEngine
from app.domain.rules.risk.drawdown_recovery_protocol import DrawdownRecoveryProtocol, DrawdownTier
from app.domain.rules.risk.cdc_controller import CDCController, CDCTier


@pytest.fixture
def risk_agent():
    return PortfolioRiskAgent()


def test_hard_laws_and_t25_floor_gap():
    """Kiểm tra Hard Laws Điều 1, 2, 4 và rủi ro kẹt 2 cây sàn T+2.5 (-13.51%)."""
    hl_engine = HardLawEngine()
    
    portfolio = PortfolioState(
        nav=1_000_000_000.0,
        positions={},
        sector_exposure={},
        locked_t25_value=0.0,
    )
    
    # 1. Thử lệnh vi phạm Điều 1 (Rủi ro T+2.5 vượt 2% NAV = 20M)
    order_excess_risk = ProposedOrder(
        ticker="FPT",
        side="BUY",
        quantity=2000,
        price=100000.0,  # 200M VND -> 200M * 13.51% = 27.02M > 20M
        stop_loss_price=95000.0,
        sector="Technology",
    )
    res_risk = hl_engine.check_order(order_excess_risk, portfolio, adtv20_continuous=2_000_000)
    assert not res_risk.passed
    assert "T+2.5 Floor Gap" in res_risk.reason

    # 2. Thử lệnh vi phạm Single Stock Limit Điều 4 (> 15% NAV) khi đã nắm giữ sẵn 10%
    portfolio_with_pos = PortfolioState(
        nav=1_000_000_000.0,
        positions={"FPT": {"quantity": 1000, "current_price": 100000.0}},  # 100M = 10% NAV
        sector_exposure={"Technology": 100_000_000.0},
        locked_t25_value=0.0,
    )
    order_excess_stock = ProposedOrder(
        ticker="FPT",
        side="BUY",
        quantity=1000,  # Mua thêm 100M -> Tổng 200M = 20% NAV > 15%
        price=100000.0,
        stop_loss_price=99000.0,  # Rủi ro 13.51% * 100M = 13.51M < 20M
        sector="Technology",
    )
    res_stock = hl_engine.check_order(order_excess_stock, portfolio_with_pos, adtv20_continuous=2_000_000)
    assert not res_stock.passed
    assert "15%" in res_stock.reason

    # 2. Thử lệnh vi phạm Luật Thanh khoản: Lệnh > 15% ADTV20
    order_excess_adtv = ProposedOrder(
        ticker="FPT",
        side="BUY",
        quantity=400_000,  # 400k > 15% của 2M = 300k
        price=200.0,
        stop_loss_price=190.0,
        sector="Technology",
    )
    res_adtv = hl_engine.check_order(order_excess_adtv, portfolio, adtv20_continuous=2_000_000)
    assert not res_adtv.passed
    assert "15% ADTV20" in res_adtv.reason

    # 3. Thử lệnh hợp lệ
    order_valid = ProposedOrder(
        ticker="FPT",
        side="BUY",
        quantity=1000,
        price=100000.0,  # 100M VND = 10% NAV
        stop_loss_price=95000.0,
        sector="Technology",
    )
    res_valid = hl_engine.check_order(order_valid, portfolio, adtv20_continuous=2_000_000)
    assert res_valid.passed


def test_t25_exposure_manager():
    """Kiểm tra khống chế trần hàng kẹt T+2.5 (<= 35% NAV)."""
    t25_mgr = T25ExposureManager(max_locked_t25_pct=35.0)
    
    nav = 1_000_000_000.0
    # Đã có 300M hàng kẹt T+2.5 (30% NAV). Mua thêm 100M -> 40% NAV -> Vượt trần 35%
    res = t25_mgr.check_t25_capacity(
        nav=nav,
        locked_t25_value=300_000_000.0,
        proposed_order_value=100_000_000.0,
        price=100_000.0,
        stop_loss_price=93_000.0,
    )
    assert not res.passed
    assert "Trần Hàng Kẹt T+2.5" in res.reason


def test_tape_anomaly_detector_vsa():
    """Kiểm tra cảm biến dị thường Giá & Volume (VSA) để rút vốn sớm."""
    detector = TapeAnomalyDetector()
    ma20_vol = 1_000_000.0

    # 1. Nến Churning Distribution: Vol 2.5x MA20, biên độ giá hẹp (0.8%)
    candle_churning = {
        "open": 50000.0,
        "high": 50400.0,
        "low": 50000.0,
        "close": 50200.0,
        "volume": 2_500_000.0,
    }
    res_churn = detector.analyze_candle(candle_churning, ma20_vol)
    assert res_churn.has_anomaly
    assert res_churn.anomaly_type == AnomalyType.CHURNING_DISTRIBUTION
    assert res_churn.action_recommended == "BLOCK_BUY"

    # 2. Nến Bearish Upthrust: Râu nến trên dài > 50% kèm Vol 2.0x MA20
    candle_upthrust = {
        "open": 50000.0,
        "high": 53000.0,  # Kéo lên 53k rồi bị đạp về 50.5k
        "low": 49800.0,
        "close": 50500.0,
        "volume": 2_000_000.0,
    }
    res_upthrust = detector.analyze_candle(candle_upthrust, ma20_vol)
    assert res_upthrust.has_anomaly
    assert res_upthrust.anomaly_type == AnomalyType.BEARISH_UPTHRUST
    assert res_upthrust.action_recommended == "BLOCK_BUY"

    # 3. Nến bình thường
    candle_normal = {
        "open": 50000.0,
        "high": 51500.0,
        "low": 49500.0,
        "close": 51200.0,
        "volume": 900_000.0,
    }
    res_norm = detector.analyze_candle(candle_normal, ma20_vol)
    assert not res_norm.has_anomaly
    assert res_norm.action_recommended == "PASS"


def test_breadth_and_distribution_days():
    """Kiểm tra đếm phiên phân phối và độ rộng sàn HOSE."""
    breadth_engine = BreadthRiskEngine()

    # Tạo chuỗi 5 phiên phân phối (giảm >= 0.2% kèm Vol tăng)
    candles = [
        {"close": 1200, "volume": 100},
        {"close": 1195, "volume": 120},  # Dist 1
        {"close": 1198, "volume": 90},
        {"close": 1190, "volume": 130},  # Dist 2
        {"close": 1185, "volume": 140},  # Dist 3
        {"close": 1180, "volume": 150},  # Dist 4
        {"close": 1175, "volume": 160},  # Dist 5
    ]
    dist_count = breadth_engine.count_distribution_days(candles)
    assert dist_count >= 5

    eval_res = breadth_engine.evaluate_market_breadth(
        distribution_days=dist_count,
        breadth_ma20_pct=25.0,
    )
    assert eval_res.health_tier == BreadthHealthTier.CRITICAL_DISTRIBUTION
    assert eval_res.action_recommended == "BLOCK_BUY"
    assert eval_res.recommended_min_cash_pct >= 60.0


def test_tail_risk_engine():
    """Kiểm tra đo lường rủi ro đuôi 3 tầng (Historical ES, EGARCH-t, Stress ES)."""
    tail_engine = TailRiskEngine()
    
    returns = [-0.02, 0.01, -0.015, 0.005, -0.03, -0.065, 0.01, -0.025]
    snapshot = tail_engine.evaluate_tail_risk(
        returns_series=returns,
        portfolio_positions={"FPT": {"weight": 0.15}},
        market_beta=1.15,
    )
    assert snapshot.historical_es_97_5 > 0.0
    assert snapshot.egarch_student_t_es > 0.0
    assert snapshot.stress_es > 0.0
    assert "margin_contagion_shock" in snapshot.stress_details


def test_drawdown_recovery_protocol():
    """Kiểm tra Drawdown Tiers và cơ chế Re-risking chậm."""
    dd_protocol = DrawdownRecoveryProtocol()
    
    # 1. NAV sụt 6% -> Tầng YELLOW -> Co giãn vị thế 0.75 (-25%)
    res_yellow = dd_protocol.evaluate_drawdown(current_nav=940_000_000, peak_nav=1_000_000_000)
    assert res_yellow.tier == DrawdownTier.YELLOW
    assert res_yellow.exposure_scale_factor == 0.75

    # 2. NAV sụt 12% -> Tầng ORANGE -> Co giãn vị thế 0.50 (-50%)
    res_orange = dd_protocol.evaluate_drawdown(current_nav=880_000_000, peak_nav=1_000_000_000)
    assert res_orange.tier == DrawdownTier.ORANGE
    assert res_orange.exposure_scale_factor == 0.50

    # 3. NAV sụt 16% -> Tầng RED -> Co giãn vị thế 0.25 (-75%)
    res_red = dd_protocol.evaluate_drawdown(current_nav=840_000_000, peak_nav=1_000_000_000)
    assert res_red.tier == DrawdownTier.RED
    assert res_red.exposure_scale_factor == 0.25


def test_portfolio_risk_agent_full_gateway():
    """Kiểm thử tích hợp Agent 06 làm Cổng thẩm định Sovereign Pre-Trade Gateway."""
    import asyncio
    
    async def _test():
        risk_agent = PortfolioRiskAgent()
        
        # Case 1: Lệnh hoàn hảo được phê chuẩn (PASS)
        event_pass = {
            "portfolio": {
                "total_nav": 1_000_000_000.0,
                "peak_nav": 1_000_000_000.0,
                "positions": {},
                "sector_exposure": {},
                "locked_t25_value": 0.0,
            },
            "proposed_order": {
                "ticker": "FPT",
                "side": "BUY",
                "quantity": 1000,
                "price": 100000.0,  # 100M VND = 10% NAV
                "stop_loss_price": 93000.0,
                "sector": "Technology",
                "adtv20": 2_000_000.0,
            },
            "market_context": {
                "distribution_days": 1,
                "breadth_ma20_pct": 65.0,
            },
        }
        res_pass = await risk_agent.process(event_pass)
        data_pass = res_pass["data"]
        assert data_pass["risk_status"] == "PASS"
        assert data_pass["decision"]["action"] == "PASS"
        assert data_pass["decision"]["approved_shares"] == 1000
        assert "governance" in data_pass
        assert "asset_scope" in data_pass["governance"]
        assert "NO DERIVATIVES" in data_pass["governance"]["asset_scope"]

        # Case 2: Lệnh bị hạ quy mô (REDUCE) do danh mục đang Drawdown 6% (YELLOW)
        event_reduce = {
            "portfolio": {
                "total_nav": 940_000_000.0,  # Drawdown 6%
                "peak_nav": 1_000_000_000.0,
                "positions": {},
                "sector_exposure": {},
                "locked_t25_value": 50_000_000.0,
            },
            "proposed_order": {
                "ticker": "MWG",
                "side": "BUY",
                "quantity": 2000,
                "price": 50000.0,  # 100M VND
                "stop_loss_price": 46500.0,
                "sector": "Retail",
                "adtv20": 3_000_000.0,
            },
            "market_context": {
                "distribution_days": 2,
                "breadth_ma20_pct": 50.0,
            },
        }
        res_reduce = await risk_agent.process(event_reduce)
        data_reduce = res_reduce["data"]
        assert data_reduce["risk_status"] == "REDUCE"
        assert data_reduce["decision"]["action"] == "REDUCE"
        assert data_reduce["decision"]["approved_shares"] < 2000
        assert data_reduce["decision"]["approved_shares"] == 1500  # -25% do Yellow Tier

        # Case 3: Lệnh bị chặn hoàn toàn (BLOCK) do phát hiện VSA Churning (Phân phối ngầm)
        event_block_vsa = {
            "portfolio": {
                "total_nav": 1_000_000_000.0,
                "peak_nav": 1_000_000_000.0,
                "positions": {},
                "sector_exposure": {},
                "locked_t25_value": 0.0,
            },
            "proposed_order": {
                "ticker": "NVL",
                "side": "BUY",
                "quantity": 1000,
                "price": 15000.0,
                "stop_loss_price": 13900.0,
                "sector": "RealEstate",
                "adtv20": 1_000_000.0,
                "candle": {
                    "open": 15000.0,
                    "high": 15100.0,
                    "low": 15000.0,
                    "close": 15050.0,
                    "volume": 3_000_000.0,  # Vol gấp 3x nhưng giá đi ngang -> Churning
                },
                "ma20_volume": 1_000_000.0,
            },
        }
        res_block = await risk_agent.process(event_block_vsa)
        data_block = res_block["data"]
        assert data_block["risk_status"] == "BLOCK"
        assert data_block["decision"]["approved_shares"] == 0
        assert data_block["tape_anomaly"]["detected"] is True

    asyncio.run(_test())
