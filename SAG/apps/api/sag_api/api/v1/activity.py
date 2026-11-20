"""近期动态 —— 知识库时间线：最近文档（会话不属于知识库动态）。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.core.principal_assertion import VerifiedPrincipal, require_project_read_principal
from sag_api.db.models import User
from sag_api.services.source_service import list_authorized_documents, list_sources

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("")
async def list_activity(
    limit: int = Query(default=20, ge=1, le=50),
    source_ids: list[str] | None = Query(default=None, max_length=100),
    _user: User = Depends(get_current_user),
    principal: VerifiedPrincipal = Depends(require_project_read_principal),
    session: AsyncSession = Depends(get_session),
) -> list[dict]:
    sources = await list_sources(session, principal=principal)
    authorized_source_ids = {source.id for source in sources}
    if source_ids is not None:
        requested_source_ids = {
            source_id.strip() for source_id in source_ids if source_id.strip()
        }
        authorized_source_ids.intersection_update(requested_source_ids)
    if not authorized_source_ids:
        return []
    documents = await list_authorized_documents(
        session,
        principal=principal,
        source_ids=sorted(authorized_source_ids),
        limit=limit,
    )
    source_names = {
        source.id: source.name
        for source in sources
        if source.id in authorized_source_ids
    }
    items: list[dict] = [
        {
            "type": "document",
            "id": d.id,
            "source_id": d.source_id,
            "title": d.filename,
            "subtitle": source_names.get(d.source_id, ""),
            "status": d.status.value,
            "at": d.created_at.isoformat(),
        }
        for d in documents[:limit]
    ]
    items.sort(key=lambda x: x["at"], reverse=True)
    return items[:limit]
