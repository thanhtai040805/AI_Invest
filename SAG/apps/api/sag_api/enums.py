"""Các enum dùng chung giữa các tầng (model / schema / service đều import được, không tác dụng phụ)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

SearchStrategy = Literal["vector", "multi"]
SEARCH_STRATEGIES = frozenset({"vector", "multi"})


def normalize_search_strategy(value: str) -> str:
    """Chuyển chiến lược tìm kiếm atomic đã ngừng sang tìm kiếm chính xác; giá trị khác giao cho caller kiểm tra."""
    return "multi" if value == "atomic" else value


class SourceType(StrEnum):
    DOCUMENT = "document"
    WEB = "web"
    MESSAGE = "message"
    AUDIO = "audio"


class ConnectorKind(StrEnum):
    FILE_UPLOAD = "file_upload"
    WEB = "web"
    # Dự trữ: NOTION = "notion"; S3 = "s3"; CONFLUENCE = "confluence"; ...


# Connector → loại nguồn mặc định
CONNECTOR_SOURCE_TYPE = {
    ConnectorKind.FILE_UPLOAD: SourceType.DOCUMENT,
    ConnectorKind.WEB: SourceType.WEB,
}


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class DocumentStatus(StrEnum):
    PENDING = "pending"        # Đã đăng ký, chờ xử lý
    LOADING = "loading"        # Đang ingest (phân tích → chia chunk → lưu trữ → vector)
    EXTRACTING = "extracting"  # Đang extract (trích xuất sự kiện / thực thể)
    PAUSED = "paused"          # Đã tạm dừng trích xuất, có thể tiếp tục từ điểm dừng chunk
    READY = "ready"            # Xử lý xong, có thể truy vấn
    FAILED = "failed"


class JobType(StrEnum):
    PROCESS_DOCUMENT = "process_document"
    SYNC_SOURCE = "sync_source"
    INDEX_UNIVERSE = "index_universe"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class BindingTargetType(StrEnum):
    SOURCE = "source"
    MCP_SERVER = "mcp_server"  # Phase C: gắn MCP server làm nguồn công cụ
