"""Project-scoped answer share and service-only platform feedback routers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.errors import BadRequestError
from sag_api.core.principal_assertion import (
    VerifiedPrincipal,
    require_bff_service_token,
    require_project_bff_principal,
)
from sag_api.schemas.answer_actions import AdminFeedbackPageOut, SharedAnswerOut
from sag_api.services import answer_actions_service as actions_svc

project_share_router = APIRouter(prefix="/projects/{project_id}/shares", tags=["answer-actions"])
platform_feedback_router = APIRouter(prefix="/platform/ai", tags=["platform-ai"])

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_ALLOWED_ADMIN_QUERY_KEYS = frozenset(
    {
        "organization_id",
        "organizationId",
        "project_id",
        "projectId",
        "rating",
        "from",
        "to",
        "cursor",
        "limit",
    }
)


async def require_project_share_principal(request: Request) -> VerifiedPrincipal:
    """Authorize share read/revoke strictly via BFF service authentication after live Project membership check."""
    await require_bff_service_token(request)
    return await require_project_bff_principal(request)


@project_share_router.get("/{share_id}", response_model=SharedAnswerOut)
async def get_shared_answer(
    project_id: str,
    share_id: str,
    principal: VerifiedPrincipal = Depends(require_project_share_principal),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    result = await actions_svc.read_project_share(
        session,
        project_id=project_id,
        share_id=share_id,
        principal=principal,
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
        headers=_NO_STORE_HEADERS,
    )


@project_share_router.delete("/{share_id}", status_code=204)
async def revoke_shared_answer(
    project_id: str,
    share_id: str,
    principal: VerifiedPrincipal = Depends(require_project_share_principal),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await actions_svc.revoke_project_share(
        session,
        project_id=project_id,
        share_id=share_id,
        principal=principal,
    )
    return Response(status_code=204, headers=_NO_STORE_HEADERS)


@platform_feedback_router.get("/feedback", response_model=AdminFeedbackPageOut)
async def list_platform_feedback(
    request: Request,
    _service_auth: None = Depends(require_bff_service_token),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    params = request.query_params
    unknown = sorted(set(params.keys()) - _ALLOWED_ADMIN_QUERY_KEYS)
    if unknown:
        raise BadRequestError(f"Unsupported query parameters: {', '.join(unknown)}")

    # Reject duplicate parameter occurrences cleanly
    for key in params.keys():
        if len(params.getlist(key)) > 1:
            raise BadRequestError(f"Duplicate query parameter: {key}")

    org_id = params.get("organizationId") or params.get("organization_id")
    if (
        params.get("organizationId") is not None
        and params.get("organization_id") is not None
        and params.get("organizationId") != params.get("organization_id")
    ):
        raise BadRequestError("Conflicting organizationId query parameters")

    proj_id = params.get("projectId") or params.get("project_id")
    if (
        params.get("projectId") is not None
        and params.get("project_id") is not None
        and params.get("projectId") != params.get("project_id")
    ):
        raise BadRequestError("Conflicting projectId query parameters")

    raw_limit = params.get("limit")
    limit = 20
    if raw_limit is not None:
        if not raw_limit.isdigit():
            raise BadRequestError("Query parameter 'limit' must be a positive integer")
        limit = int(raw_limit)

    page = await actions_svc.list_admin_feedback(
        session,
        organization_id=org_id,
        project_id=proj_id,
        rating=params.get("rating"),
        from_raw=params.get("from"),
        to_raw=params.get("to"),
        cursor=params.get("cursor"),
        limit=limit,
    )
    return JSONResponse(
        status_code=200,
        content=page.model_dump(mode="json"),
        headers=_NO_STORE_HEADERS,
    )
