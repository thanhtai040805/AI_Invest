"""Bounded Continuum BFF schemas for Knowledge Handover grounded assistance."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

HandoverAssistTask = Literal["SOURCE_PROPOSAL", "DRAFT_GENERATION", "QUESTION_ANSWER"]
SourceKind = Literal["TASK_NOTE", "PROJECT_DOCUMENT", "WORK_REPORT", "KNOWLEDGE_OBJECT"]
OfficialSourceKind = Literal["PROJECT_DOCUMENT", "WORK_REPORT", "KNOWLEDGE_OBJECT"]
LocatorKind = Literal["CHUNK", "PAGE", "LINE", "SECTION"]

OFFICIAL_SOURCE_KINDS: frozenset[str] = frozenset(
    {"PROJECT_DOCUMENT", "WORK_REPORT", "KNOWLEDGE_OBJECT"}
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceRefPayload(StrictModel):
    kind: SourceKind
    id: str = Field(min_length=24, max_length=24, pattern=r"^[a-f0-9]{24}$")
    version: int = Field(ge=1)


class CitationLocatorPayload(StrictModel):
    kind: LocatorKind
    value: str = Field(min_length=1, max_length=200)


class CitationPayload(StrictModel):
    source: SourceRefPayload
    locator: CitationLocatorPayload


class SourceExcerptInput(StrictModel):
    locatorKind: LocatorKind
    locatorValue: str = Field(min_length=1, max_length=200)
    heading: str = Field(default="", max_length=255)
    content: str = Field(min_length=1, max_length=20000)


class HandoverAssistSourceInput(StrictModel):
    kind: SourceKind
    id: str = Field(min_length=24, max_length=24, pattern=r"^[a-f0-9]{24}$")
    version: int = Field(ge=1)
    title: str = Field(min_length=1, max_length=255)
    logicalSourceId: str | None = Field(default=None, max_length=128)
    excerpts: list[SourceExcerptInput] = Field(default_factory=list, max_length=25)


class HandoverAssistRequest(StrictModel):
    task: HandoverAssistTask
    query: str | None = Field(default=None, max_length=4000)
    maxItems: int = Field(default=10, ge=1, le=100)
    sources: list[HandoverAssistSourceInput] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def _validate_task_constraints(self) -> HandoverAssistRequest:
        if self.task == "QUESTION_ANSWER":
            if not self.query or not self.query.strip():
                raise ValueError("query is required for QUESTION_ANSWER")
        if self.task in {"SOURCE_PROPOSAL", "QUESTION_ANSWER"}:
            for src in self.sources:
                if src.kind not in OFFICIAL_SOURCE_KINDS:
                    raise ValueError(
                        f"Source kind '{src.kind}' is not permitted in official {self.task}; "
                        "Task Notes never enter official AI knowledge."
                    )
        return self


class ProposedCandidateOut(StrictModel):
    source: SourceRefPayload
    reason: str = Field(min_length=1, max_length=1000)


class GeneratedDraftItemOut(StrictModel):
    title: str = Field(min_length=1, max_length=255)
    body: str = Field(min_length=1, max_length=20000)
    requirement: str = Field(min_length=1, max_length=2000)
    sources: list[SourceRefPayload] = Field(default_factory=list, max_length=50)
    citations: list[CitationPayload] = Field(default_factory=list, max_length=100)


class GroundedAnswerSectionOut(StrictModel):
    text: str = Field(min_length=1, max_length=10000)
    citations: list[CitationPayload] = Field(min_length=1, max_length=50)


class AssistCoverageOut(StrictModel):
    requestedSources: int = Field(ge=0)
    includedSources: list[SourceRefPayload] = Field(default_factory=list)
    skippedSources: list[SourceRefPayload] = Field(default_factory=list)
    truncatedSources: list[SourceRefPayload] = Field(default_factory=list)
    totalEvidenceUnits: int = Field(ge=0)
    truncated: bool = False


class HandoverAssistUsageOut(StrictModel):
    inputTokens: int = Field(default=0, ge=0)
    cachedTokens: int = Field(default=0, ge=0)
    outputTokens: int = Field(default=0, ge=0)
    totalTokens: int = Field(default=0, ge=0)
    calls: int = Field(default=0, ge=0)


class HandoverAssistResponse(StrictModel):
    task: HandoverAssistTask
    candidates: list[ProposedCandidateOut] = Field(default_factory=list)
    items: list[GeneratedDraftItemOut] = Field(default_factory=list)
    answerStatus: Literal["ANSWERED", "INSUFFICIENT_EVIDENCE"] | None = None
    message: str | None = Field(default=None, max_length=1000)
    sections: list[GroundedAnswerSectionOut] = Field(default_factory=list)
    coverage: AssistCoverageOut | None = None
    usage: HandoverAssistUsageOut | None = None

