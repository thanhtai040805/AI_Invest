"""
LLM Clients Router - AI Agents integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List

router = APIRouter(tags=["LLMClients"])


class LLMClientRequest(BaseModel):
    """LLM client request."""
    
    client_name: str = Field(..., description="Client name")
    prompt: str = Field(..., description="Prompt to send")
    parameters: Optional[Dict[str, Any]] = Field(None, description="Client parameters")


class LLMClientResponse(BaseModel):
    """LLM client response."""
    
    client_name: str
    status: str
    response: Optional[str] = None
    error: Optional[str] = None


@router.get("/list")
async def list_llm_clients():
    """List available LLM clients from AI agents."""
    from app.brain.providers import GeminiAgent, GroqAgent, OpenRouterAgent, OpenAIAgent
    
    clients = [
        {
            "name": "gemini",
            "description": "Google Gemini - Document reader/analyst for long content",
            "role": "deep_analysis",
        },
        {
            "name": "openai",
            "description": "OpenAI - Reasoning/judge for stable synthesis",
            "role": "reasoning",
        },
        {
            "name": "groq",
            "description": "Groq - Fast realtime tasks and signal scoring",
            "role": "realtime",
        },
        {
            "name": "openrouter",
            "description": "OpenRouter - Routing/fallback gateway",
            "role": "routing",
        },
    ]
    
    return {"clients": clients}


@router.post("/chat", response_model=LLMClientResponse)
async def chat_with_client(request: LLMClientRequest):
    """
    Chat with an LLM client using AI agents.
    
    Args:
        request: LLM client request
        
    Returns:
        LLM client response
    """
    try:
        from app.brain.providers import GeminiAgent, GroqAgent, OpenRouterAgent, OpenAIAgent
        import os
        
        # Map client name to agent class
        agent_classes = {
            "gemini": (GeminiAgent, "GEMINI_API_KEY", "gemini-1.5-flash"),
            "openai": (OpenAIAgent, "OPENAI_API_KEY", "gpt-4o-mini"),
            "groq": (GroqAgent, "GROQ_API_KEY", "llama3-70b-8192"),
            "openrouter": (OpenRouterAgent, "OPENROUTER_API_KEY", "deepseek/deepseek-v4-flash:free"),
        }
        
        if request.client_name not in agent_classes:
            raise HTTPException(status_code=404, detail=f"Client {request.client_name} not found")
        
        agent_class, api_key_env, default_model = agent_classes[request.client_name]
        api_key = os.getenv(api_key_env)
        
        if not api_key:
            raise HTTPException(status_code=400, detail=f"API key for {request.client_name} not configured")
        
        # Initialize agent
        agent = agent_class(
            api_key=api_key,
            model=request.parameters.get("model", default_model) if request.parameters else default_model,
        )
        
        # Send prompt
        response = await agent.analyze(request.prompt)
        
        return LLMClientResponse(
            client_name=request.client_name,
            status="success",
            response=response.get("content"),
        )
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
