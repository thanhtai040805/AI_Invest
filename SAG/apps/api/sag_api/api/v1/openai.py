"""Endpoint hội thoại tương thích OpenAI — gọi bất kỳ Agent nào như một "mô hình có trích dẫn".

    POST /api/v1/openai/{agent_id}/chat/completions
    Authorization: Bearer <sag JWT>

Hỗ trợ cả stream / không stream; request body theo cấu trúc OpenAI Chat Completions.
Truy vấn, system prompt, ngắt chống ảo giác và hội thoại nội bộ hoàn toàn nhất quán, để hệ thống ngoài kết nối liền mạch.
"""

from __future__ import annotations

import json
import time

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from sag_agent import AgentRuntime, EventType
from sag_api.core.db import SessionLocal, get_session
from sag_api.core.deps import (
    get_agent_runtime,
    get_current_user,
    get_engine_manager,
    get_llm,
    get_tool_registry,
)
from sag_api.core.errors import ConfigurationError, UpstreamError, ValidationError
from sag_api.db.models import User
from sag_api.generation import LLMClient
from sag_api.sag import EngineManager
from sag_api.services import agent_domain as svc
from sag_api.services import agent_service
from sag_api.tools import ToolRegistry

router = APIRouter(prefix="/openai", tags=["openai"])


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    messages: list[ChatMessage] = Field(min_length=1)
    model: str | None = None
    stream: bool = False
    # Trường tương thích, nhận nhưng không bắt buộc truyền (tham số truy vấn theo persona trợ lý)
    temperature: float | None = None
    max_tokens: int | None = None


def _split_query(messages: list[ChatMessage]) -> tuple[str, list[dict[str, str]]]:
    """Lấy tin nhắn user cuối làm câu hỏi vòng này, user/assistant còn lại làm lịch sử."""
    last_user = next((i for i in range(len(messages) - 1, -1, -1) if messages[i].role == "user"), None)
    if last_user is None:
        raise ValidationError("messages thiếu tin nhắn user")
    query = messages[last_user].content
    history = [
        {"role": m.role, "content": m.content}
        for idx, m in enumerate(messages)
        if idx != last_user and m.role in ("user", "assistant")
    ]
    return query, history


@router.post("/{agent_id}/chat/completions")
async def chat_completions(
    agent_id: str,
    body: ChatCompletionRequest,
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
    llm: LLMClient = Depends(get_llm),
    tool_registry: ToolRegistry = Depends(get_tool_registry),
    agent_runtime: AgentRuntime = Depends(get_agent_runtime),
):
    agent = await svc.get_agent(session, agent_id)
    query, history = _split_query(body.messages)

    plan = svc.build_ask_context(agent=agent, query=query, history=history)
    if not llm.configured:
        raise ConfigurationError("Chưa cấu hình LLM, không thể tạo câu trả lời")

    created = int(time.time())
    model = body.model or f"sag:{agent.name}"
    cid = f"chatcmpl-{agent_id[:12]}-{created}"

    def _events():
        # Không trạng thái: thread_id=None → không lưu vào DB; dùng lại cùng vòng lặp Agent
        return agent_service.generate_stream(
            SessionLocal,
            plan=plan,
            agent=agent,
            thread_id=None,
            engine_manager=engine_manager,
            llm=llm,
            tool_registry=tool_registry,
            runtime=agent_runtime,
        )

    if body.stream:

        async def gen():
            streamed_parts: list[str] = []

            def chunk(delta: dict, finish: str | None = None) -> str:
                payload = {
                    "id": cid,
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": model,
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

            yield chunk({"role": "assistant"})
            async for event in _events():
                payload = event.data["payload"]
                if event.type == EventType.MESSAGE_DELTA.value:
                    delta = str(payload["delta"])
                    streamed_parts.append(delta)
                    yield chunk({"content": delta})
                elif event.type == EventType.RUN_COMPLETED.value:
                    canonical = str(payload.get("output") or "")
                    streamed = "".join(streamed_parts)
                    # Canonicalization can append traceable-source fallbacks.
                    # Stream the suffix when it preserves the content already
                    # delivered; destructive rewrites remain a non-stream-only
                    # guarantee because SSE cannot retract prior chunks.
                    if canonical.startswith(streamed):
                        suffix = canonical[len(streamed) :]
                        if suffix:
                            yield chunk({"content": suffix})
                elif event.type in (EventType.RUN_FAILED.value, EventType.RUN_CANCELLED.value):
                    error = payload.get("error") or {}
                    yield chunk({"content": f"\n[Lỗi] {error.get('message', 'Sinh thất bại')}"})
            yield chunk({}, finish="stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
        )

    # Không stream: tiêu thụ cùng luồng sự kiện, gộp thành câu trả lời cuối
    parts: list[str] = []
    final_output = ""
    citations = plan.citations
    usage: dict = {}
    async for event in _events():
        payload = event.data["payload"]
        if event.type == EventType.MESSAGE_DELTA.value:
            parts.append(payload["delta"])
        elif event.type == EventType.RUN_COMPLETED.value:
            final_output = str(payload.get("output") or "")
            citations = payload.get("citations") or citations
            usage = payload.get("usage") or {}
        elif event.type in (EventType.RUN_FAILED.value, EventType.RUN_CANCELLED.value):
            error = payload.get("error") or {}
            raise UpstreamError(error.get("message", "Sinh thất bại"))
    return {
        "id": cid,
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": final_output or "".join(parts)},
                "finish_reason": "stop",
            }
        ],
        "usage": {
            "prompt_tokens": usage.get("input_tokens", 0),
            "completion_tokens": usage.get("output_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        # Tiện ích mở rộng sag: truy vết trích dẫn (client chuẩn bỏ qua trường không biết)
        "sag": {"citations": citations, "sources": len(citations)},
    }
