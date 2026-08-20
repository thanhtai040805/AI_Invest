"""DTO kết quả engine phía sag —— tách rời khỏi cấu trúc trả về của zleap-sag."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class RetrievedSection(BaseModel):
    """Một đoạn được truy xuất (dùng cho ngữ cảnh hỏi đáp và trích dẫn)."""

    chunk_id: str | None = None
    heading: str = ""
    content: str = ""
    score: float = 0.0
    rank: int = 0
    source_id: str | None = None
    source_config_id: str | None = None

    @classmethod
    def from_section(cls, s: dict[str, Any]) -> RetrievedSection:
        return cls(
            chunk_id=s.get("chunk_id"),
            heading=(s.get("heading") or "").strip(),
            content=(s.get("content") or "").strip(),
            score=float(s.get("score") or 0.0),
            rank=int(s.get("rank") or 0),
            source_id=s.get("source_id"),
            source_config_id=s.get("source_config_id"),
        )


class SearchOutcome(BaseModel):
    """Kết quả tìm kiếm được gộp lại."""

    query: str
    sections: list[RetrievedSection]
    stats: dict[str, Any] = {}

    @classmethod
    def from_result(cls, result: Any) -> SearchOutcome:
        raw_sections = getattr(result, "sections", None) or []
        query = getattr(result, "query", "") or ""
        if not isinstance(query, str):
            query = str(query)
        return cls(
            query=query,
            sections=[RetrievedSection.from_section(s) for s in raw_sections],
            stats=getattr(result, "stats", None) or {},
        )


class ChunkInfo(BaseModel):
    """Nội dung gốc của một chunk (dùng truy nguồn trích dẫn)."""

    chunk_id: str
    heading: str = ""
    content: str = ""
    rank: int = 0


class EntityInfo(BaseModel):
    """Một thực thể gộp từ đồ thị sự kiện–thực thể (dùng cho phân tích / sách→nhân vật)."""

    id: str
    name: str
    type: str
    description: str = ""
    heat: int = 0  # Số sự kiện liên quan (chỉ số thay thế của tần suất × mức trung tâm)


class GraphEventInfo(BaseModel):
    """Nút sự kiện trong đồ thị nguồn thông tin."""

    id: str
    source_id: str
    title: str
    source_config_id: str = ""
    summary: str = ""
    content: str = ""
    category: str = ""
    rank: int = 0
    parent_id: str | None = None
    chunk_id: str | None = None
    start_time: datetime | None = None
    score: float = 0.0


class GraphAssociationInfo(BaseModel):
    """Quan hệ được trích xuất thực giữa sự kiện và thực thể."""

    event_id: str
    entity_id: str
    weight: float = 1.0
    description: str = ""


class SourceGraphInfo(BaseModel):
    """Lát cắt đồ thị phía engine; ánh xạ tài liệu Web do lớp API bổ sung."""

    events: list[GraphEventInfo] = []
    entities: list[EntityInfo] = []
    associations: list[GraphAssociationInfo] = []
    total_entities: int = 0


class UniverseTimeBucketInfo(BaseModel):
    """One bounded bucket in the aggregate universe timeline."""

    start: datetime
    end: datetime
    count: int = 0


class UniverseSourceStatsInfo(BaseModel):
    """Aggregate-only source statistics used to draw a virtual partition."""

    event_count: int = 0
    entity_count: int = 0
    relation_count: int = 0
    category_counts: dict[str, int] = Field(default_factory=dict)
    time_buckets: list[UniverseTimeBucketInfo] = Field(default_factory=list)


class UniverseExpansionInfo(BaseModel):
    """A bounded expansion page whose event nodes carry their factual bundles."""

    anchor: dict[str, Any]
    neighbors: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    returned: int = Field(ge=0)
    has_more: bool = False
    next_cursor: str | None = Field(default=None, max_length=2048)
    snapshot_id: str = Field(min_length=1, max_length=2048)
    as_of: datetime


class UniverseTimelineBundleInfo(BaseModel):
    """One event and its bounded first-screen factual neighborhood."""

    bundle_id: str = Field(min_length=1)
    # Position in the source's canonical exploration order within this
    # snapshot: 0 is the newest end, total_events - 1 the oldest.
    ordinal: int = Field(ge=0)
    event: dict[str, Any]
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    relations: list[dict[str, Any]] = Field(default_factory=list)
    neighbor_total: int = Field(default=0, ge=0)
    neighbor_returned: int = Field(default=0, ge=0)
    complete: bool = False
    neighbor_next_cursor: str | None = Field(default=None, max_length=2048)
    cursor_before: str | None = Field(default=None, max_length=2048)
    cursor_after: str | None = Field(default=None, max_length=2048)


class UniverseTimelineInfo(BaseModel):
    """A bounded event-time page plus a small factual entity neighborhood."""

    bundles: list[UniverseTimelineBundleInfo] = Field(default_factory=list)
    # Snapshot-stable count of the source's live events; the denominator that
    # gives every ordinal (and the client's counting axis) its length.
    total_events: int = Field(default=0, ge=0)
    snapshot_id: str = Field(min_length=1, max_length=2048)
    direction: Literal["older", "newer"] = "older"
    has_newer: bool = False
    newer_cursor: str | None = Field(default=None, max_length=2048)
    has_older: bool = False
    older_cursor: str | None = Field(default=None, max_length=2048)
    has_more: bool = False
    next_cursor: str | None = Field(default=None, max_length=2048)
    as_of: datetime


class ProcessOutcome(BaseModel):
    """Kết quả xử lý tài liệu (ingest + extract)."""

    source_id: str | None = None
    chunk_count: int = 0
    event_count: int = 0
    chunk_ids: list[str] = []
    event_ids: list[str] = []
    processed_chunk_ids: list[str] = []
    eventless_chunk_ids: list[str] = []
    token_usage: int = 0
    paused: bool = False

    @classmethod
    def from_results(cls, ingest: Any, extract: Any) -> ProcessOutcome:
        return cls(
            source_id=getattr(ingest, "source_id", None),
            chunk_count=int(getattr(ingest, "chunk_count", 0) or 0),
            event_count=int(getattr(extract, "event_count", 0) or 0),
            chunk_ids=list(getattr(ingest, "chunk_ids", []) or []),
            event_ids=list(getattr(extract, "event_ids", []) or []),
            eventless_chunk_ids=list(
                getattr(extract, "eventless_chunk_ids", []) or []
            ),
        )


class ProcessCheckpoint(BaseModel):
    """Điểm dừng xử lý tài liệu có thể lưu vào Job.payload."""

    source_id: str | None = None
    chunk_ids: list[str] = []
    processed_chunk_ids: list[str] = []
    event_count: int = 0
    event_ids: list[str] = []
    eventless_chunk_ids: list[str] = []
    token_usage: int = 0

    @classmethod
    def from_payload(cls, payload: dict | None) -> ProcessCheckpoint:
        value = (payload or {}).get("process_checkpoint")
        return cls.model_validate(value) if isinstance(value, dict) else cls()

    def merge_payload(self, payload: dict | None) -> dict:
        return {
            **(payload or {}),
            "process_checkpoint": self.model_dump(),
        }


def estimate_llm_cost(token_usage: int, model_name: str = "") -> dict[str, Any]:
    """Tính toán chi phí ước tính dựa trên số lượng token và đơn giá cụ thể của từng mô hình."""
    model_lower = (model_name or "").lower()

    if "qwen2.5" in model_lower or "qwen/qwen2.5-7b" in model_lower:
        # Qwen 2.5 7B Instruct: $0.05 / 1M tokens (Input & Output)
        price_per_1m = 0.05
    elif "deepseek-v4" in model_lower or "deepseek" in model_lower:
        # DeepSeek-V4-Flash-0731: Input Miss $0.14, Cached $0.028, Output $0.28 -> Trung bình cached blend ~$0.08 / 1M tokens
        price_per_1m = 0.08
    elif "embedding" in model_lower:
        # Qwen3-Embedding-4B / 8B: $0.02 / 1M tokens
        price_per_1m = 0.02
    elif "r1" in model_lower:
        price_per_1m = 0.55
    elif "gpt-4" in model_lower:
        price_per_1m = 2.50
    else:
        price_per_1m = 0.05  # Mặc định theo Qwen 2.5 7B

    cost_usd = (token_usage / 1_000_000.0) * price_per_1m
    cost_vnd = cost_usd * 25_400.0

    return {
        "tokens": token_usage,
        "cost_usd": round(cost_usd, 6),
        "cost_vnd": round(cost_vnd, 2),
        "formatted_usd": f"${cost_usd:.4f}",
        "formatted_vnd": f"{cost_vnd:,.0f} VNĐ",
    }


