"""Project-scoped BFF endpoints for AI Chat Mind Map synthesis and persisted snapshot retrieval."""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.deps import get_llm, get_session
from sag_api.core.errors import BadRequestError, ForbiddenError
from sag_api.core.principal_assertion import (
    VerifiedPrincipal,
    require_bff_service_token,
    require_project_bff_principal,
)
from sag_api.schemas.mind_map import (
    MindMapGenerateIn,
    MindMapOut,
    normalize_mind_map_topic,
)
from sag_api.services.agent_domain import _reject_json_constant
from sag_api.services.mind_map_service import (
    generate_project_mind_map,
    get_saved_project_mind_map,
)

router = APIRouter(prefix="/projects/{project_id}/mind-map", tags=["mind-map"])

_SAFE_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
_MAX_BODY_BYTES = 16 * 1024
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


async def require_mind_map_bff_principal(request: Request) -> VerifiedPrincipal:
    """Enforce Continuum BFF service authentication unconditionally (no direct user-JWT bypass)."""
    await require_bff_service_token(request)
    return await require_project_bff_principal(request)


def _verify_project_and_tenant_scope(
    *,
    project_id: str,
    principal: VerifiedPrincipal,
    x_continuum_tenant_id: str | None,
) -> None:
    if not _SAFE_PROJECT_ID_RE.fullmatch(project_id):
        raise BadRequestError("Invalid project_id format")
    is_all_projects_scope = (
        project_id == "all"
        and getattr(principal, "conversation_scope", None) == "all"
        and bool(principal.allowed_project_ids)
    )
    if (not is_all_projects_scope and project_id not in principal.allowed_project_ids) or not principal.tenant_id:
        raise ForbiddenError(
            f"Principal '{principal.subject}' is not authorized to access project '{project_id}'"
        )
    if x_continuum_tenant_id is not None and x_continuum_tenant_id != principal.tenant_id:
        raise ForbiddenError("X-Continuum-Tenant-Id does not match the verified principal")


async def _parse_mind_map_body(request: Request) -> MindMapGenerateIn:
    raw_bytes = await request.body()
    if len(raw_bytes) > _MAX_BODY_BYTES:
        raise BadRequestError("Request body exceeds the maximum allowed size")
    if not raw_bytes or not raw_bytes.strip():
        raw_obj: Any = {}
    else:
        try:
            raw_obj = json.loads(raw_bytes, parse_constant=_reject_json_constant)
        except ValueError as exc:
            raise BadRequestError("Malformed JSON request body") from exc
    if not isinstance(raw_obj, dict):
        raise BadRequestError("Request body must be a JSON object")
    try:
        return MindMapGenerateIn.model_validate(raw_obj)
    except PydanticValidationError as exc:
        first_msg = (
            exc.errors()[0].get("msg", "Invalid request body")
            if exc.errors()
            else "Invalid request body"
        )
        raise BadRequestError(str(first_msg)) from exc


@router.get(
    "",
    response_model=MindMapOut,
    summary="Read the persisted Mind Map snapshot for an active Project without invoking the LLM",
)
async def read_project_mind_map(
    project_id: str,
    request: Request,
    x_continuum_tenant_id: str | None = Header(None, alias="X-Continuum-Tenant-Id"),
    principal: VerifiedPrincipal = Depends(require_mind_map_bff_principal),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    _verify_project_and_tenant_scope(
        project_id=project_id,
        principal=principal,
        x_continuum_tenant_id=x_continuum_tenant_id,
    )
    unknown_keys = sorted(set(request.query_params.keys()) - {"topic"})
    if unknown_keys:
        raise BadRequestError(f"Unsupported query parameters: {', '.join(unknown_keys)}")

    topic_values = request.query_params.getlist("topic")
    if len(topic_values) > 1:
        raise BadRequestError("Duplicate topic query parameter is not allowed")

    normalized_topic: str | None = None
    if len(topic_values) == 1:
        try:
            normalized_topic = normalize_mind_map_topic(topic_values[0])
        except ValueError as exc:
            raise BadRequestError(str(exc)) from exc

    result = await get_saved_project_mind_map(
        session,
        principal=principal,
        project_id=project_id,
        topic=normalized_topic,
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
        headers=_NO_STORE_HEADERS,
    )


@router.post(
    "",
    response_model=MindMapOut,
    summary="Generate and persist an on-demand Mind Map over authorized search-ready Project documents",
)
async def create_project_mind_map(
    project_id: str,
    request: Request,
    x_continuum_tenant_id: str | None = Header(None, alias="X-Continuum-Tenant-Id"),
    principal: VerifiedPrincipal = Depends(require_mind_map_bff_principal),
    session: AsyncSession = Depends(get_session),
    llm: Any = Depends(get_llm),
) -> JSONResponse:
    if not _SAFE_PROJECT_ID_RE.fullmatch(project_id):
        raise BadRequestError("Invalid project_id format")
    if request.query_params:
        unknown = ", ".join(sorted(set(request.query_params.keys())))
        raise BadRequestError(f"Unsupported query parameters: {unknown}")
    _verify_project_and_tenant_scope(
        project_id=project_id,
        principal=principal,
        x_continuum_tenant_id=x_continuum_tenant_id,
    )

    payload = await _parse_mind_map_body(request)
    result = await generate_project_mind_map(
        session,
        principal=principal,
        project_id=project_id,
        payload=payload,
        llm=llm,
    )
    return JSONResponse(
        status_code=200,
        content=result.model_dump(mode="json"),
        headers=_NO_STORE_HEADERS,
    )
