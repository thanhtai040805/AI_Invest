"""Test Suite: Agent-09 Position Monitoring & Stop-Loss Engine (Senior Broker Edition - Chuẩn HOSE).
Kiểm thử toàn diện các lớp phòng thủ:
- Tầng 0: T+2.5 Settlement & Floor Lock (Múa bên trăng)
- Tầng 1: Hard Stop tối cao (-2% NAV) ưu tiên số 1, không bị Time Stop cướp quyền
- Tầng 2: Trailing Stop khóa lợi nhuận khi tụt đỉnh
- Tầng 3: Structural Exit thủng Swing Low
- Tầng 4: Fast Exit VSA lô chẵn 100 HOSE
- Tầng 5: Time Stop chôn vốn
- Watchdog Invalidation SLA trước 14:00 vs sau 14:00
- Failsafe tự động bán phòng thủ
"""

import asyncio
import pytest
from app.domain.rules.stop_loss import StopLossEngine, StopLossOrder
from app.domain.agents.position_monitoring import PositionMonitoringAgent
from app.domain.repositories.portfolio_repository import PortfolioRepository


def test_priority_hard_stop_overrides_time_stop():
    """Kiểm tra Hard Stop (-2% NAV) phải luôn được ưu tiên số 1, không bị Time Stop cướp quyền."""
    engine = StopLossEngine()
    nav = 1_000_000_000.0  # 1 tỷ VNĐ
    entry_price = 30_000.0
    current_price = 24_000.0  # Lỗ -20%
    quantity = 10_000  # 10.000 cổ -> Lỗ 60.000.000đ = -6% NAV (vượt xa -2% NAV)

    # Giả sử cầm 60 ngày (> 50% của timeline 90 ngày), pnl_pct âm < 0.02
    market_data = {
        "days_held": 60,
        "expected_timeline_days": 90,
        "available_shares": 10_000,
    }

    order = engine.check_position(
        ticker="HPG",
        quantity=quantity,
        entry_price=entry_price,
        current_price=current_price,
        nav=nav,
        market_data=market_data,
    )

    assert order is not None
    # BẮT BUỘC là HARD_STOP chứ không được là TIME_STOP!
    assert order.rule_level == "HARD_STOP"
    assert order.suggested_action == "SELL_ALL"
    assert order.urgency == "EMERGENCY"
    assert order.quantity == 10_000


def test_t25_locked_shares_prevents_invalid_sell():
    """Kiểm tra cổ phiếu chưa về T+2.5 (available = 0) không được sinh lệnh bán khống ra sàn."""
    engine = StopLossEngine()
    nav = 1_000_000_000.0
    entry_price = 30_000.0
    current_price = 25_000.0  # Lỗ -50tr = -5% NAV
    quantity = 10_000

    # Hàng T+1 chưa về: available_shares = 0
    order = engine.check_position(
        ticker="HPG",
        quantity=quantity,
        entry_price=entry_price,
        current_price=current_price,
        nav=nav,
        available_shares=0,
    )

    assert order is not None
    assert order.rule_level == "T25_LOCKED"
    assert order.quantity == 0  # Không bán vì số dư khả dụng = 0
    assert order.suggested_action == "WAIT_T25_SETTLEMENT"
    assert order.urgency == "CRITICAL_T25_LOCKED"


def test_hose_lot_100_rounding():
    """Kiểm tra làm tròn lô chẵn 100 của HOSE khi bán 50% vị thế lẻ."""
    engine = StopLossEngine()
    # 1.550 cổ phiếu -> bán 50% = 775 -> phải làm tròn xuống 700
    rounded = engine.round_hose_lot(775)
    assert rounded == 700

    # Test với nến Bearish Rejection (Fast Exit)
    nav = 1_000_000_000.0
    order = engine.check_position(
        ticker="SSI",
        quantity=1550,
        entry_price=30_000.0,
        current_price=30_000.0,
        nav=nav,
        available_shares=1550,
        market_data={
            "current_candle": {
                "open": 30_000,
                "close": 30_200,
                "high": 31_500,  # Râu trên = 1.300 / 1.500 = 86.6% > 50%
                "low": 30_000,
                "volume": 3_000_000,
            },
            "ma20_volume": 1_000_000,  # Vol gấp 3 lần MA20
        },
    )
    assert order is not None
    assert order.rule_level == "FAST_EXIT"
    assert order.suggested_action == "REDUCE_50_PCT"
    assert order.quantity == 700  # Đã làm tròn lô chẵn 100!


