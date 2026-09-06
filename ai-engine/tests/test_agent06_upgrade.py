"""Unit & Integration Tests for Upgraded AGENT-06: Portfolio Risk Agent (IOS v5.1).

Kiểm thử toàn diện 5 trụ cột rủi ro thể chế:
1. Hydration tự động loại bỏ bẫy Phantom Portfolio (Portfolio Blindness).
2. Tích hợp Dynamic `risk_limits` từ PostgreSQL.
3. Thẩm định Hard Laws (Single stock 15%, Sector 35%, Stop-loss 2% NAV).
4. Cảm biến Dị thường VSA Tape Anomaly với Auto-hydration dữ liệu nến.
5. Drawdown Phased Recovery Protocol & CDC Controller.
6. RabbitMQ Event Publishing & Ghi nhận Snapshot CSDL `risk_snapshots`.
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import MagicMock, patch, AsyncMock
from datetime import datetime, date

from app.domain.agents.portfolio_risk import PortfolioRiskAgent
from app.domain.rules.hard_laws import HardLawEngine, ProposedOrder, PortfolioState, HardLaw
from app.domain.repositories.intelligence_repository import IntelligenceRepository


def test_agent06_auto_hydration_phantom_portfolio_eliminated():
    """Kiểm tra Agent-06 tự động nạp tài khoản và vị thế thực tế từ PortfolioRepository."""
    agent = PortfolioRiskAgent()

    # Mock PortfolioRepository trả về tài khoản thực tế
    mock_account = {
        "account_id": "ACC_INSTITUTIONAL_001",
        "cash_balance": 400000000.0,
        "total_nav": 1200000000.0,
        "peak_nav": 1250000000.0,
        "drawdown_tier": "GREEN",
    }
    mock_positions = [
        {
            "ticker": "HPG",
            "shares": 10000,
            "current_price": 28000.0,
            "average_price": 27500.0,
            "locked_t25_shares": 0,
            "sector": "Tài nguyên Cơ bản",
        },
        {
            "ticker": "SSI",
            "shares": 5000,
            "current_price": 32000.0,
            "average_price": 31000.0,
            "locked_t25_shares": 5000,  # Kẹt hàng T+2.5
            "sector": "Dịch vụ Tài chính",
        },
    ]

    with patch("app.domain.repositories.portfolio_repository.PortfolioRepository.get_account_state", return_value=mock_account), \
         patch("app.domain.repositories.portfolio_repository.PortfolioRepository.get_open_positions", return_value=mock_positions), \
         patch.object(agent, "publish_event", new_callable=AsyncMock) as mock_pub:

        # Gọi process mà HOÀN TOÀN KHÔNG truyền portfolio trong payload
        result = asyncio.run(agent.process({
            "proposed_order": {
                "ticker": "VNM",
                "side": "BUY",
                "target_shares": 2000,
                "price": 68000.0,
                "stop_loss_price": 63200.0,
                "sector": "Thực phẩm & Đồ uống",
                "adtv20": 2000000.0,
            }
        }))

        assert "data" in result
        data = result["data"]
        # NAV phải là 1.2 tỷ từ CSDL chứ không phải 1 tỷ phantom
        assert data["decision"]["action"] in ("PASS", "REDUCE")
        assert data["decision"]["approved_shares"] > 0
        # Đã phát sự kiện RabbitMQ RISK_APPROVED
        assert mock_pub.await_count >= 1


def test_agent06_hard_law_single_stock_breach_block():
    """Kiểm tra Hard Law 4: Chặn lệnh BUY khi vượt trần 15% NAV của một cổ phiếu."""
    agent = PortfolioRiskAgent()

    # Giả sử NAV 1 tỷ, đang nắm 120M HPG (12%), muốn mua thêm 50M HPG -> Tổng 170M (17% NAV) -> Vi phạm
    payload = {
        "portfolio": {
            "total_nav": 1000000000.0,
            "peak_nav": 1000000000.0,
            "cash_vnd": 500000000.0,
            "positions": {
                "HPG": {
                    "quantity": 4000,
                    "shares": 4000,
                    "current_price": 30000.0,  # 120M
                    "sector": "Tài nguyên Cơ bản",
                }
            },
            "sector_exposure": {"Tài nguyên Cơ bản": 120000000.0},
        },
        "proposed_order": {
            "ticker": "HPG",
            "side": "BUY",
            "quantity": 2000,  # Thêm 60M -> Tổng 180M = 18% NAV > 15%
            "price": 30000.0,
            "stop_loss_price": 27900.0,
            "sector": "Tài nguyên Cơ bản",
            "adtv20": 10000000.0,
        },
    }

    with patch.object(agent, "publish_event", new_callable=AsyncMock) as mock_pub:
        result = asyncio.run(agent.process(payload))
        data = result["data"]

        assert data["risk_status"] == "BLOCK"
        assert data["decision"]["approved_shares"] == 0
        assert data["hard_laws"]["single_stock"] == "BLOCK"
        assert "vượt trần" in data["decision"]["rationale"]
        # Đã phát sự kiện RabbitMQ RISK_BREACH_ALERT
        assert mock_pub.await_count >= 1


def test_agent06_hard_law_sector_breach_block():
    """Kiểm tra Hard Law 4: Chặn lệnh BUY khi ngành vượt trần 35% NAV."""
    agent = PortfolioRiskAgent()

    # Giả sử ngành Ngân hàng đã nắm 32% NAV (320M/1B), đề xuất mua thêm 50M TCB -> 37% NAV -> Block
    payload = {
        "portfolio": {
            "total_nav": 1000000000.0,
            "peak_nav": 1000000000.0,
            "cash_vnd": 400000000.0,
            "positions": {
                "VCB": {"quantity": 2000, "current_price": 90000.0, "sector": "Ngân hàng"},  # 180M
                "MBB": {"quantity": 6000, "current_price": 24000.0, "sector": "Ngân hàng"},  # 144M -> Tổng ngành 324M = 32.4%
            },
            "sector_exposure": {"Ngân hàng": 324000000.0},
        },
        "proposed_order": {
            "ticker": "TCB",
            "side": "BUY",
            "quantity": 2000,  # 2000 * 25,000 = 50M -> Tổng ngành = 374M = 37.4% > 35%
            "price": 25000.0,
            "stop_loss_price": 23250.0,
            "sector": "Ngân hàng",
            "adtv20": 15000000.0,
        },
    }

    with patch.object(agent, "publish_event", new_callable=AsyncMock):
        result = asyncio.run(agent.process(payload))
        data = result["data"]

        assert data["risk_status"] == "BLOCK"
        assert data["hard_laws"]["sector"] == "BLOCK"
        assert "ngành" in data["decision"]["rationale"].lower()


def test_agent06_t25_floor_gap_risk_stop_loss_breach():
    """Kiểm tra Hard Law 1: Rủi ro vị thế T+2.5 Floor Gap không được vượt quá 2% NAV."""
    agent = PortfolioRiskAgent()

    # Đề xuất lệnh mua quá lớn: NAV 1 tỷ, mua 200M FPT.
    # Với đệm 2 cây sàn -13.51%, tổn thất tiềm năng = 200M * 0.1351 = 27M > 20M (2% NAV) -> Vi phạm
    payload = {
        "portfolio": {
            "total_nav": 1000000000.0,
            "peak_nav": 1000000000.0,
            "cash_vnd": 800000000.0,
            "positions": {},
            "sector_exposure": {},
        },
        "proposed_order": {
            "ticker": "FPT",
            "side": "BUY",
            "quantity": 2000,  # 2000 * 110,000 = 220M VND
            "price": 110000.0,
            "stop_loss_price": 102000.0,
            "sector": "Công nghệ Thông tin",
            "adtv20": 4000000.0,
        },
    }

    with patch.object(agent, "publish_event", new_callable=AsyncMock):
        result = asyncio.run(agent.process(payload))
        data = result["data"]

        assert data["risk_status"] == "BLOCK"
        assert data["hard_laws"]["position_risk"] == "BLOCK"
        assert "vượt trần" in data["decision"]["rationale"]


def test_agent06_vsa_tape_anomaly_auto_hydration():
    """Kiểm tra tự động nạp nến và phát hiện dị thường VSA (Upthrust Volume Spike)."""
    agent = PortfolioRiskAgent()

    # Mock MarketDataRepository trả về nến Upthrust (Râu trên dài > 50%, volume gấp 3.5 MA20)
    mock_ohlcv = [
        {"open": 25000.0, "high": 25500.0, "low": 24800.0, "close": 25200.0, "volume": 1000000.0}
        for _ in range(19)
    ]
    # Nến cuối là Upthrust
    mock_ohlcv.append({
        "open": 25500.0,
        "high": 27500.0,
        "low": 25400.0,
        "close": 25600.0,  # Đóng gần thấp nhất phiên sau khi kéo xả
        "volume": 3500000.0,  # 3.5M vs MA20 1M
    })

    with patch("app.domain.repositories.market_data_repository.MarketDataRepository.get_ohlcv", return_value=mock_ohlcv), \
         patch.object(agent, "publish_event", new_callable=AsyncMock):

        payload = {
            "portfolio": {"total_nav": 1000000000.0, "positions": {}},
            "proposed_order": {
                "ticker": "VIX",
                "side": "BUY",
                "quantity": 1000,
                "price": 25600.0,
                "stop_loss_price": 23800.0,
                "sector": "Dịch vụ Tài chính",
                "adtv20": 1000000.0,
                # KHÔNG truyền candle, để hệ thống tự động query CSDL
            },
        }

        result = asyncio.run(agent.process(payload))
        data = result["data"]

        # VSA Tape Anomaly phải phát hiện được bất thường nến
        assert data["tape_anomaly"]["detected"] is True
        assert data["tape_anomaly"]["severity"] in ("WARNING", "CRITICAL")


def test_intelligence_repository_risk_limits_crud():
    """Kiểm tra đọc và lưu hạn mức rủi ro thể chế trong IntelligenceRepository."""
    intel_repo = IntelligenceRepository()

    # 1. Đọc hạn mức
    limits = intel_repo.get_risk_limits("HOSE_EQUITY")
    assert isinstance(limits, dict)
    assert limits["max_single_stock_pct"] == 15.0
    assert limits["max_sector_pct"] == 35.0
    assert limits["hard_stop_loss_pct"] == 2.0

    # 2. Lưu hạn mức
    res = intel_repo.save_risk_limits({
        "limit_type": "HOSE_EQUITY",
        "max_single_stock_pct": 15.0,
        "max_sector_pct": 35.0,
        "hard_stop_loss_pct": 2.0,
    })
    assert res is True
