import asyncio
import json
from datetime import date, datetime, timezone
from threading import Lock
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.domain.agents import market_surveillance as surveillance
from app.core.registry import AgentRegistry
from app.infrastructure.database import pg_pool
from app.infrastructure.external_api import market_data_service as market_service


VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")


@pytest.fixture
def agent_clock(monkeypatch):
    class Clock(datetime):
        instant = datetime(2026, 10, 23, 2, 45, tzinfo=timezone.utc)

        @classmethod
        def now(cls, tz=None):
            value = cls.instant.astimezone(tz) if tz else cls.instant.replace(tzinfo=None)
            return cls.fromisoformat(value.isoformat())

    monkeypatch.setattr(surveillance, "datetime", Clock)
    monkeypatch.setattr(surveillance, "get_conn", MagicMock())
    agent = surveillance.MarketSurveillanceAgent()
    hydrated = {
        "effective_date": date(2026, 10, 22),
        "df_vni": pd.DataFrame({"close": [1000.0] * 30}),
        "index_returns": pd.Series([0.001] * 30),
        "breadth_above_ma50_pct": 40.0,
        "breadth_above_ma20_pct": 45.0,
        "breadth_ma20_valid": True,
        "market_breadth_stats": {"adv_decl_ratio": 1.0},
    }
    agent._sync_hydrate_data = MagicMock(return_value=hydrated)
    agent.hmm_engine.infer_daily = MagicMock(return_value={
        "BEAR_MARKET": 0.72, "RANGE_BOUND": 0.28, "BULL_MARKET": 0.0,
    })
    agent.garch_engine.forecast_volatility = MagicMock(return_value=0.18)
    agent.garch_engine.calculate_cash_allocation = MagicMock(return_value=0.15)
    agent.market_data_service = MagicMock()
    agent.market_data_service.get_breadth = AsyncMock(return_value={})
    agent.market_data_service.get_indices = AsyncMock(return_value={"indices": []})
    agent.market_data_service.get_snapshot = AsyncMock(return_value={"stocks": []})
    return agent, Clock


def process(agent, **event):
    return asyncio.run(agent.process({"check_db_status": False, **event}))


def test_live_utc_host_matches_replay_session_and_regime_with_same_eod_inputs(agent_clock):
    agent, _ = agent_clock
    live = process(agent, market_data_date="2026-10-22")
    agent.market_data_service.get_snapshot.assert_awaited_once_with(exchange="HOSE")

    replay = process(
        agent, current_time="2026-10-23T09:45:00+07:00",
        market_data_date="2026-10-22", is_replay=True,
    )
    assert live == replay
    assert live["data"]["session_code"] == "CONTINUOUS_AM"
    assert live["data"]["current_regime"] == "BEAR_MARKET"
    assert live["data"]["hmm_probabilities"]["BEAR_MARKET"] == 0.72


@pytest.mark.parametrize("current_time", [
    "2026-10-23T02:45:00+00:00", "2026-10-23T09:45:00+07:00",
    "2026-10-23T09:45:00",
])
def test_explicit_times_resolve_to_vietnam_session(agent_clock, current_time):
    agent, _ = agent_clock
    result = process(agent, current_time=current_time)
    assert result["data"]["session_code"] == "CONTINUOUS_AM"
    agent.market_data_service.get_snapshot.assert_awaited_once()


def test_live_uses_available_realtime_snapshot_without_changing_hmm(agent_clock):
    agent, _ = agent_clock
    agent.market_data_service.get_snapshot.return_value = {"stocks": [{
        "symbol": "HPG", "price": 25000, "floor": 23000,
        "ceiling": 27000, "changePercent": 1.0, "volume": 1000,
    }]}
    result = process(agent)
    assert result["trace"]["data_source"] == "DNSE_REALTIME_STREAM"
    assert result["data"]["session_code"] == "CONTINUOUS_AM"
    assert result["data"]["current_regime"] == "BEAR_MARKET"
    assert result["data"]["ceiling_count"] == 0


@pytest.mark.parametrize("index_key", ["name", "symbol"])
@pytest.mark.parametrize(("change_percent", "context"), [
    (-0.1, "Normal"), (-0.5, "Normal"), (-3.1, "Crisis"),
])
def test_live_index_percent_matches_service_contract(agent_clock, index_key, change_percent, context):
    agent, _ = agent_clock
    agent.hmm_engine.infer_daily.return_value = {
        "BEAR_MARKET": 0.2, "RANGE_BOUND": 0.8, "BULL_MARKET": 0.0,
    }
    agent.market_data_service.get_snapshot.return_value = {"stocks": [{
        "symbol": "HPG", "price": 25000, "changePercent": 1.0,
    }], "stale": False}
    agent.market_data_service.get_indices.return_value = {
        "indices": [{index_key: "VNINDEX", "changePercent": change_percent}],
    }
    agent.distortion_monitor.analyze_distortion = MagicMock(return_value={"is_distorted": False})
    result = process(agent)
    assert agent.distortion_monitor.analyze_distortion.call_args.kwargs["vni_return"] == pytest.approx(change_percent / 100)
    assert result["data"]["session_context"] == context
    assert result["data"]["current_regime"] == ("BEAR_MARKET" if context == "Crisis" else "RANGE_BOUND")


def test_live_price_bands_use_normalized_service_fields_and_require_a_price(agent_clock):
    agent, _ = agent_clock
    agent.market_data_service.get_snapshot.return_value = {"stocks": [
        {"symbol": "AAA", "price": 9310, "floor": 9310, "ceiling": 10690, "changePercent": -6.89},
        {"symbol": "BBB", "price": 10690, "floor": 9310, "ceiling": 10690, "changePercent": 6.89},
        {"symbol": "CCC", "price": 0, "floor": 9310, "ceiling": 10690, "changePercent": None},
    ], "stale": False}
    result = process(agent)
    assert result["data"]["floor_locked_count"] == 1
    assert result["data"]["ceiling_count"] == 1


