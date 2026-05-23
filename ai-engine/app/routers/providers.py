"""
Providers Router - LLM provider management
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

router = APIRouter(tags=["Providers"])


class ProviderConfig(BaseModel):
    """LLM provider configuration."""
    
    provider: str = Field(..., description="Provider name (gemini, groq, openrouter)")
    model: str = Field(..., description="Model name")
    api_key: Optional[str] = Field(None, description="API key")
    base_url: Optional[str] = Field(None, description="Base URL")
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
            "name": "gemini",
            "description": "Google Gemini - Document reader/analyst for long content",
            "role": "deep_analysis",
            "models": ["gemini-1.5-flash", "gemini-1.5-pro"],
        },
        {
            "name": "openai",
            "description": "OpenAI - Reasoning/judge for stable synthesis",
            "role": "reasoning",
            "models": ["gpt-4o-mini", "gpt-4o"],
        },
        {
            "name": "groq",
            "description": "Groq - Fast realtime tasks and signal scoring",
            "role": "realtime",
            "models": ["llama3-70b-8192", "llama3-8b-8192", "mixtral-8x7b-32768"],
        },
        {
            "name": "openrouter",
            "description": "OpenRouter - Routing/fallback gateway",
            "role": "routing",
            "models": ["deepseek/deepseek-v4-flash:free", "anthropic/claude-3.5-sonnet"],
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
    try:
        from app.brain.providers import GeminiAgent, GroqAgent, OpenRouterAgent, OpenAIAgent
        import os
        
        # Set environment variable for API key if provided
        if config.api_key:
            if config.provider == "gemini":
                os.environ["GEMINI_API_KEY"] = config.api_key
            elif config.provider == "groq":
                os.environ["GROQ_API_KEY"] = config.api_key
            elif config.provider == "openrouter":
                os.environ["OPENROUTER_API_KEY"] = config.api_key
            elif config.provider == "openai":
                os.environ["OPENAI_API_KEY"] = config.api_key
        
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
    try:
        import os
        
        # Check if provider is configured via environment variables
        is_configured = False
        if provider == "gemini":
            is_configured = bool(os.getenv("GEMINI_API_KEY"))
        elif provider == "groq":
            is_configured = bool(os.getenv("GROQ_API_KEY"))
        elif provider == "openrouter":
            is_configured = bool(os.getenv("OPENROUTER_API_KEY"))
        elif provider == "openai":
            is_configured = bool(os.getenv("OPENAI_API_KEY"))
        
        return {
            "provider": provider,
            "configured": is_configured,
        }
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
