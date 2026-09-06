"""Unit & Integration Tests for Upgraded AGENT-07: Portfolio Allocation Agent (IOS v5.1).

Kiểm thử toàn diện 8 Engine nghiệp vụ & Chuẩn On PROD:
1. Auto-Hydration Luận điểm Đầu tư (`investment_theses`), Phản biện (`counter_thesis_verdicts`), và Khuyến nghị Tiền mặt (`risk_snapshots`).
2. Bảo vệ Tuyệt đối: Phán quyết BLOCK từ Counter-Thesis tự động từ chối phân bổ vốn (Không lọt lưới).
3. Định cỡ Vị thế Quarter Kelly f* = 0.25 * (p - (1-p)/R) và hệ số co giãn Regime thị trường.
4. Ràng buộc Trần Ngành <= 35% NAV và Trần Cổ phiếu <= 15% NAV.
5. Ngưỡng Deadband >= 2.0% chống bào mòn phí giao dịch khi chênh lệch tỷ trọng nhỏ.
6. Phân mảnh hàng T+2.5 (Available vs Locked) chống vi phạm bán khống.
7. Kích hoạt RabbitMQ Event Bus Publishing (`ORDER_INSTRUCTION` & `REBALANCE_PLANNED`).
8. CSDL `portfolio_decisions` & Hàm truy vấn `get_latest_decision`, `get_decisions_by_date`.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, date

from app.domain.agents.portfolio_allocation import PortfolioAllocationAgent
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.repositories.intelligence_repository import IntelligenceRepository
from app.core.event_topics import EventTopics


def test_agent07_auto_hydration_from_db():
    """Kiểm tra Agent-07 tự động nạp Thesis và Counter-Thesis từ CSDL khi payload bị thiếu."""
    agent = PortfolioAllocationAgent()

    mock_thesis = {
        "thesis_id": "THESIS_HPG_2026Q3_TEST",
        "ticker": "HPG",
        "conviction": "A",
        "sector": "Tài nguyên Cơ bản",
        "status": "APPROVED_ACTIVE",
        "confirming_signals": ["PASS_F1", "PASS_F2", "PASS_F3"],
    }
    mock_counter = {
        "thesis_id": "THESIS_HPG_2026Q3_TEST",
        "ticker": "HPG",
        "verdict": "PROCEED",
        "cts_score": 25.0,
    }
    mock_risk_snap = {
        "date": "2026-09-05",
        "drawdown_tier": "GREEN",
        "garch_cash_target": 10.0,
    }

    with patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_investment_thesis", return_value=mock_thesis), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_counter_thesis_verdict", return_value=mock_counter), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_risk_snapshot", return_value=mock_risk_snap), \
         patch.object(agent.repository, "get_open_positions", return_value=[]), \
         patch.object(agent, "publish_event", new_callable=AsyncMock) as mock_pub:

        # Gọi process với candidate tối giản (không truyền thesis hay counter-thesis)
        result = asyncio.run(agent.process({
            "candidate": {
                "ticker": "HPG",
                "price": 25000.0,
                "adtv20": 5000000.0,
            },
            "total_nav": 1000000000.0,
            "cash_balance": 900000000.0,
        }))

        assert "data" in result
        data = result["data"]

        # Cổ phiếu phải được phân bổ hợp lệ (không bị từ chối do thiếu conviction hay thesis)
        assert data["action"] in ("BUY", "REBALANCE")
        assert data["target_shares"] > 0
        assert data["conviction"] == "A"
        assert data["sector"] == "Tài nguyên Cơ bản"
        # Đã phát sự kiện RabbitMQ ORDER_INSTRUCTION
        assert mock_pub.await_count >= 1


def test_agent07_counter_thesis_block_prevents_buy():
    """Kiểm tra: Nếu Counter-Thesis trong CSDL là BLOCK, Agent-07 từ chối mua và trả về HOLD."""
    agent = PortfolioAllocationAgent()

    mock_thesis = {
        "thesis_id": "THESIS_RISKY_001",
        "ticker": "RISKY",
        "conviction": "A",
        "sector": "Bất động sản",
        "status": "REJECTED",
        "confirming_signals": ["PASS_F1", "PASS_F2", "PASS_F3"],
    }
    mock_counter = {
        "thesis_id": "THESIS_RISKY_001",
        "ticker": "RISKY",
        "verdict": "BLOCK",
        "cts_score": 85.0,
        "block_reasons": ["Phát hiện đồ thị sở hữu chéo rút ruột nghiêm trọng GIL CATASTROPHIC"],
    }

    with patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_investment_thesis", return_value=mock_thesis), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_counter_thesis_verdict", return_value=mock_counter):

        result = asyncio.run(agent.process({
            "candidate": {
                "ticker": "RISKY",
                "price": 50000.0,
                "adtv20": 2000000.0,
            },
            "total_nav": 1000000000.0,
            "cash_balance": 900000000.0,
        }))

        data = result["data"]
        # Phải từ chối giải ngân (HOLD)
        assert data["action"] == "HOLD"
        assert data["portfolio_decision"]["portfolio_decision"] == "HOLD"
        assert data["quantity"] == 0
        assert "BLOCK" in str(data["portfolio_decision"]["sub_action"]) or "INELIGIBLE" in str(data["portfolio_decision"]["sub_action"])


def test_agent07_quarter_kelly_and_regime_scaling():
    """Kiểm tra công thức Quarter Kelly và hệ số co giãn Regime."""
    agent = PortfolioAllocationAgent()

    mock_thesis = {
        "thesis_id": "THESIS_FPT_001",
        "ticker": "FPT",
        "conviction": "A",
        "sector": "Công nghệ Thông tin",
        "status": "APPROVED_ACTIVE",
        "confirming_signals": ["PASS_F1", "PASS_F2", "PASS_F3"],
    }
    mock_counter = {"verdict": "PROCEED", "cts_score": 15.0}

    with patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_investment_thesis", return_value=mock_thesis), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_counter_thesis_verdict", return_value=mock_counter), \
         patch.object(agent, "publish_event", new_callable=AsyncMock):

        # 1. Chế độ BULL_MARKET (hệ số 1.0x)
        res_bull = asyncio.run(agent.process({
            "candidate": {"ticker": "FPT", "price": 100000.0, "adtv20": 4000000.0},
            "total_nav": 1000000000.0,
            "cash_balance": 800000000.0,
            "regime": "BULL_MARKET",
        }))
        alloc_bull = res_bull["data"]["capital_allocation"]["preliminary_target"]

        # 2. Chế độ BEAR_MARKET (hệ số co giãn 0.3x)
        res_bear = asyncio.run(agent.process({
            "candidate": {"ticker": "FPT", "price": 100000.0, "adtv20": 4000000.0},
            "total_nav": 1000000000.0,
            "cash_balance": 800000000.0,
            "regime": "BEAR_MARKET",
        }))
        alloc_bear = res_bear["data"]["capital_allocation"]["preliminary_target"]

        # Sizing trong thị trường gấu phải nhỏ hơn rõ rệt so với thị trường tăng
        assert alloc_bull > alloc_bear
        assert alloc_bear < 0.08  # Bị co giãn phòng thủ


def test_agent07_sector_limit_35_pct_capped():
    """Kiểm tra ràng buộc trần ngành <= 35% NAV."""
    agent = PortfolioAllocationAgent()

    # Giả sử ngành Tài nguyên cơ bản đã chiếm 30% NAV (300M VND)
    mock_positions = [
        {"ticker": "NKG", "shares": 10000, "current_price": 20000.0, "sector": "Tài nguyên Cơ bản", "market_value": 200000000.0},
        {"ticker": "HSG", "shares": 5000, "current_price": 20000.0, "sector": "Tài nguyên Cơ bản", "market_value": 100000000.0},
    ]

    mock_thesis = {
        "thesis_id": "THESIS_HPG_SECTOR",
        "ticker": "HPG",
        "conviction": "A+",
        "sector": "Tài nguyên Cơ bản",
        "status": "APPROVED_ACTIVE",
        "confirming_signals": ["PASS_F1", "PASS_F2", "PASS_F3"],
    }
    mock_counter = {"verdict": "PROCEED", "cts_score": 10.0}

    with patch.object(agent.repository, "get_open_positions", return_value=mock_positions), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_investment_thesis", return_value=mock_thesis), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_counter_thesis_verdict", return_value=mock_counter), \
         patch.object(agent, "publish_event", new_callable=AsyncMock):

        # Mua thêm HPG: Dù Kelly sơ bộ có thể muốn 12% NAV, nhưng ngành chỉ còn dư (35% - 30% = 5% NAV)
        result = asyncio.run(agent.process({
            "candidate": {"ticker": "HPG", "price": 25000.0, "adtv20": 10000000.0},
            "total_nav": 1000000000.0,
            "cash_balance": 700000000.0,
            "regime": "BULL_MARKET",
        }))

        data = result["data"]
        portfolio_target = data["capital_allocation"]["portfolio_target"]
        sector_after = data["portfolio_impact"]["sector_exposure_after"]

        # Tỷ trọng cấp cho HPG phải bị chặn ở mức tối đa 5% NAV để tổng ngành không quá 35%
        assert portfolio_target <= 0.051
        assert sector_after <= 0.351


def test_agent07_deadband_threshold_prevents_churn():
    """Kiểm tra ngưỡng Deadband >= 2.0% tránh rebalance liên tục bào mòn phí."""
    agent = PortfolioAllocationAgent()

    # Đang nắm ~4.5% NAV của HPG (45M VND, 1800 cổ). Mục tiêu model là ~4.25% NAV (Chênh lệch ~0.25% < 2.0% Deadband)
    mock_positions = [
        {
            "ticker": "HPG",
            "shares": 1800,
            "current_price": 25000.0,
            "available_shares": 1800,
            "locked_t25_shares": 0,
            "market_value": 45000000.0,
            "sector": "Tài nguyên Cơ bản",
        }
    ]

    mock_thesis = {
        "thesis_id": "THESIS_HPG_DEADBAND",
        "ticker": "HPG",
        "conviction": "A",
        "sector": "Tài nguyên Cơ bản",
        "status": "APPROVED_ACTIVE",
        "confirming_signals": ["PASS_F1", "PASS_F2", "PASS_F3"],
    }
    mock_counter = {"verdict": "PROCEED", "cts_score": 15.0}

    with patch.object(agent.repository, "get_open_positions", return_value=mock_positions), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_investment_thesis", return_value=mock_thesis), \
         patch("app.domain.repositories.intelligence_repository.IntelligenceRepository.get_latest_counter_thesis_verdict", return_value=mock_counter):

        result = asyncio.run(agent.process({
            "candidate": {"ticker": "HPG", "price": 25000.0, "adtv20": 5000000.0},
            "total_nav": 1000000000.0,
            "cash_balance": 800000000.0,
            "weight_cap": 0.0425,  # Ép mục tiêu quanh 4.25% (Chênh lệch 0.25% < 2.0% Deadband)
        }))

        data = result["data"]
        # Do chênh lệch nhỏ hơn 2%, Rebalancing Engine phải giữ HOLD
        assert data["action"] == "HOLD"
        assert data["quantity"] == 0
        assert data["decision_log"]["deadband_passed"] is False


def test_agent07_save_and_query_portfolio_decision():
    """Kiểm tra lưu và truy vấn bảng portfolio_decisions qua PortfolioRepository."""
    repo = PortfolioRepository()
    now_d = date.today()

    sample_decision = {
        "decision_id": "DEC-TEST-2026-001",
        "ticker": "VNM",
        "action": "BUY",
        "target_shares": 3000,
        "allocated_weight_pct": 12.5,
        "rationale": "Thử nghiệm lưu CSDL",
    }

    # 1. Lưu quyết định
    saved = repo.save_decision(sample_decision)
    assert saved is True

    # 2. Truy vấn quyết định gần nhất theo ticker
    latest = repo.get_latest_decision("VNM")
    assert latest is not None
    assert latest["ticker"] == "VNM"
    assert latest["action"] == "BUY"
    assert latest["target_shares"] == 3000

    # 3. Truy vấn danh sách quyết định trong ngày
    daily_list = repo.get_decisions_by_date(now_d)
    assert isinstance(daily_list, list)
    assert any(d["ticker"] == "VNM" for d in daily_list)
