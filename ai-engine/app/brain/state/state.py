"""
Graph State - LangGraph state schema for debate flow
"""
from typing import TypedDict, List, Dict, Any, Optional
from enum import Enum


class DecisionType(str, Enum):
    """Types of decisions"""
    NORMAL = "normal"
    AUTO_TRADE = "auto_trade"
    RESEARCH_ONLY = "research_only"


class AgentRole(str, Enum):
    """Agent roles in the debate"""
    MARKET_ANALYST = "market_analyst"
    FUND_ANALYST = "fund_analyst"
    BULL_RESEARCHER = "bull_researcher"
    BEAR_RESEARCHER = "bear_researcher"
    PORTFOLIO_MANAGER = "portfolio_manager"
    RISK_GATE = "risk_gate"


class GraphState(TypedDict):
    """
    State for the LangGraph debate flow
    """
    # Input
    symbol: str
    user_query: str
    intent: str
    
    # Analysis results
    market_analysis: Optional[Dict[str, Any]]
    fundamental_analysis: Optional[Dict[str, Any]]
    bull_thesis: Optional[str]
    bear_thesis: Optional[str]
    debate_summary: Optional[str]
    
    # Decision
    decision: Optional[str]  # BUY, SELL, HOLD
    confidence: Optional[float]
    thesis: Optional[str]
    
    # Risk assessment
    risk_level: Optional[str]  # LOW, MEDIUM, HIGH
    risk_factors: Optional[List[str]]
    
    # Debate states (for multi-round debate)
    debate_round: int
    max_debate_rounds: int
    bull_history: str
    bear_history: str
    current_speaker: str  # "bull" or "bear"
    
    # Risk analysis states (for 3-analyst risk debate)
    risk_round: int
    max_risk_rounds: int
    aggressive_history: str
    conservative_history: str
    neutral_history: str
    current_risk_speaker: str  # "aggressive", "conservative", or "neutral"
    
    # Reflection
    reflection: Optional[str]  # Reflection on past decisions
    past_decisions: List[Dict[str, Any]]  # History of past decisions for learning
    
    # Signal processing
    portfolio_rating: Optional[str]  # Buy/Overweight/Hold/Underweight/Sell
    
    # Metadata
    decision_type: DecisionType
    models_used: List[str]
    execution_time_ms: Optional[int]
    
    # Error handling
    errors: List[str]
    warnings: List[str]
    
    # Audit
    session_id: str
    timestamp: str


class NodeOutput(TypedDict):
    """
    Output from a graph node
    """
    node_name: str
    status: str  # success, error, pending
    result: Optional[Dict[str, Any]]
    error: Optional[str]
    timestamp: str
