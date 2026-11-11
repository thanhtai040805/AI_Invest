"""Regression test suite for the 12-Agent Sovereign Pipeline & Daily ETL hidden bugs."""

from __future__ import annotations

import asyncio
import inspect
import sys
import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from zoneinfo import ZoneInfo

import pytest

from app.domain.agents.market_surveillance import MarketSurveillanceAgent
from app.domain.agents.portfolio_allocation import PortfolioAllocationAgent
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.rules.execution.eae import ExecutionAdaptationEngine
from app.domain.rules.market import macro_service
from app.domain.rules.market.session_context_manager import (
    HOSEMarketSession,
    SessionContextManager,
)
from app.infrastructure.data_pipelines import corporate_actions, ohlcv_backfill, scraper_insider
from app.infrastructure.data_pipelines.data_enricher import DataEnricher
from app.infrastructure.external_api.dnse.market_session import (
    MarketSessionManager,
    MarketState,
)

TZ_VN = ZoneInfo("Asia/Ho_Chi_Minh")


# ---------------------------------------------------------------------------
# Bug #2: UTC vs ICT (Asia/Ho_Chi_Minh) Clock Skew
# ---------------------------------------------------------------------------


def test_session_context_manager_converts_utc_to_ict():
    """02:46 UTC on a Wednesday is 09:46 ICT (CONTINUOUS_MORNING on HOSE), never CLOSED."""
    utc_0246_wed = datetime(2026, 11, 11, 2, 46, 0, tzinfo=timezone.utc)
    session = SessionContextManager.get_session(utc_0246_wed)
    assert session == HOSEMarketSession.CONTINUOUS_MORNING
    assert SessionContextManager.is_trading_active(session) is True
    assert SessionContextManager.should_pause_polling(session) is False


def test_eae_converts_utc_timestamps_to_ict():
    """ExecutionAdaptationEngine resolves HOSE market phase & ATC pegging using ICT."""
    eae = ExecutionAdaptationEngine()
    # 02:46 UTC == 09:46 ICT -> CONTINUOUS_AM
    assert eae.determine_market_phase(datetime(2026, 11, 11, 2, 46, tzinfo=timezone.utc)) == "CONTINUOUS_AM"
    # 07:35 UTC == 14:35 ICT -> ATC Dynamic IEP Pegging
    atc_res = eae.resolve_atc_contingency(
        ticker="FPT",
        remaining_quantity=10_000,
        decision_price=130_000.0,
        max_price=135_000.0,
        current_time=datetime(2026, 11, 11, 7, 35, tzinfo=timezone.utc),
        iep_price=131_000.0,
        atc_concentration=0.35,
        anomaly_status="NORMAL",
    )
    assert atc_res["phase"] == "DYNAMIC_IEP_PEGGING"
    assert atc_res["order_type"] == "LO_ATC_PEGGED"


def test_market_surveillance_agent_resolves_utc_0246_as_continuous_am():
    """MarketSurveillanceAgent must not pause polling at 09:46 ICT when passed UTC datetime."""
    agent = MarketSurveillanceAgent()
    utc_0246_wed = datetime(2026, 11, 11, 2, 46, 0, tzinfo=timezone.utc)

    fake_hydrated = {
        "effective_date": date(2026, 11, 11),
        "market_breadth_stats": {
            "total_stocks": 380,
            "advancing": 210,
            "declining": 120,
            "unchanged": 50,
            "floor_count": 2,
            "ceiling_count": 6,
            "adv_decl_ratio": 1.75,
            "foreign_net_vol": 1_000_000.0,
            "total_market_volume": 500_000_000.0,
        },
        "breadth_above_ma50_pct": 58.0,
        "breadth_valid": True,
        "breadth_above_ma20_pct": 61.0,
        "breadth_ma20_valid": True,
        "df_vni": None,
        "vni_return": 0.008,
        "index_returns": None,
        "order_book": {},
        "vn30_returns": {},
        "vn30_weights": {},
        "stock_returns_df": None,
    }

    fake_conn = MagicMock()
    with patch.object(agent, "_sync_hydrate_data", return_value=fake_hydrated), patch.object(
        agent.market_data_service, "get_breadth", side_effect=RuntimeError("offline")
    ), patch("app.domain.agents.market_surveillance.get_conn", return_value=fake_conn):
        res = asyncio.run(
            agent.process(
                {
                    "current_time": utc_0246_wed,
                    "is_realtime": True,
                }
            )
        )

    pulse = res["data"]
    assert pulse["session_code"] == HOSEMarketSession.CONTINUOUS_MORNING.value
    assert isinstance(pulse["session_context"], str) and pulse["session_context"] != ""


