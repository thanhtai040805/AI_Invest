"""zleap-sag 适配层 —— 全项目唯一 import `zleap-sag` 的地方。

对外只暴露 sag 自己的 DTO 与 `EngineManager`，从而把引擎实现细节与领域逻辑解耦，
未来替换 / 升级引擎时改动收敛在此目录。
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
