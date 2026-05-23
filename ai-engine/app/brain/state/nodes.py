"""
Graph Nodes - LangGraph debate nodes for the trading system
"""
import logging
from typing import Dict, Any, Optional
from datetime import datetime
import uuid

from .state import GraphState, NodeOutput, AgentRole, DecisionType
from app.brain.providers.orchestrator import graph_orchestrator
from app.brain.dataflows.vendors.vn import OHLCVTool, IndicatorsTool, FundamentalsTool, NewsTool

logger = logging.getLogger(__name__)


class GraphNodes:
    """
    Graph nodes for the LangGraph debate flow
    """
    
    def __init__(self):
        """Initialize Graph Nodes"""
        self.ohlcv_tool = OHLCVTool()
        self.indicators_tool = IndicatorsTool()
        self.fundamentals_tool = FundamentalsTool()
        self.news_tool = NewsTool()
        logger.info("Graph Nodes initialized")
    
    async def market_analyst_node(self, state: GraphState) -> GraphState:
        """
        Market Analyst node - Analyzes price action and technical indicators
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Market Analyst node executing for {state['symbol']}")
        
        try:
            # Save checkpoint before execution
            from .checkpointer import checkpointer
            checkpointer.save_checkpoint(
                session_id=state.get("session_id"),
                state=state,
                node_name="market_analyst",
            )
            
            # Get OHLCV data
            ohlcv_data = await self.ohlcv_tool.get_ohlcv(
                symbol=state["symbol"],
                days=30,
            )
            
            # Calculate indicators
            indicators = self.indicators_tool.get_all_indicators(ohlcv_data)
            
            # Get latest price
            latest_price = await self.ohlcv_tool.get_latest_price(state["symbol"])
            
            # Analyze with LLM
            prompt = f"""Analyze the market data for {state['symbol']}:

Current Price: {latest_price.get('price')}
Change: {latest_price.get('change')}%
RSI: {indicators.get('rsi')}
MACD: {indicators.get('macd')}
MACD Signal: {indicators.get('macd_signal')}
SMA 20: {indicators.get('sma_20')}
SMA 50: {indicators.get('sma_50')}

Provide technical analysis including:
1. Trend direction
2. Support/Resistance levels
3. Momentum indicators interpretation
4. Technical rating (BUY/SELL/HOLD)"""

            result = await graph_orchestrator.execute_node(
                node_name="market_analyst",
                state=state,
                temperature=0.7,
            )
            
            state["market_analysis"] = {
                "technical_data": indicators,
                "latest_price": latest_price,
                "analysis": result.get("content"),
                "confidence": result.get("confidence"),
            }
            
            logger.info(f"Market Analyst node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Market Analyst node failed: {str(e)}")
            state["errors"].append(f"Market Analyst error: {str(e)}")
        
        return state
    
    async def fund_analyst_node(self, state: GraphState) -> GraphState:
        """
        Fundamental Analyst node - Analyzes financial metrics
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Fundamental Analyst node executing for {state['symbol']}")
        
        try:
            # Get fundamental data
            fundamentals = await self.fundamentals_tool.get_fundamentals(state["symbol"])
            
            # Get financial ratios
            ratios = await self.fundamentals_tool.get_financial_ratios(state["symbol"])
            
            # Analyze with LLM
            prompt = f"""Analyze the fundamental data for {state['symbol']}:

P/E Ratio: {fundamentals.get('pe_ratio')}
P/B Ratio: {fundamentals.get('pb_ratio')}
ROE: {fundamentals.get('roe')}
EPS: {fundamentals.get('eps')}
Market Cap: {fundamentals.get('market_cap')}
Dividend Yield: {fundamentals.get('dividend_yield')}

Valuation: {ratios.get('valuation')}
Profitability: {ratios.get('profitability')}
Overall Score: {ratios.get('overall_score')}

Provide fundamental analysis including:
1. Valuation assessment
2. Profitability analysis
3. Growth prospects
4. Fundamental rating (BUY/SELL/HOLD)"""

            result = await graph_orchestrator.execute_node(
                node_name="fund_analyst",
                state=state,
                temperature=0.7,
            )
            
            state["fundamental_analysis"] = {
                "fundamentals": fundamentals,
                "ratios": ratios,
                "analysis": result.get("content"),
                "confidence": result.get("confidence"),
            }
            
            logger.info(f"Fundamental Analyst node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Fundamental Analyst node failed: {str(e)}")
            state["errors"].append(f"Fundamental Analyst error: {str(e)}")
        
        return state
    
    async def bull_researcher_node(self, state: GraphState) -> GraphState:
        """
        Bull Researcher node - Constructs bullish thesis
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Bull Researcher node executing for {state['symbol']}")
        
        try:
            # Get news sentiment
            sentiment = await self.news_tool.get_sentiment(state["symbol"], days=7)
            
            # Construct bullish thesis
            prompt = f"""You are a Bull Researcher. Construct a bullish thesis for {state['symbol']}.

