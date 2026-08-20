"""Lớp adapter zleap-sag —— nơi duy nhất trong toàn dự án import `zleap-sag`.

Chỉ phơi ra DTO riêng của sag và `EngineManager`, nhờ đó tách chi tiết triển khai engine khỏi logic miền,
khi thay / nâng cấp engine trong tương lai thì mọi thay đổi hội tụ trong thư mục này.
"""

from sag_api.sag.dto import (
    ChunkInfo,
    EntityInfo,
    GraphAssociationInfo,
    GraphEventInfo,
    ProcessOutcome,
    RetrievedSection,
    SearchOutcome,
    SourceGraphInfo,
)
from sag_api.sag.engine_manager import EngineManager

__all__ = [
    "ChunkInfo",
    "EngineManager",
    "EntityInfo",
    "GraphAssociationInfo",
    "GraphEventInfo",
    "ProcessOutcome",
    "RetrievedSection",
    "SearchOutcome",
    "SourceGraphInfo",
]
