"""
Graph Edges - LangGraph edge definitions for the debate flow
"""
from typing import Literal, Callable
from .state import GraphState, DecisionType


def should_continue_debate(state: GraphState) -> Literal["portfolio_manager", "bull_researcher", "bear_researcher"]:
    """
    Decide whether to continue debate or move to Portfolio Manager
    Supports multi-round debate between Bull and Bear researchers
    
    Args:
        state: Current graph state
        
    Returns:
        Next node name
    """
    debate_round = state.get("debate_round", 0)
    max_debate_rounds = state.get("max_debate_rounds", 1)
    
    # Check if max rounds reached
    if debate_round >= 2 * max_debate_rounds:  # Each round has 2 turns (bull + bear)
        return "portfolio_manager"
    
    # Determine next speaker based on current speaker
    current_speaker = state.get("current_speaker", "bear")
    
    if current_speaker == "bull":
        return "bear_researcher"
    else:
        return "bull_researcher"


def should_proceed_to_risk_gate(state: GraphState) -> Literal["risk_gate", "end"]:
    """
    Decide whether to proceed to Risk Gate or end
    
    Args:
        state: Current graph state
        
    Returns:
        Next node name
    """
    # If decision exists, proceed to risk gate
    if state.get("decision"):
        return "risk_gate"
    
    # Otherwise end
    return "end"


def should_auto_trade(state: GraphState) -> bool:
    """
    Check if this is an auto-trade decision
    
    Args:
        state: Current graph state
        
    Returns:
        bool: True if auto-trade
    """
    return state.get("decision_type") == DecisionType.AUTO_TRADE


def has_errors(state: GraphState) -> bool:
    """
    Check if there are errors in the state
    
    Args:
        state: Current graph state
        
    Returns:
        bool: True if errors exist
    """
    return len(state.get("errors", [])) > 0


def get_confidence_threshold(state: GraphState) -> float:
    """
    Get confidence threshold based on decision type
    
    Args:
        state: Current graph state
        
    Returns:
        float: Confidence threshold
    """
    if state.get("decision_type") == DecisionType.AUTO_TRADE:
        return 0.85  # Higher threshold for auto-trade
    else:
        return 0.70  # Lower threshold for research


# Edge routing functions
def route_after_market_analysis(state: GraphState) -> str:
    """Route after market analysis"""
    if has_errors(state):
        return "end"
    return "fund_analyst"


def route_after_fund_analysis(state: GraphState) -> str:
    """Route after fundamental analysis"""
    if has_errors(state):
        return "end"
    return "bull_researcher"


def route_after_bull_thesis(state: GraphState) -> str:
    """Route after bull thesis"""
    if has_errors(state):
        return "portfolio_manager"  # Skip bear if error
    return "bear_researcher"


def route_after_bear_thesis(state: GraphState) -> str:
    """Route after bear thesis"""
    return "portfolio_manager"


def route_after_portfolio_manager(state: GraphState) -> str:
    """Route after portfolio manager - to risk analysis or end"""
    if not state.get("decision"):
        return "end"
    
    # Check if risk analysis is needed
    decision_type = state.get("decision_type")
    if decision_type == DecisionType.AUTO_TRADE:
        return "aggressive_analyst"  # Start risk analysis
    else:
        return "risk_gate"  # Simple risk check


def route_after_risk_gate(state: GraphState) -> str:
    """Route after risk gate"""
    return "end"


def should_continue_risk_analysis(state: GraphState) -> Literal["portfolio_manager", "aggressive_analyst", "conservative_analyst", "neutral_analyst"]:
    """
    Decide whether to continue risk analysis or move to Portfolio Manager
    Supports 3-analyst risk debate (Aggressive, Conservative, Neutral)
    
    Args:
        state: Current graph state
        
    Returns:
        Next node name
    """
    risk_round = state.get("risk_round", 0)
    max_risk_rounds = state.get("max_risk_rounds", 1)
    
    # Check if max rounds reached
    if risk_round >= 3 * max_risk_rounds:  # Each round has 3 turns (aggressive + conservative + neutral)
        return "portfolio_manager"
    
    # Determine next speaker based on current speaker
    current_speaker = state.get("current_risk_speaker", "aggressive")
    
    if current_speaker == "aggressive":
        return "conservative_analyst"
    elif current_speaker == "conservative":
        return "neutral_analyst"
    else:
        return "aggressive_analyst"


def route_after_aggressive_analyst(state: GraphState) -> str:
    """Route after aggressive analyst"""
    if has_errors(state):
        return "portfolio_manager"  # Skip to end if error
    
    # Update state for risk analysis
    state["risk_round"] = state.get("risk_round", 0) + 1
    state["current_risk_speaker"] = "aggressive"
    
    return should_continue_risk_analysis(state)


def route_after_conservative_analyst(state: GraphState) -> str:
    """Route after conservative analyst"""
    # Update state for risk analysis
    state["risk_round"] = state.get("risk_round", 0) + 1
    state["current_risk_speaker"] = "conservative"
    
    return should_continue_risk_analysis(state)


def route_after_neutral_analyst(state: GraphState) -> str:
    """Route after neutral analyst"""
    # Update state for risk analysis
    state["risk_round"] = state.get("risk_round", 0) + 1
    state["current_risk_speaker"] = "neutral"
    
    return should_continue_risk_analysis(state)


# Conditional edge mappings
CONDITIONAL_EDGES = {
    "market_analyst": route_after_market_analysis,
    "fund_analyst": route_after_fund_analysis,
    "bull_researcher": route_after_bull_thesis,
    "bear_researcher": route_after_bear_thesis,
    "portfolio_manager": route_after_portfolio_manager,
    "risk_gate": route_after_risk_gate,
    "aggressive_analyst": route_after_aggressive_analyst,
    "conservative_analyst": route_after_conservative_analyst,
    "neutral_analyst": route_after_neutral_analyst,
}
