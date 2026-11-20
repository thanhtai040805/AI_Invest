"""Dify 外部知识库检索协议。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DifyRetrievalSetting(BaseModel):
    top_k: int | None = Field(default=None, ge=1)
    score_threshold: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description=(
            "Minimum normalized RRF rank score returned by SAG; this threshold "
            "does not represent a cosine-similarity or probability cutoff. A top-ranked "
            "candidate found by only one of two active retrievers can score 0.5."
        ),
    )


class DifyRetrievalRequest(BaseModel):
    knowledge_id: str = Field(default="", max_length=64)
    query: str = Field(default="", max_length=4000)
    retrieval_setting: DifyRetrievalSetting = Field(
        default_factory=DifyRetrievalSetting
    )
    metadata_condition: dict[str, Any] | None = None


class DifyRetrievalRecord(BaseModel):
    content: str
    title: str
    score: float = Field(
        description=(
            "Normalized reciprocal-rank fusion (RRF) score in [0, 1], "
            "not cosine similarity or a relevance probability. When both retrievers "
            "are active, a top-ranked candidate found by only one can score 0.5."
        )
    )
    metadata: dict[str, Any]


class DifyRetrievalResponse(BaseModel):
    records: list[DifyRetrievalRecord] = Field(default_factory=list)
