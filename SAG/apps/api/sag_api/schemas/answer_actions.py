"""Schemas for AI answer feedback, project-scoped sharing, regeneration, and admin telemetry."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

FeedbackRating = Literal["LIKE", "DISLIKE"]
FeedbackReasonCategory = Literal[
    "INACCURATE",
    "OUTDATED",
    "IRRELEVANT_CITATION",
    "INCOMPLETE",
    "OTHER",
]


class FeedbackUpsertIn(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    rating: FeedbackRating
    reason_category: FeedbackReasonCategory | None = Field(
        default=None,
        validation_alias=AliasChoices("reason_category", "reasonCategory"),
    )
    reason_text: str | None = Field(
        default=None,
        validation_alias=AliasChoices("reason_text", "reasonText"),
    )

    @model_validator(mode="after")
    def validate_reason_rules(self) -> FeedbackUpsertIn:
        if self.rating == "LIKE":
            if self.reason_category is not None or self.reason_text is not None:
                raise ValueError("LIKE votes cannot include a reason category or reason text")
            return self

        if self.reason_category is None:
            raise ValueError("DISLIKE votes require a reason category")

        if self.reason_text is not None:
            if len(self.reason_text) > 1000 or not self.reason_text.strip():
                raise ValueError("Reason text must be 1 to 1000 non-whitespace characters")
        elif self.reason_category == "OTHER":
            raise ValueError("OTHER reason category requires reason text")
        return self


class FeedbackOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    message_id: str
    rating: FeedbackRating
    reason_category: FeedbackReasonCategory | None = None
    reason_text: str | None = None
    updated_at: datetime


class OwnFeedbackOut(BaseModel):
    feedback: FeedbackOut | None = None


class EmptyBodyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RegenerateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    web_enabled: bool = False
    knowledge_only: bool | None = None

    @property
    def effective_web_enabled(self) -> bool:
        if "web_enabled" in self.model_fields_set:
            return self.web_enabled
        if self.knowledge_only is not None:
            return not self.knowledge_only
        return False


class ShareOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    message_id: str
    created_at: datetime


class SharedQuestionOut(BaseModel):
    content: str


class SharedPublicAnswerOut(BaseModel):
    id: str
    content: str
    citations: list[dict[str, Any]] = Field(default_factory=list)


class SharedAnswerOut(BaseModel):
    share: ShareOut
    question: SharedQuestionOut
    answer: SharedPublicAnswerOut


class AdminFeedbackTotalsOut(BaseModel):
    likes: int = Field(ge=0)
    dislikes: int = Field(ge=0)


class AdminFeedbackRowOut(BaseModel):
    id: str
    organization_id: str
    project_id: str
    message_id: str
    rating: FeedbackRating
    reason_category: FeedbackReasonCategory | None = None
    reason_text: str | None = None
    created_at: datetime
    updated_at: datetime


class AdminFeedbackPageOut(BaseModel):
    totals: AdminFeedbackTotalsOut
    items: list[AdminFeedbackRowOut] = Field(default_factory=list)
    next_cursor: str | None = None
