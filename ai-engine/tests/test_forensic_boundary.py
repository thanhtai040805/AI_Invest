import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.adapters.sag_evidence_agent import SAGEvidenceAgent
from app.domain.forensic_assessment import ForensicAssessment


def test_closed_payload_is_neutral_and_not_data_insufficient():
    assessment = ForensicAssessment.from_payload("FPT", {
        "status": "BLOCKED",
        "error_code": "SAG_CLOSED",
    })

    assert assessment.status == "SAG_CLOSED"
    assert assessment.decision == "DEFER"
    assert assessment.is_trade_safe is False
    assert assessment.to_dict().get("gil_flag") is None


def test_evidence_agent_does_not_call_closed_connector():
    connector = MagicMock(sag_analysis_hold=True)
    connector.get_gil_relationships = AsyncMock()

    async def run():
        result = await SAGEvidenceAgent(connector).assess("FPT")
        assert result.status == "SAG_CLOSED"
        connector.get_gil_relationships.assert_not_awaited()

    asyncio.run(run())


def test_hold_rejects_legacy_pass_payload(monkeypatch):
    monkeypatch.setenv("SAG_ANALYSIS_HOLD", "true")
    from app.config.settings import get_settings
    get_settings.cache_clear()
    try:
        assessment = ForensicAssessment.from_inputs("FPT", {"gil_flag": "PASS"})
        assert assessment.status == "SAG_CLOSED"
    finally:
        get_settings.cache_clear()


def test_evidence_agent_normalizes_provider_once():
    connector = MagicMock(sag_analysis_hold=False)
    connector.get_gil_relationships = AsyncMock(return_value={
        "analysis_status": "COMPLETE",
        "gil_flag": "PASS",
        "flow_signal": "NO_ABNORMAL_FLOW_OBSERVED",
    })

    async def run():
        result = await SAGEvidenceAgent(connector).assess("FPT")
        assert result.status == "COMPLETE"
        assert result.decision == "ALLOW"
        connector.get_gil_relationships.assert_awaited_once_with("FPT")
        roundtrip = ForensicAssessment.from_payload("FPT", result.to_dict())
        assert roundtrip.status == "COMPLETE"
        assert roundtrip.decision == "ALLOW"
        assert roundtrip.contract_version == "forensic-assessment.v1"

    asyncio.run(run())

