"""Connector upload file — connector tĩnh có sẵn của MVP.

Tài liệu do người dùng tải lên trực tiếp qua API, không qua discover/fetch; connector này chủ yếu cung cấp metadata và kiểm tra cấu hình,
đồng thời là triển khai đầu tiên của "trừu tượng hóa tầng thu thập", đặt khuôn mẫu interface cho các connector động sau này.
"""

from __future__ import annotations

from sag_api.connectors.base import Connector, ConnectorMeta
from sag_api.enums import ConnectorKind


class FileUploadConnector(Connector):
    meta = ConnectorMeta(
        kind=ConnectorKind.FILE_UPLOAD,
        title="Tải lên tệp",
        description="Tải lên tài liệu cục bộ (Markdown / văn bản / PDF...), engine sẽ phân tích, chia chunk, vector hóa và trích xuất sự kiện cùng thực thể.",
        supports_sync=False,
        config_fields=[],
    )
