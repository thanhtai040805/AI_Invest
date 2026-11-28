"""连接器抽象 —— 采集层的可插拔接口。

一个「连接器」负责把外部信息源变成可交给引擎处理的本地文件：

- **静态**（如文件上传）：文档由用户直接推送，`supports_sync=False`。
- **动态**（如 Web / Notion / S3，后续拓展）：实现 `discover()` 列举远端文档、
  `fetch()` 拉取到本地，由 `sync_source` 任务周期性调用。

新增连接器 = 继承 `Connector` 实现方法 + 在 `registry` 注册，无需改动上层逻辑。
"""

from __future__ import annotations

from abc import ABC
from dataclasses import dataclass, field
from typing import Any

from sag_api.enums import ConnectorKind


@dataclass
class ConfigField:
    """连接器配置项描述（供前端动态渲染表单）。"""

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
    """动态连接器发现的一篇远端文档。"""

    external_id: str
    filename: str
    content_type: str = "application/octet-stream"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class LocalFile:
    """已落到本地、可交给引擎 ingest 的文件。"""

    path: str
    filename: str
    content_type: str
    size_bytes: int


class Connector(ABC):
    """所有连接器的基类。"""

    meta: ConnectorMeta

    def validate_config(self, config: dict[str, Any]) -> None:
        """校验信源配置；不合法时抛 `ValidationError`。默认校验必填项。"""
        from sag_api.core.errors import ValidationError

        for f in self.meta.config_fields:
            if f.required and not (config or {}).get(f.key):
                raise ValidationError(f"缺少必填配置项：{f.label}（{f.key}）")

    async def discover(self, config: dict[str, Any]) -> list[DiscoveredDoc]:
        """列举远端文档（动态连接器实现）。"""
        raise NotImplementedError

    async def fetch(self, config: dict[str, Any], doc: DiscoveredDoc) -> LocalFile:
        """把一篇远端文档拉取到本地（动态连接器实现）。"""
        raise NotImplementedError
