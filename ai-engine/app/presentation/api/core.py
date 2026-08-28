"""
Core Router - Core functionality integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["Core"])


class CoreRequest(BaseModel):
    """Core functionality request."""
    
    action: str = Field(..., description="Action to perform")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Action parameters")


class CoreResponse(BaseModel):
    """Core response."""
    
    action: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/execute", response_model=CoreResponse)
async def execute_core_action(request: CoreRequest):
    """
    Execute a core action using Vibe-Trading core functionality.
    
    Args:
        request: Core action request
        
    Returns:
        Core action result
    """
    try:
        from app.core.registry import AgentRegistry
        from app.infrastructure.monitoring.job_state_service import get_all_jobs
        
        if request.action == "run_backtest":
            result = {
                "status": "success",
                "message": "Backtest runner active",
            }
        elif request.action == "get_state":
            result = {
                "status": "success",
                "state": {
                    "agents": AgentRegistry.list_agents(),
                    "jobs": get_all_jobs(),
                },
            }
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")
        
        return CoreResponse(
            action=request.action,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_core_status():
    """Get core system status."""
    try:
        from app.core.registry import AgentRegistry
        
        return {
            "status": "ok",
            "state_store": "active",
            "agents_registered": len(AgentRegistry.list_agents()),
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
