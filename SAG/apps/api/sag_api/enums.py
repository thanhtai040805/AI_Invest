"""Các enum dùng chung giữa các tầng (model / schema / service đều import được, không tác dụng phụ)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

SearchStrategy = Literal["vector", "multi"]
SEARCH_STRATEGIES = frozenset({"vector", "multi"})


def normalize_search_strategy(value: str) -> str:
    """Chuyển chiến lược tìm kiếm atomic đã ngừng sang tìm kiếm chính xác; giá trị khác giao cho caller kiểm tra."""
    return "multi" if value == "atomic" else value


class SourceType(StrEnum):
    DOCUMENT = "document"
    WEB = "web"
    MESSAGE = "message"
    AUDIO = "audio"


class ConnectorKind(StrEnum):
    FILE_UPLOAD = "file_upload"
    WEB = "web"
    # Dự trữ: NOTION = "notion"; S3 = "s3"; CONFLUENCE = "confluence"; ...


# Connector → loại nguồn mặc định
CONNECTOR_SOURCE_TYPE = {
    ConnectorKind.FILE_UPLOAD: SourceType.DOCUMENT,
    ConnectorKind.WEB: SourceType.WEB,
}


class SourceStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    ERROR = "error"


class DocumentStatus(StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    OCR_READY = "OCR_READY"
    READY = "READY"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    # Legacy values kept only so old rows can still hydrate while v2 cuts over.
    PENDING = "pending"
    LOADING = "loading"
    EXTRACTING = "extracting"
    PAUSED = "paused"


class DocumentRole(StrEnum):
    ANNUAL_BACKBONE = "ANNUAL_BACKBONE"
    LATEST_QUARTER = "LATEST_QUARTER"
    GOVERNANCE_REPORT = "GOVERNANCE_REPORT"


class ProcessingStageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    EXTRACTION_INCOMPLETE = "EXTRACTION_INCOMPLETE"
    FAILED = "FAILED"
    SKIPPED_DUPLICATE = "SKIPPED_DUPLICATE"


class EntityType(StrEnum):
    ISSUER = "issuer"
    SUBSIDIARY = "subsidiary"
    AFFILIATE = "affiliate"
    RELATED_PARTY = "related_party"
    PERSON = "person"
    BANK = "bank"
    STATE_BODY = "state_body"
    CUSTOMER = "customer"
    SUPPLIER = "supplier"
    PROJECT = "project"
    BRAND = "brand"
    OTHER = "other"


class FactType(StrEnum):
    EQUITY = "equity"
    REVENUE = "revenue"
    PROFIT = "profit"
    CAPACITY = "capacity"
    OWNERSHIP_BALANCE = "ownership_balance"
    LOAN_BALANCE = "loan_balance"
    RECEIVABLE_BALANCE = "receivable_balance"
    PAYABLE_BALANCE = "payable_balance"
    GUARANTEE_BALANCE = "guarantee_balance"
    DIVIDEND = "dividend"
    RELATED_PARTY_EXPOSURE = "related_party_exposure"
    OTHER = "other"


class RelationType(StrEnum):
    OWNS = "owns"
    INVESTS_IN = "invests_in"
    LENDS_TO = "lends_to"
    CREDITOR_OF = "creditor_of"
    GUARANTEES_FOR = "guarantees_for"
    TRANSACTS_WITH = "transacts_with"
    MANAGES = "manages"
    CONTROLS = "controls"
    AFFILIATED_WITH = "affiliated_with"
    OTHER = "other"


class AssessmentStatus(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT = "INSUFFICIENT"


class GILFlag(StrEnum):
    PASS = "PASS"
    WARNING = "WARNING"
    CATASTROPHIC = "CATASTROPHIC"
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"


class ValidationStatus(StrEnum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class JobType(StrEnum):
    PROCESS_DOCUMENT = "process_document"
    OCR_FROM_OBJECT = "ocr_from_object"
    OCR_FROM_URL = "ocr_from_url"
    SYNC_SOURCE = "sync_source"
    INDEX_UNIVERSE = "index_universe"


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"


class BindingTargetType(StrEnum):
    SOURCE = "source"
    MCP_SERVER = "mcp_server"  # Phase C: gắn MCP server làm nguồn công cụ
