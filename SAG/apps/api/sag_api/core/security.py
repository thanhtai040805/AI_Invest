"""Các thành phần xác thực cơ bản: băm mật khẩu bằng bcrypt và token JWT."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import bcrypt
import jwt

from sag_api.core.config import settings

_ALGO = "HS256"
_BCRYPT_MAX_BYTES = 72  # Giới hạn cứng của bcrypt.


def _clip(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) > _BCRYPT_MAX_BYTES:
        raise ValueError(f"Password exceeds bcrypt's {_BCRYPT_MAX_BYTES}-byte limit")
    return encoded


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_clip(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        # Older bcrypt records were created from the first 72 bytes of a password.
        return bcrypt.checkpw(password.encode("utf-8")[:_BCRYPT_MAX_BYTES], password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, extra: dict[str, Any] | None = None) -> str:
    now = datetime.now(UTC)
    lifetime_minutes = (
        min(settings.access_token_expire_minutes, 15)
        if settings.auth_mode == "continuum"
        else settings.access_token_expire_minutes
    )
    payload: dict[str, Any] = {
        "sub": subject,
        "auth_mode": settings.auth_mode,
        "iat": now,
        "exp": now + timedelta(minutes=lifetime_minutes),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.secret_key, algorithm=_ALGO)


def decode_token(token: str) -> dict[str, Any]:
    """Giải mã và xác thực JWT; nếu thất bại sẽ phát sinh `jwt.PyJWTError`."""
    return jwt.decode(token, settings.secret_key, algorithms=[_ALGO])