def test_trailing_stop_lock_profit():
    """Kiểm tra Trailing Stop khóa lợi nhuận khi cổ phiếu tụt đỉnh >= 35%."""
    engine = StopLossEngine()
    nav = 1_000_000_000.0
    entry_price = 20_000.0
    peak_price = 26_000.0  # Lãi đỉnh +30% (lãi 6.000đ/cp)
    current_price = 23_500.0  # Tụt về 23.500đ (đã mất 2.500 / 6.000 = 41.7% lãi đỉnh)

    order = engine.check_position(
        ticker="HPG",
        quantity=5000,
        entry_price=entry_price,
        current_price=current_price,
        nav=nav,
        available_shares=5000,
        market_data={"peak_price": peak_price},
    )

    assert order is not None
    assert order.rule_level == "TRAILING_STOP"
    assert order.suggested_action == "LOCK_PROFIT"
    assert order.urgency == "HIGH"
    assert order.quantity == 5000


def test_floor_lock_unfilled_reset():
    """Kiểm tra mã bị Múa bên trăng (Floor Lock) lệnh cũ không khớp sẽ được reset tính toán lại."""
    engine = StopLossEngine()
    nav = 1_000_000_000.0

    order = engine.check_position(
        ticker="NVL",
        quantity=10_000,
        entry_price=15_000.0,
        current_price=13_950.0,  # Giá sàn
        nav=nav,
        available_shares=10_000,
        market_data={
            "is_floor_locked": True,
            "last_order_unfilled": True,
        },
    )

    assert order is not None
    assert order.rule_level == "FLOOR_LOCK"
    assert order.suggested_action == "FLOOR_LOCK_RESET"
    assert order.quantity == 0


def test_agent09_invalidation_sla_14h():
    """Kiểm tra SLA xử lý Invalidation: trước 14:00 thì Escalate, sau 14:00 thì Auto Exit."""
    async def _test():
        agent = PositionMonitoringAgent(repository=PortfolioRepository())

        # Trường hợp A: Lúc 10:30 sáng (Trước 14:00)
        res_morning = await agent.process({
            "current_time": "2026-09-04T10:30:00",
            "positions": [{
                "ticker": "FPT",
                "quantity": 1000,
                "available_shares": 1000,
                "entry_price": 130_000.0,
                "current_price": 131_000.0,
            }],
            "invalidation_events": [{
                "ticker": "FPT",
                "event_type": "CATALYST_FAILED",
                "reason": "Chủ tịch đăng ký bán thoái vốn",
                "portfolio_resolved": False,
            }],
        })

        morning_data = res_morning["data"]
        # Trước 14:00 không tự động bán mà chỉ cảnh báo Escalate
        assert morning_data["stop_loss_triggered"] is False
        assert len(morning_data["invalidation_alerts"]) == 1
        assert morning_data["invalidation_alerts"][0]["action_required"] == "ESCALATE_PORTFOLIO_CONFIRMATION"
        assert morning_data["positions_health"][0]["health_status"] == "THESIS_INVALIDATED_ESCALATED"

        # Trường hợp B: Lúc 14:05 chiều (Sau 14:00, không có phản hồi)
        res_afternoon = await agent.process({
            "current_time": "2026-09-04T14:05:00",
            "positions": [{
                "ticker": "FPT",
                "quantity": 1000,
                "available_shares": 1000,
                "entry_price": 130_000.0,
                "current_price": 131_000.0,
            }],
            "invalidation_events": [{
                "ticker": "FPT",
                "event_type": "CATALYST_FAILED",
                "reason": "Chủ tịch đăng ký bán thoái vốn",
                "portfolio_resolved": False,
            }],
        })

        afternoon_data = res_afternoon["data"]
        # Quá 14:00 không có phản hồi -> BẮT BUỘC PHÁT LỆNH AUTO EXIT THOÁT HÀNG TRƯỚC ATC!
        assert afternoon_data["stop_loss_triggered"] is True
        assert len(afternoon_data["stop_loss_orders"]) == 1
        exit_order = afternoon_data["stop_loss_orders"][0]
        assert exit_order["suggested_action"] == "AUTO_EXIT_INVALIDATED_THESIS"
        assert exit_order["urgency"] == "EMERGENCY"
        assert exit_order["quantity"] == 1000

    asyncio.run(_test())


def test_failsafe_active_autonomous_sell():
    """Kiểm tra khi Failsafe ACTIVE, Agent 9 vẫn tự động bán phòng thủ khi chạm Hard Stop."""
    async def _test():
        agent = PositionMonitoringAgent(repository=PortfolioRepository())

        res = await agent.process({
            "failsafe_active": True,
            "nav": 1_000_000_000.0,
            "positions": [{
                "ticker": "HPG",
                "quantity": 10_000,
                "available_shares": 10_000,
                "entry_price": 30_000.0,
                "current_price": 27_000.0,  # Lỗ 30tr = -3% NAV -> Vi phạm Hard Stop
            }],
        })

        data = res["data"]
        assert data["failsafe_active"] is True
        assert data["failsafe_mode"] == "DEFENSIVE_SELL_ENABLED_BUY_BLOCKED"
        assert data["stop_loss_triggered"] is True
        assert len(data["stop_loss_orders"]) == 1
        order = data["stop_loss_orders"][0]
        assert order["failsafe_override"] is True
        assert order["suggested_action"] == "SELL_ALL"
        assert order["urgency"] == "EMERGENCY"

    asyncio.run(_test())


