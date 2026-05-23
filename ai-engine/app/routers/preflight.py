"""
Preflight Router - Preflight checks integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["Preflight"])


class PreflightRequest(BaseModel):
    """Preflight check request."""
    
    check_type: str = Field(..., description="Type of preflight check")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Check parameters")


class PreflightResponse(BaseModel):
    """Preflight response."""
    
    check_type: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/check", response_model=PreflightResponse)
async def run_preflight_check(request: PreflightRequest):
    """
    Run preflight check using Vibe-Trading preflight.
    
    Args:
        request: Preflight check request
        
    Returns:
        Preflight check result
    """
    try:
        from app.brain.preflight import run_preflight
        
        # Run preflight check
        result = run_preflight(
            check_type=request.check_type,
            **(request.parameters or {}),
        )
        
        return PreflightResponse(
            check_type=request.check_type,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_preflight_status():
    """Get preflight system status."""
    return {
        "status": "ok",
        "preflight_enabled": True,
    }
