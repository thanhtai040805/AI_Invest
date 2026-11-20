"""文件上传连接器 —— MVP 内置的静态连接器。

文档由用户经 API 直接上传，不走 discover/fetch；此连接器主要提供元数据与配置校验，
并作为「采集层抽象」的第一个落地实现，为后续动态连接器立好接口范式。
"""

from __future__ import annotations

from sag_api.connectors.base import Connector, ConnectorMeta
from sag_api.enums import ConnectorKind


class FileUploadConnector(Connector):
    meta = ConnectorMeta(
        kind=ConnectorKind.FILE_UPLOAD,
        title="File Upload",
        description="Upload local documents (Markdown / text / PDF, etc.) to be parsed, chunked, embedded, and extracted into events and entities.",
        supports_sync=False,
        config_fields=[],
    )
