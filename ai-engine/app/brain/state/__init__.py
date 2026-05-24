"""
Graph - LangGraph debate flow for trading analysis

Uses lazy imports via __getattr__ to avoid triggering the Gemini
client initialisation chain at package-import time.
"""

from __future__ import annotations

import importlib
from typing import Any

_LAZY_MAP: dict[str, str] = {
    # state
    "GraphState": ".state",
    "NodeOutput": ".state",
    "AgentRole": ".state",
    "DecisionType": ".state",
    # nodes
    "GraphNodes": ".nodes",
    "graph_nodes": ".nodes",
    # edges
    "should_continue_debate": ".edges",
    "should_continue_risk_analysis": ".edges",
    "CONDITIONAL_EDGES": ".edges",
    "route_after_market_analysis": ".edges",
    "route_after_fund_analysis": ".edges",
    "route_after_bull_thesis": ".edges",
    "route_after_bear_thesis": ".edges",
    "route_after_portfolio_manager": ".edges",
    "route_after_risk_gate": ".edges",
    "route_after_aggressive_analyst": ".edges",
    "route_after_conservative_analyst": ".edges",
    "route_after_neutral_analyst": ".edges",
    # reflection
    "Reflector": ".reflection",
    "reflector": ".reflection",
    # signal processing
    "SignalProcessor": ".signal_processing",
    "signal_processor": ".signal_processing",
    # checkpointer
    "Checkpointer": ".checkpointer",
    "checkpointer": ".checkpointer",
    # concurrency
    "ConcurrencyManager": ".concurrency",
    "concurrency_manager": ".concurrency",
    "AnalystType": ".concurrency",
    "AnalystSpec": ".concurrency",
    "ExecutionPlan": ".concurrency",
}


def __getattr__(name: str) -> Any:
    """Lazy-load module attributes."""
    if name in _LAZY_MAP:
        mod = importlib.import_module(_LAZY_MAP[name], __package__)
        attr = getattr(mod, name)
        # Cache on the package module for subsequent fast access
        globals()[name] = attr
        return attr
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = list(_LAZY_MAP.keys())
