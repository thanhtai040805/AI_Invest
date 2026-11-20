from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from sag_api.enums import SearchStrategy
from sag_api.schemas.insight import EntityOut, GraphRelationOut


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    strategy: SearchStrategy | None = None
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Optional explicit result limit; defaults to 7-axis RetrievalPlan cardinality (5 for standard passage Q&A, up to 50 for document listing).",
    )


class GlobalSearchRequest(BaseModel):
    """工作空间级搜索：默认有界候选分区，可传 source_ids 收窄（如 @某信源）。"""

    query: str = Field(min_length=1, max_length=4000)
    source_ids: list[str] | None = Field(default=None, max_length=256)
    top_k: int | None = Field(
        default=None,
        ge=1,
        description="Optional explicit result limit; defaults to 7-axis RetrievalPlan cardinality (5 for standard passage Q&A, up to 50 for document listing).",
    )
    strategy: SearchStrategy | None = None
    retrieval_mode: (
        Literal[
            "EXACT",
            "LOCAL_FACTUAL",
            "ENTITY_RELATIONAL",
            "TEMPORAL",
            "GLOBAL_TOPIC",
            "MULTI_HOP",
        ]
        | None
    ) = None
    save_exploration: bool = False


class SectionOut(BaseModel):
    chunk_id: str | None
    search_unit_id: str | None = None
    block_from_id: str | None = None
    block_to_id: str | None = None
    section_path: str | None = None
    document_id: str | None = None
    document_version_id: str | None = None
    document_name: str | None = None
    version_no: int | None = None
    page_from: int | None = None
    page_to: int | None = None
    anchor: str | None = None
    heading: str
    content: str
    score: float = Field(
        description=(
            "Normalized reciprocal-rank fusion (RRF) score in [0, 1]. "
            "When dense and lexical retrieval are both active, a top-ranked candidate "
            "returned by only one retriever can score 0.5. This is not cosine "
            "similarity or a relevance probability."
        )
    )
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


class SearchCitationOut(BaseModel):
    kind: Literal["internal"] = "internal"
    n: int
    document_id: str
    document_version_id: str
    document_name: str = ""
    version_no: int | None = None
    chunk_id: str
    search_unit_id: str | None = None
    block_from_id: str | None = None
    block_to_id: str | None = None
    section_path: str | None = None
    page_from: int
    page_to: int
    anchor: str
    heading: str = ""
    snippet: str = ""
    score: float = 0.0
    source_id: str
    source_name: str | None = None


class SearchResponse(BaseModel):
    query: str
    sections: list[SectionOut]
    events: list[SearchEventOut] = Field(default_factory=list)
    entities: list[EntityOut] = Field(default_factory=list)
    relations: list[GraphRelationOut] = Field(default_factory=list)
    source_hits: list[SearchSourceHitOut] = Field(default_factory=list)
    summary: str = ""
    citations: list[SearchCitationOut] = Field(default_factory=list)
    answer_status: Literal["pending", "answered", "no_answer", "skipped"] = "skipped"
    no_answer_reason: Literal["empty_evidence", "weak_evidence", "context_budget"] | None = None
    exploration_id: str | None = None
    stats: dict[str, Any]


class EvalCompareRequest(BaseModel):
    """/search/eval-compare 请求体:同一 query,多策略并排跑。"""

    query: str = Field(min_length=1, max_length=4000)
    strategies: list[SearchStrategy] = Field(min_length=2, max_length=4)
    source_ids: list[str] | None = Field(default=None, max_length=256)
    top_k: int | None = Field(default=None, ge=1, description="SAG retrieval currently uses fixed Top-5.")
    judge: bool = True  # 前端可临时关掉;是否真的调 LLM 还看后台 settings 开关


class EvalStrategyResultOut(BaseModel):
    strategy: SearchStrategy
    sections: list[SectionOut]
    stats: dict[str, Any]
    error: str | None = None


class EvalJudgeOut(BaseModel):
    a_strategy: SearchStrategy
    b_strategy: SearchStrategy
    winner: str  # "A" | "B" | "tie"
    reason: str


class EvalCompareResponse(BaseModel):
    query: str
    results: list[EvalStrategyResultOut]
    judges: list[EvalJudgeOut] = Field(default_factory=list)
    judge_enabled: bool
    judge_reason: str | None = None  # 例:LLM 未配置 / 后台 flag 关闭
