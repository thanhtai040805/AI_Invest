"""Logic nghiệp vụ về xác thực và người dùng (chế độ một người dùng)."""

from __future__ import annotations

import secrets

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.core.errors import AuthError, ConflictError, ForbiddenError, ValidationError
from sag_api.core.security import hash_password, verify_password
from sag_api.db.models import User


def _password_hash(password: str) -> str:
    if len(password.encode("utf-8")) > 72:
        raise ValidationError("Mật khẩu không được vượt quá giới hạn 72 byte UTF-8 của bcrypt")
    return hash_password(password)


async def register_user(session: AsyncSession, *, email: str, password: str, name: str = "") -> User:
    from sag_api.core.config import settings

    existing = await session.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise ConflictError("Email này đã được đăng ký")

    # Danh tính chỉ có tên cục bộ chưa phải tài khoản mật khẩu; vẫn cho phép
    # tạo credential đầu tiên khi chuyển sang password mode.
    credential_count = await session.scalar(select(func.count()).select_from(User).where(User.email != "")) or 0
    if credential_count > 0 and not settings.allow_registration:
        raise ForbiddenError("Đăng ký đã bị đóng")

    user = User(
        email=email,
        password_hash=_password_hash(password),
        name=name or email.split("@")[0],
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(session: AsyncSession, *, email: str, password: str) -> User:
    user = await session.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(password, user.password_hash):
        raise AuthError("Email hoặc mật khẩu không chính xác")
    if not user.is_active:
        raise ForbiddenError("Tài khoản đã bị vô hiệu hóa")
    return user


async def authenticate_or_register(
    session: AsyncSession,
    *,
    name: str = "",
    email: str = "",
    password: str | None = None,
    allow_create: bool = True,
    exact_existing: bool = False,
) -> User:
    name = name.strip()
    email = email.strip().lower()
    password_supplied = bool(password)
    password = password or "admin"
    rename_local_user = False

    if email:
        user = await session.scalar(select(User).where(User.email == email))
    else:
        user = None
        if name:
            user = await session.scalar(select(User).where(User.name == name).order_by(User.created_at.asc()).limit(1))
        if user is None and not exact_existing:
            user = await session.scalar(select(User).order_by(User.created_at.asc()).limit(1))
            rename_local_user = user is not None and bool(name) and user.name != name

    if user is not None:
        if password_supplied and not verify_password(password, user.password_hash):
            raise AuthError("Xác thực danh tính thất bại")
        if not user.is_active:
            raise ForbiddenError("Tài khoản đã bị vô hiệu hóa")
        if rename_local_user:
            user.name = name
            await session.commit()
            await session.refresh(user)
        return user

    if not name:
        raise ValidationError("Vui lòng nhập tên trước")

    if not allow_create:
        raise AuthError("Email hoặc mật khẩu không chính xác")

    user = User(
        email=email,
        password_hash=_password_hash(password),
        name=name,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return user


async def get_or_create_continuum_user(
    session: AsyncSession,
    *,
    subject: str,
    name: str,
) -> User:
    from sag_api.core.config import settings

    if settings.auth_mode != "continuum":
        raise ForbiddenError("Continuum sign-in is not enabled")
    synthetic_email = f"continuum-{subject}@sag.invalid"
    user = await session.get(User, subject)
    if user is None:
        user = User(
            id=subject,
            email=synthetic_email,
            password_hash=_password_hash(secrets.token_urlsafe(32)),
            name=name,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            user = await session.get(User, subject)
            if user is None:
                user = await session.scalar(select(User).where(User.email == synthetic_email))
            if user is None:
                raise
            if user.id != subject or user.email != synthetic_email:
                raise ConflictError("Continuum identity conflicts with an existing SAG user") from None
            if not user.is_active:
                raise ForbiddenError("Tài khoản đã bị vô hiệu hóa") from None
    elif user.email != synthetic_email:
        raise ConflictError("Continuum identity conflicts with an existing SAG user")
    elif not user.is_active:
        raise ForbiddenError("Tài khoản đã bị vô hiệu hóa")
    else:
        user.name = name
        await session.commit()
    await session.refresh(user)
    return user


async def get_user(session: AsyncSession, user_id: str) -> User | None:
    return await session.get(User, user_id)
