"""
AI Chat router — AI-powered analysis using Vibe-Trading agents customized for VN market.
"""

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional, Dict, Any
import json
import asyncio

from app.services.ai_service import ai_svc

router = APIRouter()


class ChatRequest(BaseModel):
    prompt: str
    context: Optional[Dict[str, Any]] = None


class BacktestRequest(BaseModel):
    symbol: str
    strategy: str
    startDate: str
    endDate: str
    params: Optional[Dict[str, Any]] = None


@router.post("/chat")
async def chat(request: ChatRequest):
    """AI chat with streaming response (SSE)."""

    async def event_generator():
        try:
            async for chunk in ai_svc.chat_stream(request.prompt, request.context):
                yield f"data: {json.dumps({'content': chunk})}\n\n"
            yield f"data: {json.dumps({'done': True})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")


@router.get("/consensus/{symbol}")
async def get_consensus(symbol: str):
    """Get AI consensus analysis for a stock."""
    try:
        return await ai_svc.get_consensus(symbol.upper())
    except Exception as e:
        return {"error": str(e)}


@router.post("/backtest")
async def submit_backtest(request: BacktestRequest):
    """Submit a backtest job."""
    try:
        result = await ai_svc.run_backtest(
            symbol=request.symbol.upper(),
            strategy=request.strategy,
            start_date=request.startDate,
            end_date=request.endDate,
            params=request.params or {},
        )
        return result
    except Exception as e:
        return {"error": str(e)}


@router.get("/backtest/{job_id}/status")
async def backtest_status(job_id: str):
    """Check backtest job status."""
    try:
        return await ai_svc.get_backtest_status(job_id)
    except Exception as e:
        return {"error": str(e)}
