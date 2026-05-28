"""
LLM Clients Router - AI Agents integration
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any

router = APIRouter(tags=["LLMClients"])


class LLMClientRequest(BaseModel):
    """LLM client request."""

    client_name: str = Field(..., description="Client name (groq0, groq1, nvidia)")
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
    from app.brain.providers.groq_client import GroqAgent

    clients = [
        {
            "name": "groq0",
            "description": "Groq llama-3.3-70b-versatile - Reasoning sâu, tổng hợp, chốt luận điểm",
            "role": "reasoning",
            "model": "llama-3.3-70b-versatile",
        },
        {
            "name": "groq1",
            "description": "Groq qwen/qwen3-32b - Structured output, JSON, classification, cross-check",
            "role": "structured_output",
            "model": "qwen/qwen3-32b",
        },
        {
            "name": "nvidia",
            "description": "NVIDIA minimaxai/minimax-m2.7 - Document reader/analyst cho news và báo cáo",
            "role": "document_analysis",
            "model": "minimaxai/minimax-m2.7",
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
        from app.brain.providers.groq_client import GroqAgent
        from app.config.settings import get_settings
        from openai import OpenAI
        s = get_settings()

        if request.client_name == "groq0":
            agent = GroqAgent(
                api_key=s.llm_groq_key0,
                model=s.llm_groq_model0,
            )
        elif request.client_name == "groq1":
            agent = GroqAgent(
                api_key=s.llm_groq_key1,
                model=s.llm_groq_model1,
            )
        elif request.client_name == "nvidia":
            client = OpenAI(base_url="https://integrate.api.nvidia.com/v1", api_key=s.llm_nvidia_key)
            response = client.chat.completions.create(
                model=s.llm_nvidia_model,
                messages=[{"role": "user", "content": request.prompt}],
                max_tokens=2048,
            )
            return LLMClientResponse(
                client_name=request.client_name,
                status="success",
                response=response.choices[0].message.content,
            )
        else:
            raise HTTPException(status_code=404, detail=f"Client {request.client_name} not found")

        response = await agent.analyze(request.prompt)

        return LLMClientResponse(
            client_name=request.client_name,
            status="success",
            response=response.get("content"),
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
