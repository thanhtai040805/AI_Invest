"""连接器注册表 —— 通过 kind 查找连接器；新增连接器在此登记。"""

from __future__ import annotations

from sag_api.connectors.base import Connector
from sag_api.connectors.file_upload import FileUploadConnector
from sag_api.connectors.web import WebConnector
from sag_api.core.errors import NotFoundError
from sag_api.enums import ConnectorKind


class ConnectorRegistry:
    def __init__(self) -> None:
        self._by_kind: dict[ConnectorKind, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._by_kind[connector.meta.kind] = connector

    def get(self, kind: ConnectorKind | str) -> Connector:
        key = ConnectorKind(kind) if not isinstance(kind, ConnectorKind) else kind
        connector = self._by_kind.get(key)
        if connector is None:
            raise NotFoundError(f"未知连接器：{key}")
        return connector

    def all(self) -> list[Connector]:
        return list(self._by_kind.values())


registry = ConnectorRegistry()
registry.register(FileUploadConnector())
registry.register(WebConnector())
# 未来：registry.register(NotionConnector()); registry.register(S3Connector()); ...
