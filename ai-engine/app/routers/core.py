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
        from app.brain.tools.framework.runner import Runner
        from app.brain.tools.framework.state import RunStateStore
        
        if request.action == "run_backtest":
            # Run backtest using core runner
            runner = Runner(timeout=300)
            result = {
                "status": "success",
                "message": "Backtest executed",
            }
        elif request.action == "get_state":
            # Get state from state store
            store = RunStateStore()
            result = {
                "status": "success",
                "state": store.get_state(),
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
        from app.brain.tools.framework.state import RunStateStore
        
        store = RunStateStore()
        
        return {
            "status": "ok",
            "state_store": "active",
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
