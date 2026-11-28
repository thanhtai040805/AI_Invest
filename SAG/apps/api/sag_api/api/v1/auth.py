from __future__ import annotations

import json
import re
import secrets
import unicodedata

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.config import settings
from sag_api.core.db import get_session
from sag_api.core.deps import get_current_user
from sag_api.core.errors import ForbiddenError, ValidationError
from sag_api.core.principal_assertion import require_bff_service_token, required_bff_header
from sag_api.core.security import create_access_token
from sag_api.db.models import User
from sag_api.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserOut
from sag_api.services.auth_service import (
    authenticate,
    authenticate_or_register,
    get_or_create_continuum_user,
    register_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_SAFE_CONTINUUM_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
_MAX_CONTINUUM_LOGIN_BODY_BYTES = 16384
_MAX_CONTINUUM_ALLOWED_PROJECTS = 256


@router.get("/status")
async def auth_status(session: AsyncSession = Depends(get_session)) -> dict[str, bool | str]:
    if settings.auth_mode == "continuum":
        return {"mode": "continuum", "registration_required": False, "registration_open": False}
    if settings.auth_mode == "local":
        return {"mode": "local", "registration_required": False, "registration_open": False}

    credential_count = await session.scalar(select(func.count()).select_from(User).where(User.email != "")) or 0
    registration_required = credential_count == 0
    return {
        "mode": "password",
        "registration_required": registration_required,
        "registration_open": registration_required or settings.allow_registration,
    }


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)) -> TokenResponse:
    if settings.auth_mode != "password":
        raise ForbiddenError("当前部署未启用邮箱密码认证")
    user = await register_user(session, email=body.email, password=body.password, name=body.name)
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    if settings.auth_mode == "continuum":
        raise ForbiddenError("Sign in through Continuum")
    if settings.auth_mode == "password":
        user = await authenticate(session, email=body.email, password=body.password or "")
    else:
        user = await authenticate_or_register(
            session,
            name=body.name,
            email="",
            password=None,
            allow_create=True,
            exact_existing=False,
        )
    return TokenResponse(access_token=create_access_token(user.id), user=UserOut.model_validate(user))


def _extract_continuum_login_options(raw_body: bytes) -> tuple[str | None, list[str] | None]:
    if not raw_body or not raw_body.strip():
        return None, None
    if len(raw_body) > _MAX_CONTINUUM_LOGIN_BODY_BYTES:
        raise ValidationError("Continuum login request body is too large")
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError("Malformed Continuum login request body") from exc
    if not isinstance(payload, dict):
        raise ValidationError("Continuum login request body must be a JSON object")

    raw_display_name = payload.get("displayName")
    if raw_display_name is None:
        raw_display_name = payload.get("display_name")
    cleaned_display_name: str | None = None
    if raw_display_name is not None:
        if not isinstance(raw_display_name, str):
            raise ValidationError("Continuum display name must be a string")
        normalized = unicodedata.normalize("NFC", raw_display_name)
        cleaned = "".join(
            " " if unicodedata.category(ch).startswith("C") else ch
            for ch in normalized
        )
        cleaned = " ".join(cleaned.split()).strip()[:120]
        cleaned_display_name = cleaned or None

    raw_allowed_projects = payload.get("allowed_project_ids")
    if raw_allowed_projects is None:
        raw_allowed_projects = payload.get("allowedProjectIds")
    cleaned_allowed_projects: list[str] | None = None
    if raw_allowed_projects is not None:
        if (
            not isinstance(raw_allowed_projects, list)
            or not raw_allowed_projects
            or len(raw_allowed_projects) > _MAX_CONTINUUM_ALLOWED_PROJECTS
        ):
            raise ValidationError(
                "Continuum allowed_project_ids must be a non-empty list of up to 256 project IDs"
            )
        deduped: set[str] = set()
        for item in raw_allowed_projects:
            if not isinstance(item, str):
                raise ValidationError("Continuum allowed_project_ids entries must be strings")
            trimmed = item.strip()
            if (
                not trimmed
                or trimmed != item
                or trimmed.lower() == "all"
                or not _SAFE_CONTINUUM_PROJECT_ID_RE.fullmatch(trimmed)
            ):
                raise ValidationError("Continuum allowed_project_ids contains an invalid project ID")
            deduped.add(trimmed)
        cleaned_allowed_projects = sorted(deduped)

    return cleaned_display_name, cleaned_allowed_projects


def _extract_optional_continuum_display_name(raw_body: bytes) -> str | None:
    display_name, _ = _extract_continuum_login_options(raw_body)
    return display_name


@router.post("/continuum-login", response_model=TokenResponse)
async def continuum_login(
    request: Request,
    response: Response,
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    response.headers["Cache-Control"] = "no-store"
    await require_bff_service_token(request)
    subject = required_bff_header(request, "X-Continuum-User-Id", max_length=32)
    header_name = required_bff_header(request, "X-Continuum-Name", max_length=120)
    organization_id = required_bff_header(request, "X-Continuum-Organization-Id", max_length=64)
    project_id = required_bff_header(request, "X-Continuum-Project-Id", max_length=64)
    if not re.fullmatch(r"[a-fA-F0-9]{24}", subject):
        raise ValidationError("Continuum user ID is invalid")
    body_display_name, body_allowed_project_ids = _extract_continuum_login_options(await request.body())
    if project_id == "all":
        if not body_allowed_project_ids:
            raise ValidationError("All-projects Continuum session requires non-empty allowed_project_ids")
    elif body_allowed_project_ids is not None and body_allowed_project_ids != [project_id]:
        raise ValidationError("allowed_project_ids is only supported when project_id is 'all'")

    name = body_display_name or header_name
    user = await get_or_create_continuum_user(
        session,
        subject=subject,
        name=name,
    )
    extra_claims: dict[str, object] = {
        "org_id": organization_id,
        "tenant_id": organization_id,
        "project_id": project_id,
        "security_partition": project_id,
        "jti": secrets.token_urlsafe(24),
    }
    if project_id == "all" and body_allowed_project_ids:
        extra_claims["allowed_project_ids"] = body_allowed_project_ids
        extra_claims["allowed_partition_ids"] = body_allowed_project_ids

    return TokenResponse(
        access_token=create_access_token(
            user.id,
            extra=extra_claims,
        ),
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(user)
