from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user, get_engine_manager
from sag_api.db.models import User
from sag_api.sag import EngineManager
from sag_api.services.gil_service import GILAnalysisResult, GILGraphAnalyzer
from sag_api.services.insight_service import get_source_graph
from sag_api.services.source_service import get_source

router = APIRouter(prefix="/gil", tags=["gil"])


class DirectGraphEvaluateRequest(BaseModel):
    ticker: str
    equity_vnd: float = 0.0
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []


@router.post("/evaluate", response_model=dict[str, Any])
async def evaluate_graph_direct(body: DirectGraphEvaluateRequest) -> dict[str, Any]:
    """Phân tích trực tiếp danh sách Nodes và Edges bằng thuật toán đồ thị thuần túy (Zero LLM Token)."""
    analyzer = GILGraphAnalyzer(ticker=body.ticker, equity_vnd=body.equity_vnd)
    analyzer.build_graph(body.nodes, body.edges)
    result = analyzer.evaluate()
    return result.to_dict()


@router.get("/sources/{source_id}", response_model=dict[str, Any])
async def evaluate_source_gil(
    source_id: str,
    equity_vnd: float = Query(default=0.0, ge=0.0),
    _user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
    engine_manager: EngineManager = Depends(get_engine_manager),
) -> dict[str, Any]:
    """Phân tích đồ thị của Source đã nạp trong SAG để đánh giá rủi ro GIL."""
    source = await get_source(session, source_id)
    source_graph = await get_source_graph(
        session,
        engine_manager,
        source,
        document_limit=1_000,
        event_limit=2_000,
        entity_limit=2_000,
    )
    nodes = [{"id": e.id, "name": e.name, "entity_type": e.type} for e in source_graph.entities]
    edges = [
        {
            "source": r.source_id,
            "target": r.target_id,
            "relation_type": r.description or r.kind,
            "amount_vnd": r.weight if r.weight > 1.0 else 0.0,
        }
        for r in source_graph.relations
    ]
    analyzer = GILGraphAnalyzer(ticker=source.name or source.id, equity_vnd=equity_vnd)
    analyzer.build_graph(nodes, edges)
    result = analyzer.evaluate()
    return result.to_dict()
