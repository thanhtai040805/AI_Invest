"""Bộ lọc và làm sạch thông tin nhạy cảm tập trung (Zero-Secret Sanitizer).

Triết lý Ponytail: Tối giản, thuần regex stdlib, không phụ thuộc thư viện ngoài.
Loại bỏ API keys, Bearer tokens, mật khẩu trong URL, query params nhạy cảm
trước khi ghi log hoặc lưu vào database.
"""

from __future__ import annotations

import re

_BEARER_TOKEN = re.compile(r"(?i)bearer\s+\S+")
_API_KEY = re.compile(r"(?i)\b(?:sk|ak)-[a-z0-9._-]{6,}\b")
_SECRET_QUERY_KEYS = re.compile(
    r"(?i)([?&](?:token|key|signature|credential|authorization|auth|api[-_]?key|access[-_]?token|refresh[-_]?token|id[-_]?token|client[-_]?secret|password|secret|x-amz-[^=]+)=)[^&#\s]+"
)
_URL_CREDENTIALS = re.compile(r"(?i)([a-z0-9+.-]+://)([^:\s/@]+:[^@\s/]+@)")


def sanitize_error_message(value: object, *, max_length: int = 500) -> str:
    """Tẩy rửa các khóa bí mật và thông tin nhạy cảm trong chuỗi lỗi."""
    if value is None:
        return ""
    message = " ".join(str(value).split())
    if not message:
        return ""
    # 1. Che giấu thông tin xác thực nhúng trong URL: http://user:pass@host
    message = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", message)
    # 2. Che giấu query parameters nhạy cảm: ?api-key=..., &token=...
    message = _SECRET_QUERY_KEYS.sub(r"\1[REDACTED]", message)
    # 3. Che giấu Bearer tokens
    message = _BEARER_TOKEN.sub("Bearer [REDACTED]", message)
    # 4. Che giấu API keys (OpenAI sk-..., AWS ak-...)
    message = _API_KEY.sub("[REDACTED]", message)
    return message[:max_length]
