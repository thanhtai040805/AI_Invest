"""Trừu tượng hóa Connector — giao diện pluggable của tầng thu thập.

Một "connector" chịu trách nhiệm biến nguồn thông tin bên ngoài thành file cục bộ để engine xử lý:

- **Tĩnh** (ví dụ upload file): tài liệu do người dùng đẩy trực tiếp, `supports_sync=False`.
- **Động** (ví dụ Web / Notion / S3, mở rộng sau): triển khai `discover()` liệt kê tài liệu từ xa,
  `fetch()` kéo về cục bộ, được task `sync_source` gọi định kỳ.

Thêm connector = kế thừa `Connector` triển khai phương thức + đăng ký trong `registry`, không cần sửa logic tầng trên.
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from sag_api.enums import ConnectorKind


@dataclass
class ConfigField:
    """Mô tả mục cấu hình của connector (để frontend render form động)."""

    key: str
    label: str
    type: str = "string"  # string | password | number | boolean | url
    required: bool = False
    placeholder: str = ""
    help: str = ""


@dataclass
class ConnectorMeta:
    kind: ConnectorKind
    title: str
    description: str
    supports_sync: bool = False
    config_fields: list[ConfigField] = field(default_factory=list)

    def to_public(self) -> dict[str, Any]:
        return {
            "kind": self.kind.value,
            "title": self.title,
            "description": self.description,
            "supports_sync": self.supports_sync,
            "config_fields": [f.__dict__ for f in self.config_fields],
        }


@dataclass
class DiscoveredDoc:
    """Một tài liệu từ xa được connector động phát hiện."""

    external_id: str
    filename: str
    content_type: str = "application/octet-stream"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LocalFile:
    """File đã tải về cục bộ, sẵn sàng giao cho engine ingest."""

    path: str
    filename: str
    content_type: str
    size_bytes: int


class Connector(ABC):
    """Lớp cơ sở của mọi connector."""

    meta: ConnectorMeta

    def validate_config(self, config: dict[str, Any]) -> None:
        """Kiểm tra cấu hình nguồn; nếu không hợp lệ sẽ ném `ValidationError`. Mặc định kiểm tra các trường bắt buộc."""
        from sag_api.core.errors import ValidationError

        for f in self.meta.config_fields:
            if f.required and not (config or {}).get(f.key):
                raise ValidationError(f"Thiếu mục cấu hình bắt buộc: {f.label} ({f.key})")

    async def discover(self, config: dict[str, Any]) -> list[DiscoveredDoc]:
        """Liệt kê tài liệu từ xa (connector động triển khai)."""
        raise NotImplementedError

    async def fetch(self, config: dict[str, Any], doc: DiscoveredDoc) -> LocalFile:
        """Kéo một tài liệu từ xa về cục bộ (connector động triển khai)."""
        raise NotImplementedError