# ---------------------------------------------------------------------------
# Bug #5 & Bug #6: MarketSurveillanceAgent SQL Reference Price & Macro ASOF
# ---------------------------------------------------------------------------


def test_market_surveillance_sql_uses_prev_close_and_lateral_macro():
    """Verify breadth/floor/ceiling SQL compares close vs LAG(close) (prev_close) and ASOF-joins macro."""
    src = inspect.getsource(MarketSurveillanceAgent._sync_hydrate_data)
    assert "LAG(close) OVER (PARTITION BY ticker ORDER BY date) AS prev_close" in src
    assert "close <= ref_price * 0.931" in src
    assert "close >= ref_price * 1.069" in src
    assert "LEFT JOIN LATERAL" in src
    assert "indicator_date <= vni.date" in src


def test_macro_service_interbank_uses_tradingview_scanner_and_target_date(monkeypatch):
    """_fetch_tradingview parses TradingView Scanner REST without tvDatafeed and persists target_date."""
    fake_payload = {
        "totalCount": 2,
        "data": [
            {"s": "ECONOMICS:VNINBR", "d": [6.75]},
            {"s": "ECONOMICS:VNINTR", "d": [4.5]},
        ],
    }

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return fake_payload

    monkeypatch.setattr("httpx.post", lambda *args, **kwargs: FakeResponse())
    monkeypatch.setattr("yfinance.Ticker", MagicMock(side_effect=RuntimeError("offline")))
    parsed: dict = {}
    macro_service._fetch_tradingview(parsed)
    assert parsed["vninbr_interbank_rate"] == pytest.approx(6.75)
    assert parsed["vnintr_interest_rate"] == pytest.approx(4.5)

    captured = {}

    def fake_persist(data, target_date=None):
        captured["data"] = data
        captured["target_date"] = target_date

    monkeypatch.setattr(macro_service, "_fetch_all_macro", lambda: {"vninbr_interbank_rate": 6.75})
    monkeypatch.setattr(macro_service, "_persist_macro", fake_persist)
    out = macro_service.refresh_macro(target_date=date(2026, 11, 11))
    assert out["vninbr_interbank_rate"] == pytest.approx(6.75)
    assert captured["target_date"] == date(2026, 11, 11)


# ---------------------------------------------------------------------------
# Bug #3: Phase 8 <-> Phase 9 Split-Brain Reconciliation
# ---------------------------------------------------------------------------


def test_portfolio_allocation_defer_order_event_suppresses_premature_publish():
    """When defer_order_event=True, Phase 8 must NOT publish ORDER_INSTRUCTION before Phase 9 Risk."""
    storage = MagicMock()
    storage.fetch_all.return_value = []
    repo = PortfolioRepository(storage=storage)
    repo.account_id = "test-user"
    repo._in_memory_account = {
        "account_id": "test-user",
        "cash_balance": 1_000_000_000.0,
        "total_nav": 1_000_000_000.0,
        "peak_nav": 1_000_000_000.0,
        "drawdown_tier": "GREEN",
        "win_rate": 0.6,
    }

    with patch.dict("os.environ", {"ENVIRONMENT": "test"}):
        agent = PortfolioAllocationAgent(repository=repo)
        agent.publish_event = AsyncMock()
        res = asyncio.run(
            agent.process(
                {
                    "candidate": {
                        "ticker": "FPT",
                        "conviction": "A",
                        "price": 130_000.0,
                        "adtv20": 3_000_000.0,
                        "sector": "Technology",
                    },
                    "investment_thesis": {
                        "status": "PROCEED",
                        "confirming_signals": ["S1", "S2", "S3"],
                        "conviction": "A",
                        "sector": "Technology",
                    },
                    "counter_thesis": {
                        "verdict": "PROCEED",
                        "cts_score": 20.0,
                    },
                    "regime": "BULL_MARKET",
                    "drawdown_tier": "GREEN",
                    "cash_target_override": 0.10,
                    "total_nav": 1_000_000_000.0,
                    "cash_balance": 1_000_000_000.0,
                    "existing_positions": [],
                    "defer_order_event": True,
                }
            )
        )

    assert res["data"]["decision_id"]
    assert res["data"]["target_shares"] > 0
    agent.publish_event.assert_not_called()


