"""Pydantic schemas for SAG Knowledge Routing RAG API (Phase 0 / Phase 1).

Complies with Section 9.1 and 9.2 of phase-0-contracts-and-foundations.md.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DocumentObjectIngestRequest(BaseModel):
    source_url: str = Field(..., min_length=8, max_length=4096)
    original_filename: str = Field(..., min_length=1, max_length=512)
    content_type: str = Field(default="application/octet-stream", max_length=256)
    expected_sha256: str = Field(..., pattern=r"^[a-fA-F0-9]{64}$")
    expected_size_bytes: int = Field(..., ge=1)
    logical_source_id: str | None = Field(default=None, max_length=512)
    source_published_at: datetime | None = None


class DocumentUploadResponse(BaseModel):
    document_id: str
    version_no: int
    version_id: str
    run_id: str
    file_hash: str
    status: str
    search_status: str
    knowledge_status: str
    is_duplicate: bool


class StageProgressItem(BaseModel):
    status: str
    duration_ms: float = 0.0


class DocumentVersionStatusResponse(BaseModel):
    document_id: str
    version_no: int
    status: str
    search_status: str
    knowledge_status: str
    search_ready: bool
    knowledge_ready: bool
    current_stage: str
    stage_progress: dict[str, StageProgressItem] = Field(default_factory=dict)
    error: dict[str, Any] | None = None
    progress: int = 0
    document_status: str | None = None
    parser_provider: str | None = None
    parser_status: str | None = None
    chunk_count: int = 0
    event_count: int = 0
    token_usage: int = 0


class ProjectDocumentVersionRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=64)
    version_no: int = Field(ge=1)


class ProjectDocumentReadinessBatchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[ProjectDocumentVersionRef] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def reject_duplicate_versions(self) -> ProjectDocumentReadinessBatchRequest:
        refs = [(item.document_id, item.version_no) for item in self.documents]
        if len(refs) != len(set(refs)):
            raise ValueError("Duplicate document version references are not allowed")
        return self


class ProjectDocumentReadinessItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1, max_length=64)
    version_no: int = Field(ge=1)
    found: bool
    status: str = Field(max_length=32)
    search_status: str = Field(max_length=32)
    search_ready: bool
    document_status: str | None = Field(default=None, max_length=32)
    chunk_count: int = Field(ge=0)


class ProjectDocumentReadinessBatchResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verified_at: datetime
    items: list[ProjectDocumentReadinessItem] = Field(max_length=100)


class DocumentExtractedChunkItem(BaseModel):
    chunk_id: str
    ordinal: int
    section_path: str = ""
    block_type: str = "paragraph"
    page_from: int = 1
    page_to: int = 1
    token_count: int = 0
    content: str


class DocumentVersionChunksResponse(BaseModel):
    document_id: str
    version_no: int
    source_stage: Literal["SEARCH_UNITS", "CANONICAL_BLOCKS", "EMPTY"] = "EMPTY"
    total_chunks: int = 0
    total_tokens: int = 0
    items: list[DocumentExtractedChunkItem] = Field(default_factory=list)


class DocumentRevokeRequest(BaseModel):
    logical_source_id: str | None = None
    document_id: str | None = None
    reason: str | None = None


class DocumentRevokeResponse(BaseModel):
    status: Literal["REVOKED", "ALREADY_REVOKED", "NOT_FOUND"]
    document_id: str | None = None


class DocumentReprocessRequest(BaseModel):
    source_url: str | None = Field(default=None, min_length=8, max_length=4096)
    original_filename: str | None = Field(default=None, min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=256)
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")
    expected_size_bytes: int | None = Field(default=None, ge=1)
