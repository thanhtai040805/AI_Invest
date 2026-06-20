"""
Providers Router - LLM provider management
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["Providers"])


class ProviderConfig(BaseModel):
    """LLM provider configuration."""

    provider: str = Field(..., description="Provider name (groq0, groq1, nvidia)")
    model: str = Field(..., description="Model name")
    api_key: Optional[str] = Field(None, description="API key")
    temperature: Optional[float] = Field(0.0, description="Temperature")


class ProviderResponse(BaseModel):
    """Provider response."""

    provider: str
    status: str
    configured: bool
    model: Optional[str] = None


@router.get("/list")
async def list_providers():
    """List available LLM providers."""
    providers = [
        {
            "name": "groq0",
            "description": "Groq llama-3.3-70b-versatile - Reasoning sâu, tổng hợp tín hiệu, chốt luận điểm",
            "role": "reasoning",
            "models": ["llama-3.3-70b-versatile", "qwen/qwen3-32b"],
        },
        {
            "name": "groq1",
            "description": "Groq qwen/qwen3-32b - Structured output, JSON, classification, cross-check",
            "role": "structured_output",
            "models": ["qwen/qwen3-32b"],
        },
        {
            "name": "nvidia",
            "description": "NVIDIA minimaxai/minimax-m2.7 - Document reader/analyst cho news và báo cáo",
            "role": "document_analysis",
            "models": ["minimaxai/minimax-m2.7"],
        },
    ]

    return {"providers": providers}


@router.post("/configure", response_model=ProviderResponse)
async def configure_provider(config: ProviderConfig):
    """
    Configure an LLM provider.

    Args:
        config: Provider configuration

    Returns:
        Configuration result
    """
    from app.config.settings import get_settings
    import os

    try:
        if config.api_key:
            if config.provider == "groq0":
                os.environ["GROQ_API_KEY0"] = config.api_key
            elif config.provider == "groq1":
                os.environ["GROQ_API_KEY1"] = config.api_key
            elif config.provider == "nvidia":
                os.environ["NVDIA"] = config.api_key

        return ProviderResponse(
            provider=config.provider,
            status="configured",
            configured=True,
            model=config.model,
        )

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{provider}/status")
async def get_provider_status(provider: str):
    """Get provider configuration status."""
    import os

    is_configured = False
    if provider == "groq0":
        is_configured = bool(os.getenv("GROQ_API_KEY0"))
    elif provider == "groq1":
        is_configured = bool(os.getenv("GROQ_API_KEY1"))
    elif provider == "nvidia":
        is_configured = bool(os.getenv("NVDIA"))

    return {
        "provider": provider,
        "configured": is_configured,
    }
