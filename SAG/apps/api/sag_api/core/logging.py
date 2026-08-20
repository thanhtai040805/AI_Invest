"""Cấu hình log nhẹ + middleware theo dõi request."""

from __future__ import annotations

import contextvars
import logging
import sys
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

_CONFIGURED = False

# ID theo dõi của request hiện tại, để log và xử lý lỗi tham chiếu
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="-")


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(_RequestIdFilter())
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s  %(levelname)-7s  [%(request_id)s]  %(name)s  %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers = [handler]
    # Giảm nhiễu từ bên thứ ba, và cấm model client xuất toàn bộ prompt/nội dung ở chế độ DEBUG.
    for noisy in (
        "httpx",
        "httpcore",
        "openai",
        "lancedb",
        "aiosqlite",
        "LiteLLM",
        "LiteLLM Router",
        "LiteLLM Proxy",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("zleap.sag.ai.openai").setLevel(logging.INFO)
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"sag.{name}")


class _RequestIdFilter(logging.Filter):
    """Chèn id request hiện tại vào mỗi bản ghi log; khi không nằm trong ngữ cảnh request thì là '-'."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = request_id_var.get()
        return True


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Gán id theo dõi cho mỗi request: đầu vào lấy X-Request-Id hoặc tạo mới, đầu ra ghi ngược vào header phản hồi."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:16]
        token = request_id_var.set(rid)
        request.state.request_id = rid
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        response.headers["X-Request-Id"] = rid
        return response