Market Analysis: {state.get('market_analysis', {}).get('analysis', 'N/A')}
Fundamental Analysis: {state.get('fundamental_analysis', {}).get('analysis', 'N/A')}
News Sentiment: {sentiment.get('sentiment')} (score: {sentiment.get('sentiment_score')})

Provide a compelling bullish thesis including:
1. Key bullish drivers
2. Catalysts for price increase
3. Target price (if applicable)
4. Risk factors (to be addressed by Bear)"""

            result = await graph_orchestrator.execute_node(
                node_name="bull_researcher",
                state=state,
                temperature=0.8,
            )
            
            state["bull_thesis"] = result.get("content")
            state["models_used"].append(result.get("model"))
            
            logger.info(f"Bull Researcher node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Bull Researcher node failed: {str(e)}")
            state["errors"].append(f"Bull Researcher error: {str(e)}")
        
        return state
    
    async def bear_researcher_node(self, state: GraphState) -> GraphState:
        """
        Bear Researcher node - Constructs bearish thesis
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Bear Researcher node executing for {state['symbol']}")
        
        try:
            # Construct bearish thesis
            prompt = f"""You are a Bear Researcher. Construct a bearish thesis for {state['symbol']}.

Bull Thesis: {state.get('bull_thesis', 'N/A')}
Market Analysis: {state.get('market_analysis', {}).get('analysis', 'N/A')}
Fundamental Analysis: {state.get('fundamental_analysis', {}).get('analysis', 'N/A')}

Provide a compelling bearish thesis including:
1. Key bearish drivers
2. Risks and concerns
3. Downside potential
4. Counter-arguments to Bull thesis"""

            result = await graph_orchestrator.execute_node(
                node_name="bear_researcher",
                state=state,
                temperature=0.8,
            )
            
            state["bear_thesis"] = result.get("content")
            state["models_used"].append(result.get("model"))
            
            logger.info(f"Bear Researcher node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Bear Researcher node failed: {str(e)}")
            state["errors"].append(f"Bear Researcher error: {str(e)}")
        
        return state
    
    async def portfolio_manager_node(self, state: GraphState) -> GraphState:
        """
        Portfolio Manager node - Makes final decision based on debate
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Portfolio Manager node executing for {state['symbol']}")
        
        try:
            # Synthesize debate and make decision
            prompt = f"""You are the Portfolio Manager. Make a final investment decision for {state['symbol']}.

Bull Thesis: {state.get('bull_thesis', 'N/A')}
Bear Thesis: {state.get('bear_thesis', 'N/A')}
Market Analysis: {state.get('market_analysis', {}).get('analysis', 'N/A')}
Fundamental Analysis: {state.get('fundamental_analysis', {}).get('analysis', 'N/A')}
User Query: {state.get('user_query', 'N/A')}

Provide:
1. Final decision (BUY/SELL/HOLD)
2. Confidence level (0-100)
3. Investment thesis (synthesized from debate)
4. Key reasons for decision
5. Risk assessment
6. **Rating**: Buy/Overweight/Hold/Underweight/Sell