def test_stale_postgres_snapshot_keeps_eod_fallback(agent_clock):
    agent, _ = agent_clock
    agent.market_data_service.get_snapshot.return_value = {"stocks": [
        {"symbol": f"STOCK{i}", "price": 9300, "changePercent": -7.0}
        for i in range(20)
    ], "source": "postgres", "stale": True}
    result = process(agent)
    assert result["trace"]["data_source"] == "POSTGRESQL_EOD"
    assert result["data"]["session_context"] == "Normal"
    assert result["data"]["floor_locked_count"] == 0


def test_missing_live_index_change_keeps_known_eod_return(agent_clock):
    agent, _ = agent_clock
    agent._sync_hydrate_data.return_value["vni_return"] = -0.04
    agent.market_data_service.get_snapshot.return_value = {
        "stocks": [{"symbol": "HPG", "price": 25000, "changePercent": 1.0}],
    }
    agent.market_data_service.get_indices.return_value = {
        "indices": [{"name": "VNINDEX", "changePercent": None}],
    }
    result = process(agent)
    assert result["data"]["session_context"] == "Crisis"


def test_live_date_rolls_over_in_vietnam_before_utc(agent_clock):
    agent, clock = agent_clock
    clock.instant = datetime(2026, 10, 22, 18, 45, tzinfo=timezone.utc)
    result = process(agent)
    agent._sync_hydrate_data.assert_called_once_with(date(2026, 10, 23))
    assert result["data"]["session_code"] == "CLOSED"


def test_historical_replay_does_not_fetch_today_realtime_data(agent_clock):
    agent, _ = agent_clock
    result = process(
        agent, current_time="2026-08-24T09:45:00+07:00",
        market_data_date="2026-08-23", is_replay=True,
    )
    assert result["data"]["session_code"] == "CONTINUOUS_AM"
    agent._sync_hydrate_data.assert_called_once_with(date(2026, 8, 23))
    agent.market_data_service.get_snapshot.assert_not_awaited()


def test_weekend_is_determined_in_vietnam_time(agent_clock):
    agent, _ = agent_clock
    result = process(agent, current_time="2026-10-23T18:00:00+00:00")
    agent._sync_hydrate_data.assert_called_once_with(date(2026, 10, 24))
    assert result["data"]["session_code"] == "CLOSED"


@pytest.mark.parametrize(("day", "session"), [
    ("2026-08-24", "CLOSED"), (date(2026, 8, 24), "NEGOTIATED"),
])
def test_eod_date_only_input_keeps_existing_session(agent_clock, day, session):
    agent, _ = agent_clock
    result = process(agent, date=day)
    assert result["data"]["session_code"] == session
    agent._sync_hydrate_data.assert_called_once_with(date(2026, 8, 24))
    agent.market_data_service.get_snapshot.assert_not_awaited()


@pytest.mark.parametrize("feed_state", ["fresh", "stale", "empty", "error"])
def test_live_registry_dispatch_with_real_market_data_facade(agent_clock, monkeypatch, feed_state):
    agent, _ = agent_clock
    baseline = {
        "symbol": "HPG", "exchange": "HOSE", "price": 25000,
        "floor": 23000, "ceiling": 27000, "volume": 1000,
        "changePercent": 1.0, "source": "postgres", "stale": True,
    }
    service = object.__new__(market_service.MarketDataService)
    service._hub = SimpleNamespace(
        _lock=Lock(), _universe_symbols={"HPG"},
        _quotes={"HPG": {**baseline, "source": "dnse-ws", "stale": False}} if feed_state == "fresh" else {},
        _market_index={"VNINDEX": {"name": "VNINDEX", "changePercent": -0.1}},
        get_market_board_snapshot=lambda: {"stocks": [] if feed_state == "empty" else [baseline]},
    )
    service._rest = SimpleNamespace(is_live=False)
    redis = MagicMock()
    redis.get.return_value = None
    monkeypatch.setattr(market_service, "get_redis", lambda: redis)
    if feed_state == "error":
        service.get_snapshot = AsyncMock(side_effect=RuntimeError("Feed unavailable"))
    agent.market_data_service = service
    monkeypatch.setattr(AgentRegistry, "_agents", {"market_surveillance": agent})
    monkeypatch.setattr(pg_pool, "get_conn", surveillance.get_conn)

    result = asyncio.run(AgentRegistry.dispatch("market_surveillance", {
        "target_date": "2026-10-23", "market_data_date": "2026-10-23",
        "is_replay": False, "replay_account_id": "test-account",
    }))

    assert result["status"] == "SUCCESS"
    assert result["result"]["data"]["session_code"] == "CONTINUOUS_AM"
    assert result["result"]["data"]["current_regime"] == "BEAR_MARKET"
    assert result["result"]["data"]["session_context"] == "Normal"
    assert result["result"]["trace"]["data_source"] == (
        "DNSE_REALTIME_STREAM" if feed_state == "fresh" else "POSTGRESQL_EOD"
    )
    agent._sync_hydrate_data.assert_called_once_with(date(2026, 10, 23))
    cursor = surveillance.get_conn.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value
    audit_calls = [call for call in cursor.execute.call_args_list if "INSERT INTO log_market_surveillance" in call.args[0]]
    assert len(audit_calls) == 1
    assert json.loads(audit_calls[0].args[1][3]) == result["result"]["data"]
