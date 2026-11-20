"""Project-scoped BFF endpoint for Continuum Knowledge Handover grounded AI tasks."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.deps import get_llm, get_session
from sag_api.core.errors import ForbiddenError
from sag_api.core.principal_assertion import VerifiedPrincipal, require_project_bff_principal
from sag_api.generation.llm import LLMClient
from sag_api.schemas.handover_assist import HandoverAssistRequest, HandoverAssistResponse
from sag_api.services.handover_assist_service import execute_handover_assist

router = APIRouter(prefix="/projects/{project_id}/handover", tags=["handover"])


@router.post(
    "/assist",
    response_model=HandoverAssistResponse,
    summary="Execute grounded handover proposal, draft synthesis, or cited Q&A within project scope",
)
async def handover_assist(
    project_id: str,
    body: HandoverAssistRequest,
    x_continuum_tenant_id: str | None = Header(None, alias="X-Continuum-Tenant-Id"),
    principal: VerifiedPrincipal = Depends(require_project_bff_principal),
    session: AsyncSession = Depends(get_session),
    llm: LLMClient = Depends(get_llm),
) -> HandoverAssistResponse:
    if project_id not in principal.allowed_project_ids or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    if x_continuum_tenant_id and x_continuum_tenant_id != principal.tenant_id:
        raise ForbiddenError("X-Continuum-Tenant-Id does not match the verified principal")

    return await execute_handover_assist(
        session,
        principal=principal,
        project_id=project_id,
        body=body,
        llm=llm,
    )

