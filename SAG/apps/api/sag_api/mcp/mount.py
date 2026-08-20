"""Gắn MCP kho kiến thức SAG như endpoint Streamable-HTTP vào FastAPI.

Host bên ngoài (Claude Desktop / Cursor) có thể gắn:

    http://<host>/mcp/                         # toàn bộ kho kiến thức
    http://<host>/mcp/?source_id=<id nguồn>    # một nguồn duy nhất

Yêu cầu trước tiên được kiểm tra JWT, rồi tải một hoặc tất cả Source theo `source_id` tùy chọn và tiêm vào contextvar.
Phạm vi được cô lập theo yêu cầu, host bên ngoài và agent trong tiến trình có thể dùng chung cùng một server.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs

import jwt
from mcp.server.transport_security import TransportSecuritySettings
from sqlalchemy import select

from sag_api.core.db import SessionLocal
from sag_api.core.logging import get_logger
from sag_api.core.security import decode_token
from sag_api.db.models import Source
from sag_api.mcp.server import build_source_mcp, use_scope

if TYPE_CHECKING:
    from fastapi import FastAPI
    from mcp.server.fastmcp import FastMCP

log = get_logger("mcp.http")


async def _send_json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [(b"content-type", b"application/json")],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _bearer(scope) -> str | None:
    for name, value in scope.get("headers") or []:
        if name == b"authorization":
            raw = value.decode("latin-1")
            return raw[7:].strip() if raw.lower().startswith("bearer ") else raw.strip()
    return None


class ScopedKnowledgeMCP:
    """Bọc ASGI: xác thực và tiêm phạm vi toàn kho hoặc một nguồn, rồi ủy quyền cho ứng dụng MCP."""

    def __init__(self, parent_app: FastAPI, mcp_asgi) -> None:
        self._parent = parent_app
        self._mcp = mcp_asgi

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await self._mcp(scope, receive, send)
            return

        params = parse_qs((scope.get("query_string") or b"").decode())
        source_id = (params.get("source_id") or [""])[0].strip()

        token = _bearer(scope)
        if not token:
            await _send_json(send, 401, {"error": "Thiếu token xác thực"})
            return
        try:
            decode_token(token)
        except jwt.PyJWTError:
            await _send_json(send, 401, {"error": "Token không hợp lệ hoặc đã hết hạn"})
            return

        async with SessionLocal() as session:
            statement = select(Source).order_by(Source.created_at, Source.id)
            if source_id:
                statement = statement.where(Source.id == source_id)
            sources = tuple((await session.execute(statement)).scalars().all())
        if source_id and not sources:
            await _send_json(send, 404, {"error": "Nguồn không tồn tại"})
            return

        engine_manager = self._parent.state.engine_manager
        with use_scope(engine_manager, sources):
            await self._mcp(scope, receive, send)


def attach_source_mcp(app: FastAPI) -> FastMCP:
    """Tạo MCP kho kiến thức bản HTTP và gắn vào `/mcp`.

    Route của FastMCP lớp trong được chuyển về gốc `/`, lớp ngoài dùng `Mount("/mcp")` đỡ — tránh
    đường dẫn kép kiểu `/mcp` trong `/mcp`. Host bên ngoài dùng `/mcp/` có dấu gạch chéo; `source_id`
    chỉ dùng cho chế độ tương thích một nguồn tùy chọn.
    """
    # FastMCP mặc định hiểu host=127.0.0.1 là "chỉ chấp nhận Host localhost".
    # Ứng dụng ASGI này thực tế gắn dưới FastAPI có thể truy cập qua mạng LAN/reverse proxy, và lớp
    # ngoài ép buộc xác thực Bearer, do đó tắt danh sách trắng Host dành riêng localhost của SDK.
    mcp = build_source_mcp(
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    mcp.settings.streamable_http_path = "/"
    mcp_asgi = mcp.streamable_http_app()  # Tạo session_manager một cách lười biếng
    app.mount("/mcp", ScopedKnowledgeMCP(app, mcp_asgi))
    return mcp
