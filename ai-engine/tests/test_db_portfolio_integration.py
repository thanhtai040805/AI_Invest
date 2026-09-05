"""Test Database Portfolio Repository & Agent Integration (IOS v5.1).
Kiểm thử tính toàn vẹn của chu trình đọc/ghi tài khoản, khớp lệnh trừ tiền + cộng cổ phiếu,
và giám sát vị thế tự động qua PortfolioRepository.
"""

import asyncio
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.agents.portfolio_allocation import PortfolioAllocationAgent
from app.domain.agents.trade_execution import TradeExecutionAgent
from app.domain.agents.position_monitoring import PositionMonitoringAgent


def reset_test_db(repo: PortfolioRepository):
    """Reset tài khoản và vị thế kiểm thử về trạng thái chuẩn ban đầu."""
    try:
        # Reset số dư user
        repo.storage.execute(
            "UPDATE users SET cash_balance = 1000000000.0 WHERE id = %s",
            (repo._in_memory_account["account_id"],)
        )
        # Xóa các vị thế test
        repo.storage.execute(
            "DELETE FROM positions WHERE symbol IN ('FPT', 'VNM', 'HPG')",
        )
        # Xóa các orders test
        repo.storage.execute(
            "DELETE FROM orders WHERE symbol IN ('FPT', 'VNM', 'HPG')",
        )
    except Exception:
        pass
    repo._in_memory_account["cash_balance"] = 1000000000.0
    repo._in_memory_account["total_nav"] = 1000000000.0
    repo._in_memory_positions.clear()


def test_portfolio_repository_lifecycle():
    """Kiểm thử chu trình vòng đời tài khoản và vị thế trong PortfolioRepository."""
    async def _test():
        repo = PortfolioRepository()
        reset_test_db(repo)

        # 1. Kiểm tra tài khoản ban đầu
        acc = repo.get_account_state()
        assert acc["cash_balance"] == 1000000000.0
        assert acc["total_nav"] == 1000000000.0

        # 2. Khớp lệnh MUA 500 FPT giá 150,000đ (Trị giá 75,000,000đ)
        buy_result = repo.execute_order_transaction(
            ticker="FPT",
            action="BUY",
            shares=500,
            executed_price=150000.0,
            target_price=150000.0,
            slippage_bps=0.0,
        )
        assert buy_result["status"] == "FILLED"
        assert buy_result["remaining_cash"] == 1000000000.0 - 75000000.0

        # 3. Kiểm tra danh mục vị thế sau khi mua
        positions = repo.get_open_positions()
        fpt_pos = next((p for p in positions if p["ticker"] == "FPT"), None)
        assert fpt_pos is not None
        assert fpt_pos["shares"] == 500
        assert fpt_pos["average_price"] == 150000.0

        # 4. Mua thêm 500 FPT giá 160,000đ (Kiểm tra bình quân giá)
        repo.execute_order_transaction(
            ticker="FPT",
            action="BUY",
            shares=500,
            executed_price=160000.0,
        )
        positions = repo.get_open_positions()
        fpt_pos = next((p for p in positions if p["ticker"] == "FPT"), None)
        assert fpt_pos is not None
        assert fpt_pos["shares"] == 1000
        assert fpt_pos["average_price"] == 155000.0  # (150*500 + 160*500) / 1000

        # 5. Khớp lệnh BÁN 400 FPT giá 170,000đ
        repo.execute_order_transaction(
            ticker="FPT",
            action="SELL",
            shares=400,
            executed_price=170000.0,
        )
        positions = repo.get_open_positions()
        fpt_pos = next((p for p in positions if p["ticker"] == "FPT"), None)
        assert fpt_pos is not None
        assert fpt_pos["shares"] == 600

        # Dọn dẹp sau test
        reset_test_db(repo)

    asyncio.run(_test())


def test_agents_db_sync_pipeline():
    """Kiểm thử sự phối hợp nhịp nhàng giữa Agent-07, Agent-08 và Agent-09 qua PortfolioRepository."""
    async def _test():
        repo = PortfolioRepository()
        reset_test_db(repo)

        alloc_agent = PortfolioAllocationAgent(repository=repo)
        exec_agent = TradeExecutionAgent(repository=repo)
        mon_agent = PositionMonitoringAgent(repository=repo)

        # 1. Agent-07 định cỡ phân bổ vốn
        alloc_res = await alloc_agent.process({
            "candidate": {"ticker": "VNM", "conviction": "A+", "price": 70000.0},
            "regime": "BULL_MARKET",
        })
        order_instruction = alloc_res["data"]
        assert order_instruction["action"] == "BUY"
        assert order_instruction["target_shares"] > 0

        # 2. Agent-08 thực thi lệnh và tự động ghi vào DB
        exec_res = await exec_agent.process({
            "order_instruction": order_instruction,
            "adtv20": 3000000.0,
        })
        report = exec_res["data"]
        assert report["status"] == "EXECUTED"
        assert report["ticker"] == "VNM"

        # Kiểm tra vị thế đã được ghi vào DB
        positions = repo.get_open_positions()
        vnm_pos = next((p for p in positions if p["ticker"] == "VNM"), None)
        assert vnm_pos is not None
        assert vnm_pos["shares"] == order_instruction["target_shares"]

        # 3. Agent-09 tự động đọc vị thế từ DB và canh gác Stop-loss
        mon_res = await mon_agent.process({
            "market_ticks": {
                "VNM": {"current_candle": {"close": 68000.0}, "ma20_volume": 2000000.0}
            }
        })
        mon_data = mon_res["data"]
        assert mon_data["monitored_count"] >= 1
        mon_vnm = next(p for p in mon_data["positions_health"] if p["ticker"] == "VNM")
        assert mon_vnm["ticker"] == "VNM"
        assert mon_vnm["health_status"] in ("HEALTHY", "WARNING")

        # Dọn dẹp sau test
        reset_test_db(repo)

    asyncio.run(_test())
