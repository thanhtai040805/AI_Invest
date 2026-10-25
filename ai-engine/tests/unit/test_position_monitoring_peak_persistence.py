import asyncio
from contextlib import nullcontext
from unittest.mock import MagicMock

import app.domain.agents.position_monitoring as position_monitoring
from app.domain.agents.position_monitoring import PositionMonitoringAgent


class StorageStub:
    def __init__(self):
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))


class PeakRepositoryStub:
    account_id = "account-a"

    def __init__(self):
        self.storage = StorageStub()
        self.peaks = {}
        self.account_state_reads = 0
        self.peak_writes = 0

    def get_account_state(self):
        self.account_state_reads += 1
        return {"total_nav": 1_000_000_000.0}

    def update_position_peak_price(self, ticker, observed_peak, user_id=None):
        self.peak_writes += 1
        key = (user_id or self.account_id, ticker)
        self.peaks[key] = max(self.peaks.get(key, 0.0), observed_peak)
        return self.peaks[key]


def run_monitor(agent, price, user_id="account-a", is_replay=False):
    return asyncio.run(agent.process({
        "current_time": "2026-10-13T10:00:00+07:00",
        "nav": 1_000_000_000.0,
        "auto_dispatch": False,
        "is_replay": is_replay,
        "positions": [{
            "ticker": "PVP",
            "user_id": user_id,
            "quantity": 1_400,
            "available_shares": 1_400,
            "entry_price": 19_825.0,
            "current_price": price,
            "peak_price": 25_550.0 if is_replay else None,
        }],
    }))


def test_trailing_peak_survives_agent_restart_and_uses_current_price():
    repository = PeakRepositoryStub()
    first_agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)

    peak_result = run_monitor(first_agent, 25_550.0)
    assert peak_result["data"]["stop_loss_triggered"] is False
    assert repository.account_state_reads == 0

    restarted_agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)
    after_restart = run_monitor(restarted_agent, 22_700.0)

    assert after_restart["data"]["stop_loss_triggered"] is True
    order = after_restart["data"]["stop_loss_orders"][0]
    assert order["rule_level"] == "TRAILING_STOP"
    assert order["price"] == 22_700.0
    assert "đỉnh 25,550" in order["reason"]
    assert repository.account_state_reads == 0


def test_trailing_peak_is_scoped_to_the_position_account():
    repository = PeakRepositoryStub()
    agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)

    run_monitor(agent, 25_550.0, user_id="account-a")
    other_account = run_monitor(agent, 22_700.0, user_id="account-b")

    assert other_account["data"]["stop_loss_triggered"] is False
    assert repository.account_state_reads == 0


def test_replay_uses_cached_peak_without_writing_db_each_bar():
    repository = PeakRepositoryStub()
    agent = PositionMonitoringAgent(repository=repository, auto_dispatch=False)

    result = run_monitor(agent, 22_700.0, is_replay=True)

    assert result["data"]["stop_loss_orders"][0]["rule_level"] == "TRAILING_STOP"
    assert repository.peak_writes == 0
    assert not any("position_health_ticks" in call[0][0] for call in repository.storage.calls)
    assert len(agent._replay_health_ticks) == 1
    writes_before_flush = len(repository.storage.calls)

    agent.flush_replay_state()

    assert len(repository.storage.calls) == writes_before_flush + 1
    assert sum("position_health_ticks" in call[0][0] for call in repository.storage.calls) == 1


def test_replay_audit_rows_are_inserted_as_a_batch(monkeypatch):
    agent = PositionMonitoringAgent(repository=PeakRepositoryStub(), auto_dispatch=False)
    cursor = MagicMock()
    rows_written = []
    execute_values = MagicMock(side_effect=lambda _cur, _sql, rows, **_kwargs: rows_written.extend(rows))
    connection = type("Connection", (), {"cursor": lambda self: nullcontext(cursor)})()
    monkeypatch.setattr(position_monitoring, "get_conn", lambda: nullcontext(connection))
    monkeypatch.setattr(position_monitoring, "execute_values", execute_values)

    asyncio.run(agent._log_audit_trace(
        {"is_replay": True, "target_date": "2026-10-13"},
        {},
        {"ticker": "PVP", "pnl_pct": 1.25, "stop_loss_triggered": False, "thesis_invalidated": False},
        "SUCCESS",
    ))
    assert execute_values.call_count == 0

    agent.flush_replay_audit()

    assert execute_values.call_count == 1
    assert len(rows_written) == 1
    assert rows_written[0][:4] == ("PVP", 1.25, False, False)
    assert rows_written[0][4].isoformat() == "2026-10-13T09:45:00+07:00"
    assert agent._replay_audit_rows == []
