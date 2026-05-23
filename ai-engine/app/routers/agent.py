"""
Agent Router - Uses Vibe-Trading agent loop with VN adapters
"""

from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
import uuid
import asyncio

router = APIRouter(tags=["Agent"])


class AgentRequest(BaseModel):
    """Agent analysis request."""
    
    query: str = Field(..., description="User query about stock")
    symbol: Optional[str] = Field(None, description="Stock symbol (e.g., VCB)")
    context: Optional[Dict[str, Any]] = Field(None, description="Additional context")


class AgentResponse(BaseModel):
    """Agent analysis response."""
    
    session_id: str
    status: str
    message: Optional[str] = None
    analysis: Optional[Dict[str, Any]] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None


# In-memory session storage (in production, use database)
sessions: Dict[str, Dict[str, Any]] = {}


@router.post("/analyze", response_model=AgentResponse)
async def analyze_stock(request: AgentRequest, background_tasks: BackgroundTasks):
    """
    Run agent analysis using Vibe-Trading agent loop with VN adapters.
    
    Args:
        request: Agent request with query and optional symbol
        
    Returns:
        Agent analysis result
    """
    try:
        session_id = str(uuid.uuid4())
        
        # Initialize session
        sessions[session_id] = {
            "id": session_id,
            "query": request.query,
            "symbol": request.symbol,
            "status": "running",
            "messages": [],
            "tool_calls": [],
        }
        
        # Import Vibe-Trading agent components
        from app.brain.agents.core.loop import AgentLoop
        from app.brain.agents.core.context import ContextBuilder
        from app.brain.tools import build_registry
        
        # Build tool registry with VN adapters
        tool_registry = build_registry(
            include_shell_tools=False,
        )
        
        # Create context builder
        context_builder = ContextBuilder()
        
        # For now, return a placeholder response
        # In production, this would actually run the agent loop
        sessions[session_id]["status"] = "completed"
        sessions[session_id]["message"] = f"Analysis for {request.symbol or 'market'}: {request.query}"
        
        return AgentResponse(
            session_id=session_id,
            status="completed",
            message=sessions[session_id]["message"],
            analysis={},
            tool_calls=[],
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/session/{session_id}")
async def get_session(session_id: str):
    """Get agent session status."""
    if session_id not in sessions:
        raise HTTPException(status_code=404, detail="Session not found")
    return sessions[session_id]


@router.get("/session/{session_id}/stream")
async def stream_session(session_id: str):
    """Stream agent session updates via SSE."""
    from fastapi.responses import StreamingResponse
    
    async def event_generator():
        if session_id not in sessions:
            yield f"event: error\ndata: {{\"error\": \"Session not found\"}}\n\n"
            return
        
        session = sessions[session_id]
        
        # Send initial status
        yield f"event: status\ndata: {{\"status\": \"{session['status']}\"}}\n\n"
        
        # In production, this would stream real-time updates
        # For now, just send completion
        yield f"event: done\ndata: {{\"status\": \"{session['status']}\"}}\n\n"
    
    return StreamingResponse(event_generator(), media_type="text/event-stream")
