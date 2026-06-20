"""
UI Services Router - UI services integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["UIServices"])


class UIServiceRequest(BaseModel):
    """UI service request."""
    
    service_name: str = Field(..., description="Service name")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Service parameters")


class UIServiceResponse(BaseModel):
    """UI service response."""
    
    service_name: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/run", response_model=UIServiceResponse)
async def run_ui_service(request: UIServiceRequest):
    """
    Run UI service using Vibe-Trading ui_services.
    
    Args:
        request: UI service request
        
    Returns:
        UI service result
    """
    try:
        from app.services.ui_services import build_run_analysis, load_run_context
        
        if request.service_name == "build_run_analysis":
            result = build_run_analysis(**(request.parameters or {}))
        elif request.service_name == "load_run_context":
            result = load_run_context(**(request.parameters or {}))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown service: {request.service_name}")
        
        return UIServiceResponse(
            service_name=request.service_name,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/list")
async def list_ui_services():
    """List available UI services."""
    services = [
        {
            "name": "build_run_analysis",
            "description": "Build run analysis for UI",
        },
        {
            "name": "load_run_context",
            "description": "Load run context for UI",
        },
    ]
    
    return {"services": services}
