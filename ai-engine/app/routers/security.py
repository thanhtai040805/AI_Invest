"""
Security Router - Security features integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["Security"])


class SecurityRequest(BaseModel):
    """Security request."""
    
    action: str = Field(..., description="Security action")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Action parameters")


class SecurityResponse(BaseModel):
    """Security response."""
    
    action: str
    status: str
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None


@router.post("/validate")
async def validate_security(request: SecurityRequest):
    """
    Validate security using Vibe-Trading security features.
    
    Args:
        request: Security validation request
        
    Returns:
        Security validation result
    """
    try:
        from app.brain.security import validate_input
        
        if request.action == "validate_input":
            result = validate_input(**(request.parameters or {}))
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {request.action}")
        
        return SecurityResponse(
            action=request.action,
            status="success",
            result=result,
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/status")
async def get_security_status():
    """Get security system status."""
    return {
        "status": "ok",
        "security_enabled": True,
    }
