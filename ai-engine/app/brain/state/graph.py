"""Trading agent orchestration graph built with LangGraph.

Defines the state machine that wires analysts, researchers, risk debaters,
and managers into a runnable ``StateGraph``.
"""

from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, StateGraph
from langgraph.checkpoint.memory import MemorySaver
from langchain_core.language_models.chat_models import BaseChatModel

from app.brain.agents.utils.agent_states import AgentState

from app.brain.agents.analysts.market_analyst import create_market_analyst
from app.brain.agents.analysts.sentiment_analyst import create_sentiment_analyst
from app.brain.agents.analysts.fundamentals_analyst import create_fundamentals_analyst
from app.brain.agents.analysts.news_analyst import create_news_analyst
from app.brain.agents.researchers.bull_researcher import create_bull_researcher
from app.brain.agents.researchers.bear_researcher import create_bear_researcher
from app.brain.state.signal_writer import create_signal_writer
from app.brain.agents.debaters.aggressive_debator import create_aggressive_debator
from app.brain.agents.debaters.conservative_debator import create_conservative_debator
from app.brain.agents.debaters.neutral_debator import create_neutral_debator
from app.brain.agents.managers.research_manager import create_research_manager
from app.brain.agents.managers.portfolio_manager import create_portfolio_manager
from app.brain.agents.trader.trader import create_trader


def build_graph(llm: BaseChatModel, task_type: str = "full") -> StateGraph:
    """Build and compile the trading agent orchestration graph.

    Args:
        llm: A LangChain chat model instance (ChatOpenAI, ChatAnthropic, etc.).
        task_type: ``"full"`` — end-to-end pipeline (default).

    Returns:
        A compiled ``StateGraph`` ready for ``graph.invoke(...)``.
    """
    builder = StateGraph(AgentState)

    # ---- nodes ----
    builder.add_node("market_analyst", create_market_analyst(llm))
    builder.add_node("sentiment_analyst", create_sentiment_analyst(llm))
    builder.add_node("fundamentals_analyst", create_fundamentals_analyst(llm))
    builder.add_node("news_analyst", create_news_analyst(llm))
    builder.add_node("bull_researcher", create_bull_researcher(llm))
    builder.add_node("bear_researcher", create_bear_researcher(llm))
    builder.add_node("research_manager", create_research_manager(llm))
    builder.add_node("trader", create_trader(llm))
    builder.add_node("aggressive_debator", create_aggressive_debator(llm))
    builder.add_node("conservative_debator", create_conservative_debator(llm))
    builder.add_node("neutral_debator", create_neutral_debator(llm))
    builder.add_node("portfolio_manager", create_portfolio_manager(llm))

    # ---- edges ----
    builder.set_entry_point("market_analyst")

    # Analysis phase
    builder.add_edge("market_analyst", "sentiment_analyst")
    builder.add_edge("sentiment_analyst", "fundamentals_analyst")
    builder.add_edge("fundamentals_analyst", "news_analyst")
    builder.add_edge("news_analyst", "bull_researcher")

    # Research phase (bull first, then bear)
    builder.add_edge("bull_researcher", "bear_researcher")
    builder.add_edge("bear_researcher", "research_manager")

    # Decision phase
    builder.add_edge("research_manager", "trader")
    builder.add_edge("trader", "aggressive_debator")

    # Risk phase — sequential debate, last writer wins
    builder.add_edge("aggressive_debator", "conservative_debator")
    builder.add_edge("conservative_debator", "neutral_debator")

    # Final
    builder.add_edge("neutral_debator", "portfolio_manager")
    # Persist portfolio manager decision into ai_signals for downstream paper trading
    builder.add_node("signal_writer", create_signal_writer())
    builder.add_edge("portfolio_manager", "signal_writer")
    builder.add_edge("signal_writer", END)

    memory = MemorySaver()
    return builder.compile(checkpointer=memory)