Format as JSON with keys: decision, confidence, thesis, reasons, risk_level, rating"""

            result = await graph_orchestrator.execute_node(
                node_name="portfolio_manager",
                state=state,
                temperature=0.5,
            )
            
            # Parse decision from result
            content = result.get("content", "")
            
            # Simple parsing (in production, use JSON parser)
            if "BUY" in content.upper():
                decision = "BUY"
            elif "SELL" in content.upper():
                decision = "SELL"
            else:
                decision = "HOLD"
            
            state["decision"] = decision
            state["thesis"] = content
            state["confidence"] = result.get("confidence", 0.75)
            state["models_used"].append(result.get("model"))
            
            # Process signal to extract portfolio rating
            from .signal_processing import signal_processor
            signal_result = signal_processor.process_signal(
                decision=decision,
                thesis=content,
                confidence=state["confidence"],
            )
            state["portfolio_rating"] = signal_result.get("rating")
            
            logger.info(f"Portfolio Manager node completed for {state['symbol']}: {decision} (Rating: {state['portfolio_rating']})")
            
        except Exception as e:
            logger.error(f"Portfolio Manager node failed: {str(e)}")
            state["errors"].append(f"Portfolio Manager error: {str(e)}")
            state["decision"] = "HOLD"  # Default to HOLD on error
            state["portfolio_rating"] = "Hold"
        
        return state
    
    async def risk_gate_node(self, state: GraphState) -> GraphState:
        """
        Risk Gate node - Final risk assessment and validation
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Risk Gate node executing for {state['symbol']}")
        
        try:
            # Assess risk level
            confidence = state.get("confidence", 0.75)
            decision = state.get("decision", "HOLD")
            
            # Risk assessment logic
            if confidence < 0.5:
                risk_level = "HIGH"
                risk_factors = ["Low confidence in analysis"]
            elif confidence < 0.7:
                risk_level = "MEDIUM"
                risk_factors = ["Moderate confidence in analysis"]
            else:
                risk_level = "LOW"
                risk_factors = []
            
            # Additional risk factors based on decision type
            if decision == "BUY":
                risk_factors.append("Long position risk")
            elif decision == "SELL":
                risk_factors.append("Short position risk")
            
            state["risk_level"] = risk_level
            state["risk_factors"] = risk_factors
            
            # For high-risk decisions, downgrade to HOLD
            if risk_level == "HIGH" and state["decision_type"] == DecisionType.AUTO_TRADE:
                logger.warning(f"High risk detected, downgrading decision to HOLD")
                state["decision"] = "HOLD"
                state["warnings"].append("Decision downgraded to HOLD due to high risk")
            
            logger.info(f"Risk Gate node completed for {state['symbol']}: {risk_level}")
            
        except Exception as e:
            logger.error(f"Risk Gate node failed: {str(e)}")
            state["errors"].append(f"Risk Gate error: {str(e)}")
            state["risk_level"] = "HIGH"
        
        return state
    
    async def aggressive_analyst_node(self, state: GraphState) -> GraphState:
        """
        Aggressive Analyst node - Takes aggressive risk perspective
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Aggressive Analyst node executing for {state['symbol']}")
        
        try:
            # Get past lessons for context
            from .reflection import reflector
            past_lessons = reflector.get_past_lessons(state.get("past_decisions", []), state["symbol"])
            
            prompt = f"""You are an Aggressive Risk Analyst. Evaluate the risk of {state['symbol']} from an aggressive perspective.

Decision: {state.get('decision', 'N/A')}
Thesis: {state.get('thesis', 'N/A')}
Confidence: {state.get('confidence', 0)}

{past_lessons}

