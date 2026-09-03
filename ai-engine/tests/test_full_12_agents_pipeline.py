"""Comprehensive Test Suite: Full 12 Multi-Agent Plug-and-Play Architecture & Closed-Loop Pipeline Verification."""

import asyncio
import pytest
from app.core.registry import AgentRegistry
import app.domain.agents  # Tự động nạp và đăng ký 12 Agents


def test_all_12_agents_registered():
    """Kiểm tra toàn bộ 12 Agents đã được đăng ký đầy đủ trong Registry."""
    agents = AgentRegistry.list_agents()
    agent_names = {a["name"] for a in agents}
    
    expected_agents = {
        "agent_market_surveillance",
        "agent_universe_discovery",
        "agent_equity_research",
        "agent_investment_thesis",
        "agent_counter_thesis",
        "agent_portfolio_risk",
        "agent_portfolio_allocation",
        "agent_trade_execution",
        "agent_position_monitoring",
        "agent_reinforcement_learning",
        "agent_system_governance",
        "agent_strategy_cio",
    }
    
    assert expected_agents.issubset(agent_names), f"Thiếu Agent: {expected_agents - agent_names}"
    assert len(agent_names) >= 12


def test_end_to_end_12_agent_pipeline():
    """Chạy mô phỏng tuần tự luồng dữ liệu khép kín (Closed-Loop) qua toàn bộ 12 Agents."""
    async def _test():
        # 1. Market Surveillance
        res_surv = await AgentRegistry.dispatch("market_surveillance", {"date": "2026-08-28"})
        assert res_surv["status"] == "SUCCESS"
        surv_data = res_surv["result"]["data"]
        regime = surv_data["current_regime"]
        session_context = surv_data.get("session_context", "Normal")
        halted_tickers = surv_data.get("halted_tickers", [])
        assert regime in ["BULL_MARKET", "BEAR_MARKET", "RANGE_BOUND"]

        # 2. Reinforcement Learning (Chạy trước để nạp tham số động cho Agent 03, 06, 07)
        res_rl = await AgentRegistry.dispatch("reinforcement_learning", {
            "regime": regime,
            "realized_trades": [{"ticker": "FPT", "pnl": 12000000.0}, {"ticker": "VNM", "pnl": -3000000.0}],
            "factor_predictions": {"FPT": {"css": 82.0}},
            "forward_returns": {"FPT": 0.08},
        })
        assert res_rl["status"] == "SUCCESS"
        rl_data = res_rl["result"]["data"]
        assert "policy_weights" in rl_data
        assert "kelly_matrix" in rl_data

        # 3. Universe Discovery (Lớp 0 Beneish & GIL Check, nhận tín hiệu từ Agent-01)
        res_disc = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "VNM", "HPG"],
            "session_context": session_context,
            "current_regime": regime,
            "halted_tickers": halted_tickers,
            "beneish_overrides": {"FPT": -2.45, "VNM": -2.60, "HPG": -2.30},
        })
        assert res_disc["status"] == "SUCCESS"
        assert res_disc["result"]["data"]["eligible_count"] > 0

        # 4. Equity Research (Bơm 1: Nhận policy_weights từ Agent-10 & Moat AI qua SAG)
        res_res = await AgentRegistry.dispatch("equity_research", {
            "ticker": "FPT",
            "sector": "Technology",
            "current_regime": regime,
            "policy_weights": rl_data["policy_weights"],
        })
        assert res_res["status"] == "SUCCESS"
        assert res_res["result"]["data"]["conviction"] in ["A+", "A", "B", "C", "D"]
        assert res_res["result"]["trace"]["weights_source"] == "AGENT-10 (Reinforcement Learning Adaptive Weights)"

        research_report_data = res_res["result"]["data"]
        research_report_data["conviction"] = "A"
        research_report_data["css"] = 82.0
        research_report_data["current_price"] = 150000.0

        # 5. Investment Thesis (Adaptive Pricing & 3 Signals)
        res_thesis = await AgentRegistry.dispatch("investment_thesis", {"research_report": research_report_data})
        assert res_thesis["status"] == "SUCCESS"
        thesis_data = res_thesis["result"]["data"]
        signals = thesis_data.get("confirming_signals") or thesis_data.get("input_validation", {}).get("independent_signals", [])
        assert len(signals) == 3

        # 6. Counter Thesis (Devil's Advocate & CTS Score)
        res_counter = await AgentRegistry.dispatch("counter_thesis", {"investment_thesis": thesis_data})
        assert res_counter["status"] == "SUCCESS"
        assert res_counter["result"]["data"]["verdict"] in ["PROCEED", "CONDITIONAL", "BLOCK"]

        # 7. Strategy CIO (Arbitration)
        res_cio = await AgentRegistry.dispatch("strategy_cio", {
            "conflict": {
                "thesis_id": thesis_data["thesis_id"],
                "ticker": "FPT",
                "thesis_view": "BULLISH_A_PLUS",
                "counter_view": "CONDITIONAL_WARNING",
            }
        })
        assert res_cio["status"] == "SUCCESS"
        cio_resolution = res_cio["result"]["data"]

        # 8. Portfolio Allocation (Bơm 3: Nhận kelly_matrix từ Agent-10 & Áp trần CIO -> Đề xuất Lệnh ProposedOrder)
        res_alloc = await AgentRegistry.dispatch("portfolio_allocation", {
            "candidate": {"ticker": "FPT", "conviction": "A", "price": 150000.0, "sector": "Technology"},
            "total_nav": 1000000000.0,
            "kelly_matrix": rl_data["kelly_matrix"],
            "weight_cap": cio_resolution.get("weight_cap", 0.15),
            "regime": regime,
        })
        assert res_alloc["status"] == "SUCCESS"
        alloc_data = res_alloc["result"]["data"]
        assert alloc_data["target_shares"] > 0

        # 9. Portfolio Risk (CỔNG THẨM ĐỊNH TỐI CAO: Thẩm định ProposedOrder, Hard Laws, T+2.5, VSA & Tail Risk)
        res_risk = await AgentRegistry.dispatch("portfolio_risk", {
            "portfolio": {"total_nav": 1000000000.0, "peak_nav": 1000000000.0, "locked_t25_value": 0.0},
            "proposed_order": alloc_data,
            "cdc_status": rl_data["cdc_triggered"],
            "market_context": {"distribution_days": 1, "breadth_ma20_pct": 60.0},
        })
        assert res_risk["status"] == "SUCCESS"
        risk_data = res_risk["result"]["data"]
        assert risk_data["risk_status"] in ["PASS", "REDUCE"]
        assert risk_data["decision"]["approved_shares"] > 0

        # 10. Trade Execution (EAE VWAP Slicing: Thực thi số lượng đã được Risk Agent ký duyệt)
        res_exec = await AgentRegistry.dispatch("trade_execution", {
            "order_instruction": risk_data["decision"],
            "adtv20": 2000000,
        })
        assert res_exec["status"] == "SUCCESS"
        exec_data = res_exec["result"]["data"]
        assert exec_data["status"] == "EXECUTED"
        assert exec_data["shares"] == risk_data["decision"]["approved_shares"]

        # 11. Position Monitoring (4 Lớp Stop-loss & Thesis Watchdog)
        res_mon = await AgentRegistry.dispatch("position_monitoring", {
            "position": {"ticker": "FPT", "average_price": 150000.0, "current_price": 152000.0, "quantity": 500},
            "nav": 1000000000.0,
        })
        assert res_mon["status"] == "SUCCESS"
        assert res_mon["result"]["data"]["stop_loss_triggered"] is False

        # 12. System Governance (Sổ cái SHA-256 & Failsafe Check)
        res_gov = await AgentRegistry.dispatch("system_governance", {
            "actions_to_audit": [
                {"agent_id": "portfolio_allocation", "event_type": "BUY_DECISION", "details": alloc_data},
                {"agent_id": "trade_execution", "event_type": "TRADE_FILLED", "details": exec_data},
            ],
            "broker_heartbeat": {"latency_ms": 110.0, "is_connected": True, "missed_beats": 0},
        })
        assert res_gov["status"] == "SUCCESS"
        assert res_gov["result"]["data"]["system_status"] == "COMPLIANT"

    asyncio.run(_test())


