from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from sag_api.enums import SearchStrategy
from sag_api.schemas.insight import EntityOut, GraphRelationOut


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    strategy: SearchStrategy | None = None
    top_k: int | None = Field(default=None, ge=1, le=50)


class GlobalSearchRequest(BaseModel):
    """Tìm kiếm cấp workspace: mặc định phân vùng ứng viên giới hạn, có thể truyền source_ids để thu hẹp (ví dụ @một nguồn nào đó)."""

    query: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] | None = Field(default=None, max_length=256)
    top_k: int | None = Field(default=None, ge=1, le=50)
    strategy: SearchStrategy | None = None
    save_exploration: bool = False


class SectionOut(BaseModel):
    chunk_id: str | None
    heading: str
    content: str
    score: float
    rank: int
    source_id: str | None
    source_name: str | None = None


class SearchEventOut(BaseModel):
    id: str
    document_id: str | None = None
    source_id: str | None = None
    source_name: str | None = None
    title: str
    summary: str = ""
    category: str = ""
    rank: int = 0
    parent_id: str | None = None
    chunk_id: str | None = None
    start_time: datetime | None = None
    score: float = 0.0


class SearchSourceHitOut(BaseModel):
    source_id: str
    source_name: str | None = None
    event_hits: int = 0
    max_score: float = 0.0
    latest_event_time: datetime | None = None


class SearchResponse(BaseModel):
    query: str
    sections: list[SectionOut]
    events: list[SearchEventOut] = Field(default_factory=list)
    entities: list[EntityOut] = Field(default_factory=list)
    relations: list[GraphRelationOut] = Field(default_factory=list)
    source_hits: list[SearchSourceHitOut] = Field(default_factory=list)
    summary: str = ""
    exploration_id: str | None = None
    stats: dict[str, Any]