Provide aggressive risk assessment including:
1. Why this decision might be too conservative
2. Potential upside if risks are taken
3. What additional risks could be managed
4. Recommendation (accept/modify/reject)"""

            result = await graph_orchestrator.execute_node(
                node_name="aggressive_analyst",
                state=state,
                temperature=0.8,
            )
            
            # Update history
            state["aggressive_history"] = state.get("aggressive_history", "") + f"\n\n{result.get('content')}"
            state["models_used"].append(result.get("model"))
            
            logger.info(f"Aggressive Analyst node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Aggressive Analyst node failed: {str(e)}")
            state["errors"].append(f"Aggressive Analyst error: {str(e)}")
        
        return state
    
    async def conservative_analyst_node(self, state: GraphState) -> GraphState:
        """
        Conservative Analyst node - Takes conservative risk perspective
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Conservative Analyst node executing for {state['symbol']}")
        
        try:
            # Get past lessons for context
            from .reflection import reflector
            past_lessons = reflector.get_past_lessons(state.get("past_decisions", []), state["symbol"])
            
            prompt = f"""You are a Conservative Risk Analyst. Evaluate the risk of {state['symbol']} from a conservative perspective.

Decision: {state.get('decision', 'N/A')}
Thesis: {state.get('thesis', 'N/A')}
Confidence: {state.get('confidence', 0)}

Aggressive view: {state.get('aggressive_history', 'N/A')}

{past_lessons}

Provide conservative risk assessment including:
1. Why this decision might be too risky
2. Potential downside scenarios
3. What additional safeguards are needed
4. Recommendation (accept/modify/reject)"""

            result = await graph_orchestrator.execute_node(
                node_name="conservative_analyst",
                state=state,
                temperature=0.7,
            )
            
            # Update history
            state["conservative_history"] = state.get("conservative_history", "") + f"\n\n{result.get('content')}"
            state["models_used"].append(result.get("model"))
            
            logger.info(f"Conservative Analyst node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Conservative Analyst node failed: {str(e)}")
            state["errors"].append(f"Conservative Analyst error: {str(e)}")
        
        return state
    
    async def neutral_analyst_node(self, state: GraphState) -> GraphState:
        """
        Neutral Analyst node - Takes balanced risk perspective
        
        Args:
            state: Current graph state
            
        Returns:
            Updated graph state
        """
        logger.info(f"Neutral Analyst node executing for {state['symbol']}")
        
        try:
            # Get past lessons for context
            from .reflection import reflector
            past_lessons = reflector.get_past_lessons(state.get("past_decisions", []), state["symbol"])
            
            prompt = f"""You are a Neutral Risk Analyst. Evaluate the risk of {state['symbol']} from a balanced perspective.

Decision: {state.get('decision', 'N/A')}
Thesis: {state.get('thesis', 'N/A')}
Confidence: {state.get('confidence', 0)}

Aggressive view: {state.get('aggressive_history', 'N/A')}
Conservative view: {state.get('conservative_history', 'N/A')}

{past_lessons}

Provide balanced risk assessment including:
1. Synthesis of aggressive and conservative views
2. Most likely risk scenarios
3. Recommended risk management approach
4. Final recommendation (accept/modify/reject)"""

            result = await graph_orchestrator.execute_node(
                node_name="neutral_analyst",
                state=state,
                temperature=0.6,
            )
            
            # Update history
            state["neutral_history"] = state.get("neutral_history", "") + f"\n\n{result.get('content')}"
            state["models_used"].append(result.get("model"))
            
            logger.info(f"Neutral Analyst node completed for {state['symbol']}")
            
        except Exception as e:
            logger.error(f"Neutral Analyst node failed: {str(e)}")
            state["errors"].append(f"Neutral Analyst error: {str(e)}")
        
        return state
    
    def create_node_output(
        self,
        node_name: str,
        status: str,
        result: Optional[Dict[str, Any]] = None,
        error: Optional[str] = None
    ) -> NodeOutput:
        """
        Create a node output object
        
        Args:
            node_name: Name of the node
            status: Status (success, error, pending)
            result: Result data
            error: Error message
            
        Returns:
            NodeOutput object
        """
        return {
            "node_name": node_name,
            "status": status,
            "result": result,
            "error": error,
            "timestamp": datetime.now().isoformat(),
        }


# Singleton instance
graph_nodes = GraphNodes()