def test_reconcile_decision_after_risk_blocks_and_cancels_untouched_campaign():
    """PortfolioRepository.reconcile_decision_after_risk zeroes blocked orders and cancels untouched campaigns."""
    storage = MagicMock()
    repo = PortfolioRepository(storage=storage)
    repo._in_memory_campaigns["MWG"] = {
        "ticker": "MWG",
        "status": "IN_PROGRESS",
        "accumulated_shares": 0,
        "target_shares": 1500,
    }
    decision_id = str(uuid.uuid4())

    ok = repo.reconcile_decision_after_risk(
        decision_id=decision_id,
        ticker="MWG",
        risk_status="BLOCK",
        approved_shares=0,
        approved_weight_pct=0.0,
        risk_rationale="Drawdown Tier RED kill-switch active",
    )

    assert ok is True
    assert repo._in_memory_campaigns["MWG"]["status"] == "CANCELLED_BY_RISK"
    executed_sqls = [c.args[0] for c in storage.execute.call_args_list]
    assert any("UPDATE portfolio_campaigns" in sql and "CANCELLED_BY_RISK" in sql for sql in executed_sqls)
    assert any("UPDATE portfolio_decisions" in sql and "target_shares = 0" in sql for sql in executed_sqls)

    # Also test REDUCE reconciliation
    storage.reset_mock()
    reduce_id = str(uuid.uuid4())
    ok_reduce = repo.reconcile_decision_after_risk(
        decision_id=reduce_id,
        ticker="FPT",
        risk_status="REDUCE",
        approved_shares=600,
        approved_weight_pct=0.078,
        risk_rationale="Trimmed to respect sector cap",
    )
    assert ok_reduce is True
    reduce_call = storage.execute.call_args_list[-1]
    assert "UPDATE portfolio_decisions" in reduce_call.args[0]
    assert reduce_call.args[1][0] == 600
    assert "[REDUCED_BY_RISK -> 600 CP]" in reduce_call.args[1][2]


# ---------------------------------------------------------------------------
# Bug #4: Corporate Actions Vietcap IQ O(1) Batch & vnstock-Free Fallbacks
# ---------------------------------------------------------------------------


def test_corporate_actions_vci_normalization_and_missing_vnstock_fallback(monkeypatch):
    """Vietcap IQ DIV & ISS bonus share events map cleanly; missing vnstock never raises ModuleNotFoundError."""
    raw_div = {
        "ticker": "FPT",
        "eventCode": "DIV",
        "eventTitleVi": "Trả cổ tức bằng tiền mặt 1,000 đồng/CP",
        "exrightDate": "2026-11-20T00:00:00",
        "recordDate": "2026-11-21T00:00:00",
        "valuePerShare": 1000,
    }
    norm_div = corporate_actions._normalize_vci_event(raw_div)
    row_div = corporate_actions._action_row("FPT", norm_div)
    assert row_div is not None
    assert row_div[0] == "FPT"
    assert row_div[1] == date(2026, 11, 20)
    assert row_div[2] == "DIVIDEND"
    assert row_div[3] == 1000

    raw_bonus = {
        "ticker": "HPG",
        "eventCode": "ISS",
        "eventTitleVi": "Phát hành cổ phiếu - Cổ phiếu thưởng tỉ lệ 15.0%",
        "eventTitleEn": "Share Issuance - Bonus Issue ratio 15.0%",
        "exrightDate": "2026-12-01",
        "exerciseRatio": 0.15,
    }
    norm_bonus = corporate_actions._normalize_vci_event(raw_bonus)
    row_bonus = corporate_actions._action_row("HPG", norm_bonus)
    assert row_bonus is not None
    assert row_bonus[2] == "STOCK_DIVIDEND"
    assert row_bonus[4] == pytest.approx(0.15)

    # Simulate vnstock completely uninstalled when fallback path runs
    monkeypatch.setitem(sys.modules, "vnstock", None)
    monkeypatch.setitem(sys.modules, "vnstock.explorer", None)
    monkeypatch.setitem(sys.modules, "vnstock.explorer.vci", None)
    monkeypatch.setitem(sys.modules, "vnstock.explorer.vci.company", None)
    res = corporate_actions.refresh_incremental(symbols=["FPT", "HPG"], use_batch=False)
    assert res["status"] == "unavailable"
    assert res["mode"] == "vnstock_unavailable"


