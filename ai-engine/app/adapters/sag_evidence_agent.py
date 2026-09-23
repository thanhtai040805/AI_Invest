"""Single SAG boundary for producing neutral forensic evidence."""

from __future__ import annotations

from typing import Any

from app.adapters.sag_connector import SAGConnector, sag_connector
from app.domain.forensic_assessment import ForensicAssessment


class SAGEvidenceAgent:
    """Agent-like producer; intentionally not registered as a decision agent."""

    def __init__(self, connector: SAGConnector | Any | None = None):
        self.connector = connector or sag_connector

    @property
    def analysis_closed(self) -> bool:
        return bool(self.connector.sag_analysis_hold)

    def closed_assessment(self, ticker: str = "") -> ForensicAssessment:
        return ForensicAssessment.closed(ticker)

    async def assess(self, ticker: str) -> ForensicAssessment:
        if self.analysis_closed:
            return self.closed_assessment(ticker)
        result = await self.connector.get_gil_relationships(ticker)
        return ForensicAssessment.from_payload(ticker, result)


sag_evidence_agent = SAGEvidenceAgent()
