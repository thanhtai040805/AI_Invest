"""Unit and Integration Tests for AGENT-02 (Universe Discovery Agent) Production Upgrade."""

import asyncio
from datetime import date
import pytest

import app.domain.agents
from app.core.registry import AgentRegistry
from app.domain.rules.beneish import beneish_engine
from app.domain.rules.universe_manager import universe_manager
from app.domain.repositories.universe_repository import UniverseRepository
from app.infrastructure.database.pg_pool import get_conn


def test_beneish_engine_financial_exemption():
    """Kiểm tra BeneishMScoreEngine miễn trừ kiểm tra cho nhóm Ngân hàng/Tài chính."""
    res = beneish_engine.calculate_m_score("VCB")
    assert res["status"] == "PASS"
    assert res["is_exempt"] is True
    assert res["m_score"] <= -1.78  # Điểm an toàn


def test_beneish_engine_real_data_calculation():
    """Kiểm tra BeneishMScoreEngine tính toán 8 biến chuẩn từ CSDL thật."""
    res_fpt = beneish_engine.calculate_m_score("FPT")
    assert res_fpt["status"] == "PASS"
    assert res_fpt["is_exempt"] is False
    assert res_fpt["m_score"] is not None
    assert res_fpt["m_score"] <= -1.78
    assert "dsri" in res_fpt["variables"]
    assert "gmi" in res_fpt["variables"]
    assert "tata" in res_fpt["variables"]


def test_universe_manager_classify_with_real_liquidity():
    """Kiểm tra UniverseManager phân loại đúng nhóm A, B, C và Excluded với ADTV20 thực tế."""
    res = universe_manager.classify_universe(["FPT", "VNM", "VCB"])
    tickers = {r["ticker"]: r for r in res["results"]}
    assert "FPT" in tickers
    assert tickers["FPT"]["universe_group"] == "A"
    assert tickers["FPT"]["adtv20"] >= 15_000_000_000


def test_agent02_dispatch_real_pipeline():
    """Kiểm tra Agent 02 dispatch qua AgentRegistry chạy hoàn toàn trên CSDL thật không cần mock."""
    async def _test():
        res = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "VNM", "VCB"],
            "session_context": "Normal",
            "current_regime": "BULL_MARKET",
        })
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        assert data["scanned_count"] == 3
        assert data["eligible_count"] >= 2
        eligible_tickers = {x["ticker"] for x in data["discovery_list"]}
        assert "FPT" in eligible_tickers
        assert "VCB" in eligible_tickers

    asyncio.run(_test())


def test_agent02_crisis_discovery_freeze():
    """Kiểm tra Hard Law: Chế độ Crisis đóng van mua mới 100%."""
    async def _test():
        res = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "VNM", "VCB"],
            "session_context": "Crisis",
            "current_regime": "BEAR_MARKET",
        })
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        assert data["eligible_count"] == 0
        assert data["exclusion_log"][0]["reason"] == "MARKET_CRISIS_DISCOVERY_FREEZE"

    asyncio.run(_test())


def test_agent02_intraday_halt_exclusion():
    """Kiểm tra loại bỏ cổ phiếu bị Halt từ tín hiệu của Agent-01."""
    async def _test():
        res = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "VNM"],
            "session_context": "Normal",
            "current_regime": "BULL_MARKET",
            "halted_tickers": ["VNM"],
        })
        assert res["status"] == "SUCCESS"
        data = res["result"]["data"]
        excluded = {x["ticker"]: x["reason"] for x in data["exclusion_log"]}
        assert "VNM" in excluded
        assert excluded["VNM"] == "TRADING_STATUS_HALTED_INTRADAY"

    asyncio.run(_test())


def test_agent02_state_persistence_in_postgres():
    """Kiểm tra dữ liệu được ghi chuẩn xác vào universe_securities và beneish_results."""
    async def _test():
        res = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": ["FPT", "VCB"],
            "session_context": "Normal",
            "current_regime": "BULL_MARKET",
        })
        assert res["status"] == "SUCCESS"

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT ticker, universe_group, beneish_status FROM universe_securities WHERE ticker IN ('FPT', 'VCB')"
                )
                rows = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
                assert "FPT" in rows
                assert rows["FPT"][0] == "A"
                assert rows["FPT"][1] == "PASS"
                assert "VCB" in rows
                assert rows["VCB"][0] == "A"

                cur.execute(
                    "SELECT ticker, m_score, status FROM beneish_results WHERE ticker = 'FPT' ORDER BY quarter_date DESC LIMIT 1"
                )
                b_row = cur.fetchone()
                assert b_row is not None
                assert float(b_row[1]) <= -1.78
                assert b_row[2] == "PASS"

    asyncio.run(_test())
