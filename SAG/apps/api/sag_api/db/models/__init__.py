"""Nhập gộp các model ORM — đảm bảo Base.metadata đăng ký toàn bộ bảng."""

from sag_api.db.models.agent import Agent, AgentBinding, Message, Thread
from sag_api.db.models.document import (
    AssessmentRun,
    Document,
    DocumentAsset,
    DocumentFacet,
    DocumentEntity,
    DocumentEvidenceChunk,
    DocumentFact,
    DocumentRelation,
    DocumentTreeNode,
    EmbeddingChunk,
    Entity,
    EntityAlias,
    EntityMention,
    EvidenceSpan,
    Fact,
    Issuer,
    Observation,
    ProcessingRun,
    Relation,
    ReviewQueueItem,
)
from sag_api.db.models.job import Job
from sag_api.db.models.setting import Setting
from sag_api.db.models.source import Source
from sag_api.db.models.universe import (
    ExplorationSession,
    ExplorationStep,
    UniverseDirtySource,
    UniverseOverview,
    UniversePartition,
)
from sag_api.db.models.user import User

__all__ = [
    "Agent",
    "AgentBinding",
    "AssessmentRun",
    "Document",
    "DocumentAsset",
    "DocumentFacet",
    "DocumentEntity",
    "DocumentEvidenceChunk",
    "DocumentFact",
    "DocumentRelation",
    "DocumentTreeNode",
    "EmbeddingChunk",
    "Entity",
    "EntityAlias",
    "EntityMention",
    "EvidenceSpan",
    "Fact",
    "Issuer",
    "Job",
    "Message",
    "Observation",
    "ProcessingRun",
    "Relation",
    "ReviewQueueItem",
    "Setting",
    "Source",
    "Thread",
    "User",
    "ExplorationSession",
    "ExplorationStep",
    "UniverseDirtySource",
    "UniverseOverview",
    "UniversePartition",
]
