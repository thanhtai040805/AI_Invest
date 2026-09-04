"""API Router cho Dịch vụ Sinh / Đánh giá RAG (Generation API) phục vụ ai-engine."""

from __future__ import annotations

import json
import logging
import os
import re
from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.db.models import Document, Source
from sag_api.generation import LLMClient

logger = logging.getLogger("sag_api.generation")

router = APIRouter(prefix="/generation", tags=["generation"])


def get_generation_llm(request: Request) -> LLMClient:
    llm = getattr(request.app.state, "llm", None)
    if llm is None:
        return LLMClient(settings)
    return llm


class GenerationRequest(BaseModel):
    query: str
    filter: Optional[dict[str, Any]] = None
    stream: bool = False


@router.post("", response_model=dict[str, Any])
async def generate(
    body: GenerationRequest,
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_generation_llm),
) -> dict[str, Any]:
    """Sinh câu trả lời / đánh giá từ LLM với toàn văn tài liệu BCTC của mã cổ phiếu."""
    ticker = ""
    if body.filter and isinstance(body.filter, dict):
        ticker = str(body.filter.get("ticker") or "").upper().strip()

    context_text = ""
    if ticker:
        source_name = f"BCTC_{ticker}"
        # Tìm Source trong DB
        stmt = select(Source).where(Source.name == source_name)
        source = (await session.execute(stmt)).scalar_one_or_none()
        if source:
            # Lấy danh sách Document đang active
            doc_stmt = (
                select(Document)
                .where(Document.source_id == source.id, Document.is_active == True)
                .order_by(Document.created_at.desc())
            )
            docs = (await session.execute(doc_stmt)).scalars().all()
            doc_texts = []
            for d in docs:
                if d.storage_path and os.path.isfile(d.storage_path):
                    try:
                        with open(d.storage_path, "r", encoding="utf-8", errors="replace") as f:
                            content = f.read()
                            doc_texts.append(f"### TÀI LIỆU: {d.filename} (Vai trò: {d.doc_role or 'BCTC'})\n\n{content}")
                    except Exception as err:
                        logger.warning(f"Không thể đọc file {d.storage_path}: {err}")

            if doc_texts:
                context_text = "\n\n" + ("=" * 50) + "\n\n".join(doc_texts)

    if context_text:
        full_content = (
            f"Dưới đây là TOÀN BỘ nội dung các Báo cáo Tài chính của mã {ticker}:\n\n"
            f"{context_text}\n\n"
            f"==================================================\n\n"
            f"YÊU CẦU PHÂN TÍCH:\n{body.query}"
        )
    else:
        full_content = body.query

    messages = [
        {
            "role": "system",
            "content": "Bạn là Chuyên gia Cao cấp về Phân tích Báo cáo Tài chính và Lợi thế Cạnh tranh Doanh nghiệp tại Thị trường Chứng khoán Việt Nam.",
        },
        {"role": "user", "content": full_content},
    ]

    try:
        response_text = await llm.complete(messages)
    except Exception as e:
        logger.error(f"Lỗi khi gọi LLM sinh đánh giá: {e}")
        return {
            "status": "FALLBACK",
            "ticker": ticker,
            "text": str(e),
            "response": {
                "ticker": ticker,
                "moat_score": 0.0,
                "intangibles_score": 0.0,
                "switching_costs_score": 0.0,
                "network_effect_score": 0.0,
                "cost_advantage_score": 0.0,
                "efficient_scale_score": 0.0,
                "evidence_quote": f"Lỗi gọi LLM Backend: {e}",
                "multiplier": 0.75,
            },
        }

    # Bóc tách cấu trúc JSON từ câu trả lời của LLM
    parsed_json = {}
    match = re.search(r"\{.*\}", response_text, re.DOTALL)
    if match:
        try:
            parsed_json = json.loads(match.group(0))
        except Exception:
            parsed_json = {}

    if not parsed_json:
        parsed_json = {
            "ticker": ticker,
            "moat_score": 60.0,
            "intangibles_score": 50.0,
            "switching_costs_score": 50.0,
            "network_effect_score": 50.0,
            "cost_advantage_score": 50.0,
            "efficient_scale_score": 50.0,
            "evidence_quote": response_text[:300] if response_text else "",
            "multiplier": 1.0,
        }

    parsed_json["ticker"] = ticker
    return {
        "text": response_text,
        "response": parsed_json,
        "status": "SUCCESS",
    }
