"""Decision Router API endpoint."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from sag_api.core.deps import get_current_user
from sag_api.db.models import User
from sag_api.services.decision_router import route_query

router = APIRouter(prefix="/decision", tags=["decision"])


class DecisionRouteRequest(BaseModel):
    query: str = Field(..., description="Câu hỏi hoặc tin nhắn cần phân loại")
    context: dict[str, Any] | None = Field(default=None, description="Ngữ cảnh bổ sung nếu có")


class DecisionRouteResponse(BaseModel):
    query: str
    is_chitchat: bool = Field(..., description="Có phải câu chào hỏi / tán gẫu không")
    need_retrieval: bool = Field(..., description="Có cần tra cứu cơ sở tri thức SAG không")
    suggested_strategy: str = Field(..., description="Chiến lược tìm kiếm đề xuất: vector hoặc multi")
    domain: str = Field(..., description="Chủ đề: technical, business_hr, hoặc general")
    memory_scope: str = Field(
        default="project_documents",
        description="Phạm vi bằng chứng: none, session_history, project_documents, hybrid_both, full_session_audit",
    )
    temporal_mode: str = Field(
        default="atemporal",
        description="Chế độ thời gian: current_effective, change_lineage, past_point, atemporal",
    )
    confidence: float = Field(..., description="Độ tin cậy của mô hình")
    latency_ms: float = Field(..., description="Thời gian suy luận (mili-giây)")
    model: str = Field(..., description="Decision model used for classification")
    fallback_used: bool = False
    fallback_reason: str | None = None


@router.post("/route", response_model=DecisionRouteResponse, summary="Định tuyến ý định câu hỏi qua Decision Router")
async def route_user_query(
    body: DecisionRouteRequest,
    _user: User = Depends(get_current_user),
) -> DecisionRouteResponse:
    """Classify without blocking the API event loop during the cloud request."""
    result = await asyncio.to_thread(route_query, body.query, body.context)
    return DecisionRouteResponse(**result)
