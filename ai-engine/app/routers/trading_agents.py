"""
TradingAgents Router - Uses TradingAgents graph and agents for VN market
"""

import os
import uuid

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from langchain_openai import ChatOpenAI

from app.brain.state.graph import build_graph
from app.brain.agents import (
    create_market_analyst,
    create_sentiment_analyst,
    create_fundamentals_analyst,
    create_news_analyst,
    create_bull_researcher,
    create_bear_researcher,
    create_research_manager,
    create_trader,
    create_aggressive_debator,
    create_conservative_debator,
    create_neutral_debator,
    create_portfolio_manager,
)

router = APIRouter(tags=["TradingAgents"])


class TradingAgentsRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol (e.g., VCB)")
    task_type: str = Field(default="full", description="Task type (analysis, research, risk_assessment)")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Additional parameters")


class TradingAgentsResponse(BaseModel):
    task_id: str
    status: str
    symbol: str
    task_type: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


tasks: Dict[str, Dict[str, Any]] = {}


_AGENT_REGISTRY = {
    "market_analyst": ("analysts", "Market trend and technical analysis", create_market_analyst),
    "sentiment_analyst": ("analysts", "Social media and news sentiment", create_sentiment_analyst),
    "fundamentals_analyst": ("analysts", "Fundamental data and valuation", create_fundamentals_analyst),
    "news_analyst": ("analysts", "Global news and world affairs", create_news_analyst),
    "bull_researcher": ("researchers", "Bullish thesis development", create_bull_researcher),
    "bear_researcher": ("researchers", "Bearish thesis development", create_bear_researcher),
    "research_manager": ("managers", "Investment plan synthesis", create_research_manager),
    "trader": ("trader", "Transaction proposal", create_trader),
    "aggressive_debator": ("risk_mgmt", "High-risk perspective", create_aggressive_debator),
    "conservative_debator": ("risk_mgmt", "Low-risk perspective", create_conservative_debator),
    "neutral_debator": ("risk_mgmt", "Balanced perspective", create_neutral_debator),
    "portfolio_manager": ("managers", "Final portfolio decision", create_portfolio_manager),
}


@router.post("/execute", response_model=TradingAgentsResponse)
async def execute_trading_agents(request: TradingAgentsRequest, background_tasks: BackgroundTasks):
    """Execute TradingAgents graph for Vietnam market analysis."""
    try:
        task_id = str(uuid.uuid4())

        tasks[task_id] = {
            "id": task_id,
            "symbol": request.symbol,
            "task_type": request.task_type,
            "status": "running",
            "result": None,
        }

        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if not api_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured")

        llm = ChatOpenAI(api_key=api_key, model=model)
        graph = build_graph(llm, task_type=request.task_type)

        def execute_graph():
            try:
                config = {"configurable": {"thread_id": str(uuid.uuid4())}}
                result = graph.invoke({
                    "company_of_interest": request.symbol,
                    "trade_date": request.parameters.get("trade_date", "") if request.parameters else "",
                    "asset_type": request.parameters.get("asset_type", "stock") if request.parameters else "stock",
                }, config=config)
                tasks[task_id]["status"] = "completed"
                tasks[task_id]["result"] = result
            except Exception as e:
                tasks[task_id]["status"] = "failed"
                tasks[task_id]["error"] = str(e)

        background_tasks.add_task(execute_graph)

        return TradingAgentsResponse(
            task_id=task_id,
            status="running",
            symbol=request.symbol,
            task_type=request.task_type,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/task/{task_id}")
async def get_task(task_id: str):
    """Get TradingAgents task status."""
    if task_id not in tasks:
        raise HTTPException(status_code=404, detail="Task not found")
    return tasks[task_id]


@router.get("/agents")
async def list_agents():
    """List available TradingAgents for VN market."""
    agents = [
        {"name": name, "type": t[0], "description": t[1]}
        for name, t in _AGENT_REGISTRY.items()
    ]
    return {"agents": agents}


@router.get("/graph/nodes")
async def list_graph_nodes():
    """List available graph nodes for VN market."""
    nodes = [
        {"name": name, "description": t[1]}
        for name, t in _AGENT_REGISTRY.items()
    ]
    return {"nodes": nodes}
