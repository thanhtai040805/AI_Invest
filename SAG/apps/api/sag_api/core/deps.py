"""Dependency FastAPI: xác thực + singleton cấp ứng dụng. Một người dùng, không có workspace/vai trò."""

from __future__ import annotations

import hmac
from typing import Any

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.db import get_session
from sag_api.core.errors import AuthError
from sag_api.core.security import decode_token
from sag_api.db.models import User
from sag_api.services.auth_service import get_user

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: AsyncSession = Depends(get_session),
) -> User:
    if creds is None:
        from sag_api.core.config import settings
        if settings.environment == "dev" or settings.debug:
            user = await get_user(session, "dev_system_user")
            if user is None:
                from sag_api.db.models.user import User as UserModel
                from sag_api.core.security import hash_password
                user = UserModel(
                    id="dev_system_user",
                    email="system@local.aiinvest",
                    name="System Local Agent",
                    password_hash=hash_password("localdev123"),
                    is_active=True,
                )
                session.add(user)
                await session.commit()
                await session.refresh(user)
            request.state.user = user
            return user
        raise AuthError("Thiếu token xác thực")
    try:
        payload = decode_token(creds.credentials)
    except jwt.PyJWTError as e:
        raise AuthError("Token không hợp lệ hoặc đã hết hạn") from e
    user_id = payload.get("sub")
    user = await get_user(session, user_id) if user_id else None
    if user is None or not user.is_active:
        raise AuthError("Người dùng không tồn tại hoặc đã bị vô hiệu hóa")
    request.state.user = user
    return user


def get_engine_manager(request: Request) -> Any:
    return request.app.state.engine_manager


def get_job_queue(request: Request) -> Any:
    return request.app.state.job_queue


def get_llm(request: Request) -> Any:
    return request.app.state.llm


def get_agent_runtime(request: Request) -> Any:
    return request.app.state.agent_runtime


def get_tool_registry():
    """Bảng đăng ký công cụ của Agent (công cụ truy vấn/thực thể tích hợp + công cụ MCP được tiêm lúc chạy)."""
    from sag_api.tools import registry

    return registry


async def require_service_or_admin(
    request: Request,
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> None:
    """Authenticate v2 internal/admin APIs without the generic web user model."""
    from sag_api.core.config import settings

    if settings.environment == "dev" or settings.debug:
        if creds is None:
            return
    if creds is None:
        raise AuthError("Thiếu token xác thực")
    provided = creds.credentials
    allowed = [token for token in (settings.service_token, settings.admin_token) if token]
    if not allowed:
        raise AuthError("SAG_SERVICE_TOKEN hoặc SAG_ADMIN_TOKEN chưa được cấu hình")
    if not any(hmac.compare_digest(provided, token) for token in allowed):
        raise AuthError("Token không hợp lệ")
