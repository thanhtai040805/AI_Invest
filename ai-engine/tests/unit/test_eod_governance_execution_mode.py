import asyncio

import pytest

from app.core.registry import AgentRegistry
from app.domain.pipeline.eod_pipeline import EODPipelineRunner


class FakeStorage:
    def fetch_all(self, query, params=None):
        if "COUNT(*)" in query:
            return [(1,)]
        return []


@pytest.mark.parametrize(
    ("configured_mode", "expected_mode"),
    [("SHADOW_RUNNER", "SHADOW_RUNNER"), ("LIVE", "DISABLED"), ("unknown", "SHADOW_RUNNER")],
)
def test_eod_governance_audit_uses_effective_mode_without_fake_heartbeat(
    monkeypatch, configured_mode, expected_mode
):
    monkeypatch.setenv("MULTI_AGENT_MODE", configured_mode)
    governance_events = []

    async def fake_dispatch(agent_name, event_data):
        if agent_name == "system_governance":
            governance_events.append(event_data)
            return {"status": "SUCCESS", "result": {"data": {"system_status": "COMPLIANT"}}}
        if agent_name == "market_surveillance":
            return {"status": "SUCCESS", "result": {"data": {"current_regime": "NORMAL"}}}
        return {"status": "SUCCESS", "result": {"data": {}}}

    monkeypatch.setattr(AgentRegistry, "dispatch", fake_dispatch)
    result = asyncio.run(EODPipelineRunner(storage=FakeStorage()).run(target_date="2026-10-13"))

    assert result["status"] == "SUCCESS"
    assert governance_events[0]["execution_mode"] == expected_mode
    assert "broker_heartbeat" not in governance_events[0]
