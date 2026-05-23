"""
Graph - LangGraph debate flow for trading analysis
"""

from .state import GraphState, NodeOutput, AgentRole, DecisionType
from .nodes import GraphNodes, graph_nodes
from .edges import (
    should_continue_debate,
    should_continue_risk_analysis,
    CONDITIONAL_EDGES,
    route_after_market_analysis,
    route_after_fund_analysis,
    route_after_bull_thesis,
    route_after_bear_thesis,
    route_after_portfolio_manager,
    route_after_risk_gate,
    route_after_aggressive_analyst,
    route_after_conservative_analyst,
    route_after_neutral_analyst,
)
from .reflection import Reflector, reflector
from .signal_processing import SignalProcessor, signal_processor
from .checkpointer import Checkpointer, checkpointer
from .concurrency import (
    ConcurrencyManager,
    concurrency_manager,
    AnalystType,
    AnalystSpec,
    ExecutionPlan,
)

__all__ = [
    # State
    "GraphState",
    "NodeOutput",
    "AgentRole",
    "DecisionType",
    # Nodes
    "GraphNodes",
    "graph_nodes",
    # Edges
    "should_continue_debate",
    "should_continue_risk_analysis",
    "CONDITIONAL_EDGES",
    "route_after_market_analysis",
    "route_after_fund_analysis",
    "route_after_bull_thesis",
    "route_after_bear_thesis",
    "route_after_portfolio_manager",
    "route_after_risk_gate",
    "route_after_aggressive_analyst",
    "route_after_conservative_analyst",
    "route_after_neutral_analyst",
    # Reflection
    "Reflector",
    "reflector",
    # Signal Processing
    "SignalProcessor",
    "signal_processor",
    # Checkpointer
    "Checkpointer",
    "checkpointer",
    # Concurrency
    "ConcurrencyManager",
    "concurrency_manager",
    "AnalystType",
    "AnalystSpec",
    "ExecutionPlan",
]
