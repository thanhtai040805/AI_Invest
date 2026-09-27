import asyncio
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from app.core.base_agent import BaseAgent


class AuditAgent(BaseAgent):
    def __init__(self):
        super().__init__("investment_thesis", [], "log_investment_thesis")

    async def process(self, event_data):
        return {"data": {"ticker": "HPG", "status": "WAIT_OR_SKIP"}}


def test_replay_audit_keeps_simulated_day_separate_from_write_time(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr("app.infrastructure.database.pg_pool.get_conn", connection)
    agent = AuditAgent()
    asyncio.run(agent.run_event({"target_date": "2026-09-21", "market_data_date": "2026-09-18", "is_replay": True}))
    sql, params = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert params[-2:] == (date(2026, 9, 21), True)
    assert "analysis_date, is_replay, created_at" in sql
    assert sql.endswith("CURRENT_TIMESTAMP)")


def test_live_analysis_day_uses_vietnam_timezone(monkeypatch):
    connection = MagicMock()
    monkeypatch.setattr("app.infrastructure.database.pg_pool.get_conn", connection)
    asyncio.run(AuditAgent().run_event({"target_date": datetime(2026, 9, 20, 18, tzinfo=timezone.utc)}))
    _, params = connection.return_value.__enter__.return_value.cursor.return_value.__enter__.return_value.execute.call_args.args
    assert params[-2:] == (date(2026, 9, 21), False)


def test_replay_without_day_does_not_mislabel_audit_as_today(monkeypatch, caplog):
    connection = MagicMock()
    monkeypatch.setattr("app.infrastructure.database.pg_pool.get_conn", connection)
    asyncio.run(AuditAgent().run_event({"is_replay": True}))
    connection.assert_not_called()
    assert "Replay audit requires target_date" in caplog.text