def test_data_enricher_and_insider_scraper_use_db_without_vnstock(monkeypatch):
    """DataEnricher.fetch_dividend_events and scraper_insider read PostgreSQL first without vnstock warnings."""
    monkeypatch.setitem(sys.modules, "vnstock", None)

    fake_cursor = MagicMock()
    fake_cursor.fetchall.return_value = [
        (date(2026, 11, 20), "DIVIDEND", 1500.0, None, "VND", date(2026, 11, 21), "Cổ tức đợt 1", "vci_iq"),
    ]
    fake_cm = MagicMock()
    fake_cm.__enter__.return_value = fake_cursor
    fake_cm.__exit__.return_value = False

    with patch("app.infrastructure.database.pg_pool.get_cursor", return_value=fake_cm):
        divs = DataEnricher.fetch_dividend_events("FPT")
    assert len(divs["dividends"]) == 1
    assert divs["summary"]["dividend_amount"] == pytest.approx(1500.0)
    assert divs["summary"]["ex_dividend_date"] == "2026-11-20"

    monkeypatch.setattr(
        scraper_insider,
        "_get_db_insider_records",
        lambda sym, limit=20: {
            "shareholders": [{"name": "Trương Gia Bình", "ownershipPct": 6.8}],
            "officers": [{"name": "Nguyễn Văn Khoa", "position": "CEO"}],
        },
    )
    monkeypatch.setattr(scraper_insider, "_search_cafef_insider_news", AsyncMock(return_value=[]))
    insider = asyncio.run(scraper_insider.get_insider_data("FPT"))
    assert insider["shareholders"][0]["name"] == "Trương Gia Bình"
    assert insider["officers"][0]["name"] == "Nguyễn Văn Khoa"


# ---------------------------------------------------------------------------
# Bug #7 & OHLCV Auction Split Preservation
# ---------------------------------------------------------------------------


def test_market_session_manager_keeps_websocket_connected_until_1500_ict():
    """DNSE stream stays connected through 14:45-15:00 NEGOTIATED session and disconnects at 15:00 ICT."""
    mgr = MarketSessionManager()
    dt_1450_ict = datetime(2026, 11, 11, 14, 50, tzinfo=TZ_VN)
    assert mgr.get_market_state(dt_1450_ict) == MarketState.NEGOTIATED
    assert mgr.is_connected(dt_1450_ict) is True
    assert mgr.is_market_open(dt_1450_ict) is False

    dt_1500_ict = datetime(2026, 11, 11, 15, 0, tzinfo=TZ_VN)
    assert mgr.get_market_state(dt_1500_ict) == MarketState.CLOSED
    assert mgr.is_connected(dt_1500_ict) is False


def test_ohlcv_backfill_preserves_auction_volume_columns():
    """Daily OHLCV upsert must COALESCE volume_continuous/volume_atc/volume_ato so ATCAnomalyDetector retains split data."""
    src = inspect.getsource(ohlcv_backfill.upsert_today)
    assert "COALESCE(EXCLUDED.volume_continuous, market_data_daily.volume_continuous)" in src
    assert "COALESCE(EXCLUDED.volume_atc, market_data_daily.volume_atc)" in src
    assert "COALESCE(EXCLUDED.volume_ato, market_data_daily.volume_ato)" in src