def test_universe_discovery_missing_bctc_excluded():
    """Kiểm tra Hard Law: Ticker thiếu BCTC phải bị loại với DATA_MISSING, không được pass ảo."""
    async def _test():
        # Quét 1 mã không tồn tại / không có BCTC
        res = await AgentRegistry.dispatch("universe_discovery", {"tickers": ["NON_EXISTENT_TICKER_123"]})
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        assert data["eligible_count"] == 0
        assert data["excluded_count"] >= 1
        exclusion_reasons = [e["reason"] for e in data["exclusion_log"]]
        assert "DATA_MISSING" in exclusion_reasons or "BENEISH_M_SCORE_MANIPULATION" in exclusion_reasons

    asyncio.run(_test())


def test_equity_research_missing_ticker_raises():
    """Kiểm tra tính toàn vẹn: Thiếu ticker trong research phải raise exception rõ ràng."""
    async def _test():
        with pytest.raises(Exception):
            await AgentRegistry.dispatch("equity_research", {})

    asyncio.run(_test())


def test_reinforcement_learning_real_ic_calculation():
    """Kiểm tra Reinforcement Learning tính toán IC tương quan thực tế khi có đủ mẫu."""
    async def _test():
        predictions = {
            f"SYM_{i}": {"f1_value": 50 + i * 5, "f2_quality": 40 + i * 6, "css": 50 + i * 5}
            for i in range(10)
        }
        forward_rets = {f"SYM_{i}": 0.01 * i for i in range(10)}
        res = await AgentRegistry.dispatch("reinforcement_learning", {
            "factor_predictions": predictions,
            "forward_returns": forward_rets,
        })
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        # Phải tính được IC dương vì tương quan thuận hoàn hảo
        assert data["ic_by_factor"]["F1_Value"] > 0.5

    asyncio.run(_test())


