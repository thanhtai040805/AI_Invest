"""Test suite: Verify Plug-and-Play Agent Framework, Registry & Isolated Logging."""

import asyncio
import pytest
from app.core.base_agent import BaseAgent
from app.core.registry import AgentRegistry, agent_registry
from app.adapters.sag_connector import SAGConnector


class MockResearchAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="equity_research",
            state_tables=["factor_scores", "moat_profiles"],
            log_table="log_equity_research",
            enabled=True,
        )

    async def process(self, event_data: dict) -> dict:
        ticker = event_data.get("ticker", "FPT")
        return {
            "ticker": ticker,
            "data": {
                "f1_value": 72.5,
                "f2_quality": 88.0,
                "moat_score": 85.0,
                "css": 82.4,
                "conviction": "A+",
            },
            "trace": {
                "moat_source": "SAG_RAG",
                "calc_steps": "CSS = 0.3*F1 + 0.4*F2 ...",
            },
        }


class MockCIOAgent(BaseAgent):
    def __init__(self):
        super().__init__(
            agent_name="strategy_cio",
            state_tables=["strategic_allocations", "cio_resolutions"],
            log_table="log_strategy_cio",
            enabled=True,
        )

    async def process(self, event_data: dict) -> dict:
        return {
            "resolution_id": "res-123",
            "data": {
                "action": "APPROVE_CONDITIONAL",
                "approved_weight": 0.08,
            },
            "trace": {
                "deliberation": "Debate between Thesis (Bull) and CounterThesis (Bear) resolved.",
            },
        }


def test_agent_registry_plug_and_play():
    async def _test():
        # 1. Register agents
        research_agent = MockResearchAgent()
        cio_agent = MockCIOAgent()

        AgentRegistry.register(research_agent)
        AgentRegistry.register(cio_agent)

        # 2. Verify registered
        agents = AgentRegistry.list_agents()
        names = [a["name"] for a in agents]
        assert "agent_equity_research" in names
        assert "agent_strategy_cio" in names

        # 3. Dispatch event
        res = await AgentRegistry.dispatch("equity_research", {"ticker": "VNM"})
        assert res["status"] == "SUCCESS"
        assert res["agent"] == "equity_research"
        assert res["result"]["ticker"] == "VNM"

        # 4. Dynamic Unregister (No impact on other agents)
        assert AgentRegistry.unregister("strategy_cio") is True
        assert AgentRegistry.get_agent("strategy_cio") is None
        assert AgentRegistry.get_agent("equity_research") is not None

    asyncio.run(_test())


def test_sag_connector_fallback():
    async def _test():
        connector = SAGConnector(api_base="http://localhost:9999/api/v1")  # mock unavailable endpoint
        res = await connector.get_moat_assessment("FPT")
        assert res["ticker"] == "FPT"
        assert res["moat_score"] == 0.0
        assert res["status"] == "FALLBACK"

    asyncio.run(_test())