def test_agent09_auto_fetch_realtime_price():
    """Kiểm tra Agent-09 tự động kéo giá realtime từ MarketDataRepository khi không có market_ticks."""
    async def _test():
        class MockMarketDataRepo:
            def get_realtime_or_latest_price(self, symbol, allow_eod_fallback=True):
                if symbol == "HPG":
                    return 24_000.0  # Giá thị trường rơi sâu xuống 24k
                return 50_000.0

        agent = PositionMonitoringAgent(
            repository=PortfolioRepository(),
            market_data_repo=MockMarketDataRepo(),
            auto_dispatch=False,
        )

        # Truyền vị thế mua giá 30k, KHÔNG truyền current_price và KHÔNG truyền market_ticks
        res = await agent.process({
            "nav": 1_000_000_000.0,
            "positions": [{
                "ticker": "HPG",
                "quantity": 10_000,
                "available_shares": 10_000,
                "entry_price": 30_000.0,
                # Không truyền current_price -> Bắt buộc Agent 9 phải tự fetch realtime!
            }],
        })

        data = res["data"]
        pos = data["positions_health"][0]
        # Giá hiện tại phải được tự động cập nhật là 24.000đ từ MarketDataRepository
        assert pos["current_price"] == 24_000.0
        assert pos["unrealized_pnl_vnd"] == -60_000_000.0
        # Hard Stop phải kích hoạt vì lỗ -6% NAV > 2% NAV
        assert data["stop_loss_triggered"] is True
        assert data["stop_loss_orders"][0]["rule_level"] == "HARD_STOP"

    asyncio.run(_test())


def test_agent09_auto_dispatch_to_agent08():
    """Kiểm tra Agent-09 tự động bắn lệnh khẩn cấp sang Agent-08 khi có cờ auto_dispatch."""
    async def _test():
        repo = PortfolioRepository()
        agent = PositionMonitoringAgent(repository=repo, auto_dispatch=True)

        res = await agent.process({
            "nav": 1_000_000_000.0,
            "positions": [{
                "ticker": "HPG",
                "quantity": 10_000,
                "available_shares": 10_000,
                "entry_price": 30_000.0,
                "current_price": 25_000.0,  # Lỗ -50tr = -5% NAV
            }],
            "auto_dispatch": True,
        })

        data = res["data"]
        assert data["stop_loss_triggered"] is True
        order = data["stop_loss_orders"][0]
        # Lệnh phải được tự động dispatch sang Agent-08
        assert order["dispatch_status"] == "DISPATCHED_TO_AGENT_08"
        assert len(data["dispatch_results"]) >= 1
        exec_report = data["dispatch_results"][0]
        assert exec_report["status"] in ("EXECUTED", "PARTIALLY_EXECUTED")
        assert exec_report["ticker"] == "HPG"

    asyncio.run(_test())


def test_agent08_allows_defensive_sell_during_failsafe():
    """Kiểm tra Agent-08 cho phép lệnh BÁN phòng thủ từ Agent-09 bypass Failsafe nhưng vẫn chặn lệnh MUA."""
    async def _test():
        from app.domain.agents.trade_execution import TradeExecutionAgent
        exec_agent = TradeExecutionAgent(repository=PortfolioRepository())

        # 1. Thử lệnh MUA khi Failsafe ACTIVE -> Phải bị BLOCK
        res_buy = await exec_agent.process({
            "failsafe_active": True,
            "order_instruction": {
                "ticker": "HPG",
                "action": "BUY",
                "target_shares": 1000,
                "price": 27000.0,
            },
        })
        assert res_buy["data"]["status"] == "BLOCKED_FAILSAFE"

        # 2. Thử lệnh BÁN phòng thủ khẩn cấp từ Agent-09 khi Failsafe ACTIVE -> Phải được BẬT ĐÈN XANH
        res_sell = await exec_agent.process({
            "failsafe_active": True,
            "order_instruction": {
                "ticker": "HPG",
                "action": "SELL",
                "shares": 1000,
                "price": 27000.0,
                "failsafe_override": True,
                "bypass_portfolio_agent": True,
            },
        })
        assert res_sell["data"]["status"] in ("EXECUTED", "PARTIALLY_EXECUTED")
        assert res_sell["data"]["shares"] == 1000

    asyncio.run(_test())


