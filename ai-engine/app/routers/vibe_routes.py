"""
Vibe API Router - Vibe-Trading API routes integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["VibeAPI"])


class VibeAPIRequest(BaseModel):
    """Vibe API request."""
    
    endpoint: str = Field(..., description="API endpoint")
    parameters: Optional[Dict[str, Any]] = Field(None, description="API parameters")


class VibeAPIResponse(BaseModel):
    """Vibe API response."""
    
    endpoint: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/call", response_model=VibeAPIResponse)
async def call_vibe_api(request: VibeAPIRequest):
    """
    Call Vibe-Trading API endpoint.
    
    Args:
        request: Vibe API request
        
    Returns:
        Vibe API response
    """
    try:
        from app.routers.vibe_api import call_endpoint
        
        # Call API endpoint
        result = call_endpoint(
            endpoint=request.endpoint,
            **(request.parameters or {}),
        )
        
        return VibeAPIResponse(
            endpoint=request.endpoint,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/endpoints")
async def list_vibe_api_endpoints():
    """List available Vibe API endpoints."""
    endpoints = [
        {
            "name": "alpha_routes",
            "description": "Alpha zoo API routes",
        },
        {
            "name": "run_routes",
            "description": "Backtest run routes",
        },
        {
            "name": "session_routes",
            "description": "Session management routes",
        },
    ]
    
    return {"endpoints": endpoints}