def test_agent_01_to_agent_02_crisis_freeze():
    """Kiểm tra Agent-02 tự động đóng van Discovery Freeze khi Agent-01 phát hiện bối cảnh Crisis."""
    async def _test():
        res = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "VNM", "HPG"],
            "session_context": "Crisis",
            "current_regime": "BEAR_MARKET",
        })
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        # Đóng van bảo toàn vốn: eligible_count bắt buộc phải bằng 0!
        assert data["eligible_count"] == 0
        assert data["exclusion_log"][0]["reason"] == "MARKET_CRISIS_DISCOVERY_FREEZE"

    asyncio.run(_test())


def test_agent_02_halted_stock_exclusion():
    """Kiểm tra Agent-02 loại bỏ ngay lập tức cổ phiếu bị tạm ngừng giao dịch trong phiên (Halt/Suspended)."""
    async def _test():
        res = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "NVL", "HPG"],
            "session_context": "Normal",
            "current_regime": "BULL_MARKET",
            "halted_tickers": ["NVL"],  # NVL bị tạm ngừng giao dịch trong phiên
            "beneish_overrides": {"FPT": -2.45, "HPG": -2.30},
        })
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        excluded_tickers = [x["ticker"] for x in data["exclusion_log"]]
        assert "NVL" in excluded_tickers
        nvl_exclusion = next(x for x in data["exclusion_log"] if x["ticker"] == "NVL")
        assert nvl_exclusion["reason"] == "TRADING_STATUS_HALTED_INTRADAY"

    asyncio.run(_test())


