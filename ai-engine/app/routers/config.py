"""
Config Router - Configuration management
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["Config"])


class ConfigUpdate(BaseModel):
    """Configuration update request."""
    
    key: str = Field(..., description="Configuration key")
    value: Any = Field(..., description="Configuration value")


class ConfigResponse(BaseModel):
    """Configuration response."""
    
    key: str
    value: Any
    status: str


@router.get("/")
async def get_config():
    """Get current configuration."""
    try:
        from app.config.settings import get_settings
        
        settings = get_settings()
        
        return {
            "dnse_enabled": settings.dnse_enabled,
            "dnse_configured": settings.dnse_configured,
            "nvidia_configured": bool(settings.llm_nvidia_key),
            "groq0_configured": bool(settings.llm_groq_key0),
            "groq1_configured": bool(settings.llm_groq_key1),
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/update", response_model=ConfigResponse)
async def update_config(request: ConfigUpdate):
    """
    Update configuration.
    
    Args:
        request: Configuration update request
        
    Returns:
        Configuration update result
    """
    try:
        # For now, just return success
        # In production, this would update the configuration file
        return ConfigResponse(
            key=request.key,
            value=request.value,
            status="updated",
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/schema")
async def get_config_schema():
    """Get configuration schema."""
    schema = {
        "dnse": {
            "enabled": "boolean",
            "api_key": "string",
        },
        "llm": {
            "provider": "string",
            "model": "string",
            "api_key": "string",
        },
    }
    
    return {"schema": schema}
