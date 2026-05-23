"""Graph Router — build and execute trading agent orchestration graphs."""

import os
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from langchain_openai import ChatOpenAI

from app.brain.state.graph import build_graph

router = APIRouter(tags=["Graph"])


class GraphExecuteRequest(BaseModel):
    symbol: str = Field(..., description="Stock symbol (e.g., VCB)")
    task_type: str = Field(default="full", description="Graph pipeline (full)")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Additional parameters")


class GraphExecuteResponse(BaseModel):
    graph_name: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.get("/list")
async def list_graphs():
    """List available graph nodes and pipeline steps."""
    nodes = [
        {"name": "market_analyst", "description": "Market trend and technical analysis"},
        {"name": "sentiment_analyst", "description": "Social media and news sentiment"},
        {"name": "fundamentals_analyst", "description": "Fundamental data and valuation"},
        {"name": "news_analyst", "description": "Global news and world affairs"},
        {"name": "bull_researcher", "description": "Bullish thesis development"},
        {"name": "bear_researcher", "description": "Bearish thesis development"},
        {"name": "research_manager", "description": "Investment plan synthesis"},
        {"name": "trader", "description": "Transaction proposal"},
        {"name": "aggressive_debator", "description": "High-risk perspective"},
        {"name": "conservative_debator", "description": "Low-risk perspective"},
        {"name": "neutral_debator", "description": "Balanced perspective"},
        {"name": "portfolio_manager", "description": "Final portfolio decision"},
    ]
    return {"graphs": [{"name": "full", "description": "End-to-end trading pipeline", "nodes": nodes}]}


@router.post("/execute", response_model=GraphExecuteResponse)
async def execute_graph(request: GraphExecuteRequest):
    """Build and execute the full trading agent graph."""
    try:
        api_key = os.getenv("OPENAI_API_KEY")
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        if not api_key:
            raise HTTPException(status_code=400, detail="OPENAI_API_KEY not configured")

        llm = ChatOpenAI(api_key=api_key, model=model)
        graph = build_graph(llm, task_type=request.task_type)

        config = {"configurable": {"thread_id": str(uuid.uuid4())}}
        result = graph.invoke({
            "company_of_interest": request.symbol,
            "trade_date": request.parameters.get("trade_date", "") if request.parameters else "",
            "asset_type": request.parameters.get("asset_type", "stock") if request.parameters else "stock",
        }, config=config)

        return GraphExecuteResponse(
            graph_name=request.task_type,
            status="success",
            result=result,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
