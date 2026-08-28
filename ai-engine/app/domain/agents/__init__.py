"""12 Multi-Agent Semantic Registry Package (IOS v5.1)

Tập trung 12 Agents nghiệp vụ độc lập theo chuẩn Semantic Plug-and-Play.
Tự động đăng ký toàn bộ 12 Agents vào AgentRegistry khi package được nạp.
"""

from app.core.registry import AgentRegistry

# Import 12 Agent Classes
from app.domain.agents.market_surveillance import MarketSurveillanceAgent
from app.domain.agents.universe_discovery import UniverseDiscoveryAgent
from app.domain.agents.equity_research import EquityResearchAgent
from app.domain.agents.investment_thesis import InvestmentThesisAgent
from app.domain.agents.counter_thesis import CounterThesisAgent
from app.domain.agents.portfolio_risk import PortfolioRiskAgent
from app.domain.agents.portfolio_allocation import PortfolioAllocationAgent
from app.domain.agents.trade_execution import TradeExecutionAgent
from app.domain.agents.position_monitoring import PositionMonitoringAgent
from app.domain.agents.reinforcement_learning import ReinforcementLearningAgent
from app.domain.agents.system_governance import SystemGovernanceAgent
from app.domain.agents.strategy_cio import StrategyCIOAgent


def initialize_all_agents() -> None:
    """Khởi tạo và đăng ký toàn bộ 12 Agents vào Registry."""
    agents = [
        MarketSurveillanceAgent(),
        UniverseDiscoveryAgent(),
        EquityResearchAgent(),
        InvestmentThesisAgent(),
        CounterThesisAgent(),
        PortfolioRiskAgent(),
        PortfolioAllocationAgent(),
        TradeExecutionAgent(),
        PositionMonitoringAgent(),
        ReinforcementLearningAgent(),
        SystemGovernanceAgent(),
        StrategyCIOAgent(),
    ]
    for agent in agents:
        AgentRegistry.register(agent)


# Tự động khởi tạo ngay khi import
initialize_all_agents()

__all__ = [
    "MarketSurveillanceAgent",
    "UniverseDiscoveryAgent",
    "EquityResearchAgent",
    "InvestmentThesisAgent",
    "CounterThesisAgent",
    "PortfolioRiskAgent",
    "PortfolioAllocationAgent",
    "TradeExecutionAgent",
    "PositionMonitoringAgent",
    "ReinforcementLearningAgent",
    "SystemGovernanceAgent",
    "StrategyCIOAgent",
    "initialize_all_agents",
]
