from datetime import date
from unittest.mock import MagicMock
from experiments.replay_agent_pipeline import fill_pending, ReplayAudit


def test_fill_pending_executes_order():
    mock_repo = MagicMock()
    mock_repo.record_replay_execution.return_value = {
        "executed_price": 50000.0,
        "shares": 100,
    }
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
    assert len(audit.events) == 1
    valid, count, err = audit.verify_full_chain()
    assert valid is True
    assert count == 1
    assert err is None
