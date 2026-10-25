import asyncio
from datetime import date, datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import experiments.replay_agent_pipeline as replay
from experiments.replay_agent_pipeline import fill_pending, ReplayAudit


def test_fill_pending_executes_order(monkeypatch):
    mock_repo = MagicMock()
    mock_repo.record_replay_execution.return_value = {
        "executed_price": 50000.0,
        "shares": 100,
    }
    monkeypatch.setattr(replay, "quote_history", lambda *_: [{
        "time": datetime(2026, 9, 23, 9, 15, 1, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh")),
        "bid": [],
        "offer": [{"price": 50.0, "qtty": 100}],
    }])
    bars = {"FPT": {"price": 50000.0, "source": "DNSE_1M_OPEN"}}
    pending = [{"symbol": "FPT", "side": "BUY", "quantity": 100}]
    fills_log = []

    remaining = fill_pending(
        day=date(2026, 9, 23),
        bars=bars,
        pending_orders=pending,
        repository=mock_repo,
        account_id="test_acc",
        mark_as_of=date(2026, 9, 22),
        fills_log=fills_log,
    )

    assert remaining == []
    assert len(fills_log) == 1
    assert fills_log[0]["symbol"] == "FPT"
    assert fills_log[0]["price"] == 50000.0
    mock_repo.record_replay_execution.assert_called_once()


def test_replay_audit_logs_event():
    audit = ReplayAudit()
    evt_id = audit.log_event("agent_test", "TEST_EVENT", {"foo": "bar"})
    assert evt_id is not None
    assert audit.last_hash == evt_id
    valid, count, err = audit.verify_full_chain()
    assert valid is True
    assert count == 1
    assert err is None


def test_replay_audit_counts_agent_calls_without_retaining_payloads():
    audit = ReplayAudit()
    audit.log_event("position_monitoring", "AGENT_RESULT", {"large": "payload"})
    audit.log_event("position_monitoring", "AGENT_ERROR", {"error": "failure"})

    assert audit.agent_calls == {"position_monitoring": 1}
    assert audit.agent_errors == 1
    assert audit.event_count == 2


def test_replay_position_cache_preserves_intraday_t25_unlock_time():
    position = {
        "ticker": "FPT",
        "quantity": 1000,
        "shares": 1000,
        "opened_at": datetime(2026, 8, 21, 10, 0),
    }
    morning = replay.replay_position_at_time(
        position, datetime(2026, 8, 25, 10, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    )
    afternoon = replay.replay_position_at_time(
        position, datetime(2026, 8, 25, 13, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    )

    assert (morning["available_shares"], morning["locked_t25_shares"]) == (0, 1000)
    assert (afternoon["available_shares"], afternoon["locked_t25_shares"]) == (1000, 0)


def test_replay_peak_is_persisted_once_at_session_close():
    repository = SimpleNamespace(update_position_peak_price=MagicMock())
    positions = [{"ticker": "FPT", "average_price": 20_000.0}]

    replay.persist_replay_peak_prices(
        repository, "account-a", positions, {"FPT": 25_550.0}, date(2026, 8, 25)
    )

    repository.update_position_peak_price.assert_called_once_with(
        "FPT", 25_550.0, user_id="account-a"
    )


def test_intraday_monitor_uses_cached_position_and_nav(monkeypatch):
    day = date(2026, 8, 25)
    event_time = datetime(2026, 8, 25, 13, 0, tzinfo=ZoneInfo("Asia/Ho_Chi_Minh"))
    position = {
        "ticker": "FPT", "quantity": 1000, "shares": 1000,
        "average_price": 20_000.0, "opened_at": datetime(2026, 8, 21, 10, 0),
        "available_shares": 0, "locked_t25_shares": 1000,
    }
    state = {"positions": {"FPT": position}, "nav": 1_000_000_000.0}
    dispatched = []

    async def dispatch(name, event):
        dispatched.append((name, event))
        return {"status": "SUCCESS", "result": {"data": {"stop_loss_orders": []}}}

    def unexpected_read(*_args, **_kwargs):
        raise AssertionError("unchanged intraday state should come from cache")

    monkeypatch.setattr(replay.AgentRegistry, "dispatch", dispatch)
    asyncio.run(replay.monitor_intraday_bar(
        day=day,
        bar={"symbol": "FPT", "time": event_time, "open": 20.0, "high": 20.2,
             "low": 19.9, "close": 20.1, "volume": 1000, "ma20_volume": 500},
        repository=SimpleNamespace(get_open_positions=unexpected_read, get_account_state=unexpected_read),
        account_id="account-a", mark_as_of=date(2026, 8, 24), pending_orders=[], fills_log=[],
        peak_prices={"FPT": 20_000.0}, active_symbols={"FPT"}, quote_cache={},
        market_repository=SimpleNamespace(),
        swing_low_cache={("FPT", date(2026, 8, 24)): 19_000.0},
        replay_state=state,
    ))

    event = dispatched[0][1]
    assert event["nav"] == 1_000_000_000.0
    assert event["positions"][0]["available_shares"] == 1000
    assert event["positions"][0]["locked_t25_shares"] == 0
