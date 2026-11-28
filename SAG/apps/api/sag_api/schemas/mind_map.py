"""Strict Pydantic v2 schemas for Project-scoped AI Chat Mind Map V1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def normalize_mind_map_topic(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("topic must be a string")
    trimmed = " ".join(value.split()).strip()
    if not trimmed:
        raise ValueError("topic must not be blank or whitespace-only")
    if len(trimmed) > 200 or len(trimmed.casefold()) > 200:
        raise ValueError("topic must not exceed 200 characters after normalization")
    return trimmed


class MindMapGenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: str | None = Field(default=None)
    refresh: bool = Field(default=True)

    @field_validator("topic", mode="before")
    @classmethod
    def validate_topic(cls, value: object) -> str | None:
        return normalize_mind_map_topic(value)

    @field_validator("refresh", mode="before")
    @classmethod
    def validate_refresh(cls, value: object) -> bool:
        if not isinstance(value, bool):
            raise ValueError("refresh must be a boolean")
        return value


class MindMapCoverageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accessibleReadyDocumentCount: int = Field(ge=0)
    usedDocumentCount: int = Field(ge=0)
    omittedDocumentCount: int = Field(ge=0)
    pendingDocumentCount: int = Field(ge=0)
    truncatedDocumentCount: int = Field(ge=0)
    evidenceUnitCount: int = Field(ge=0)


class MindMapCitationOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    number: int = Field(ge=1)
    sourceId: str = Field(min_length=1, max_length=128)
    sourceName: str | None = Field(default=None, max_length=240)
    projectId: str | None = Field(default=None, max_length=128)
    documentId: str = Field(min_length=1, max_length=128)
    documentName: str | None = Field(default=None, max_length=240)
    chunkId: str | None = Field(default=None, max_length=128)
    versionNo: int | None = Field(default=None, ge=1)
    heading: str | None = Field(default=None, max_length=240)
    snippet: str | None = Field(default=None, max_length=1200)
    label: str | None = Field(default=None, max_length=300)
    pageFrom: int | None = Field(default=None, ge=1)
    pageTo: int | None = Field(default=None, ge=1)


class MindMapNodeOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    label: str = Field(min_length=1, max_length=80)
    summary: str | None = Field(default=None, max_length=320)
    citationNumbers: list[int] = Field(default_factory=list)
    children: list[MindMapNodeOut] = Field(default_factory=list, max_length=8)


MindMapNodeOut.model_rebuild()


class MindMapUsageOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputTokens: int = Field(default=0, ge=0)
    cachedTokens: int = Field(default=0, ge=0)
    outputTokens: int = Field(default=0, ge=0)
    totalTokens: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)


class MindMapOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    projectId: str = Field(min_length=1, max_length=64)
    topic: str | None = Field(default=None, max_length=200)
    status: Literal["ready", "empty", "uninitialized"]
    generationMode: Literal["llm", "outline_fallback", "none"]
    fallbackReason: Literal["LLM_UNAVAILABLE", "LLM_INVALID_OUTPUT"] | None = None
    scopeFingerprint: str = Field(min_length=16, max_length=128)
    coverage: MindMapCoverageOut
    tree: MindMapNodeOut | None = None
    citations: list[MindMapCitationOut] = Field(default_factory=list)
    generatedAt: datetime
    cached: bool = False
    hasSourceUpdates: bool = False
    usage: MindMapUsageOut | None = None
