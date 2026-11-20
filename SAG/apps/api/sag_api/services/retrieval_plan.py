"""Compositional 7-Axis RetrievalPlan IR, validator, and slot/cardinality resolver.

Replaces hardcoded top-k overrides and flat profile enumerations with an
orthogonal 7-axis specification:
1. TargetUnit      (PASSAGE, DOCUMENT, TOPIC, FACT, TURN)
2. SelectionMode   (RELEVANCE, FILTER, LOOKUP)
3. FieldPredicate  (validated metadata/scope/temporal constraints)
4. CardinalitySpec (ONE, N, ALL with API > QUERY > DEFAULT precedence)
5. OrderMode       (RELEVANCE, TIME_DESC, TIME_ASC, FIELD)
6. Decomposition   (NONE, PARALLEL, SEQUENTIAL)
7. OutputMode      (ANSWER, LIST, COUNT, SUMMARIZE, COMPARE, OPEN)
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sag_api.core.config import FIXED_SEARCH_TOP_K

RETRIEVAL_PLAN_VERSION = "qsp-v2"


class TargetUnit(StrEnum):
    PASSAGE = "PASSAGE"
    DOCUMENT = "DOCUMENT"
    TOPIC = "TOPIC"
    FACT = "FACT"
    TURN = "TURN"


class SelectionMode(StrEnum):
    RELEVANCE = "RELEVANCE"
    FILTER = "FILTER"
    LOOKUP = "LOOKUP"


class OrderMode(StrEnum):
    RELEVANCE = "RELEVANCE"
    TIME_DESC = "TIME_DESC"
    TIME_ASC = "TIME_ASC"
    FIELD = "FIELD"


class DecompositionMode(StrEnum):
    NONE = "NONE"
    PARALLEL = "PARALLEL"
    SEQUENTIAL = "SEQUENTIAL"


class OutputMode(StrEnum):
    ANSWER = "ANSWER"
    LIST = "LIST"
    COUNT = "COUNT"
    SUMMARIZE = "SUMMARIZE"
    COMPARE = "COMPARE"
    OPEN = "OPEN"


class CardinalityMode(StrEnum):
    ONE = "ONE"
    N = "N"
    ALL = "ALL"


class CountSource(StrEnum):
    API = "API"
    QUERY = "QUERY"
    DEFAULT = "DEFAULT"


class AnswerShape(StrEnum):
    FACT_ANSWER = "fact_answer"
    MULTI_FACET_COMPARE = "multi_facet_compare"
    DOCUMENT_LIST = "document_list"
    EXHAUSTIVE_COUNT = "exhaustive_count"
    SUMMARIZE_SET = "summarize_set"
    OPEN_DOCUMENT = "open_document"


VALID_ANSWER_SHAPES: frozenset[str] = frozenset(item.value for item in AnswerShape)

FIELD_REGISTRY: dict[str, tuple[str, ...]] = {
    "document_name": ("eq", "contains", "in"),
    "source_id": ("eq", "in"),
    "content_type": ("eq", "in"),
    "time_range": ("gte", "lte", "between"),
    "temporal_mode": ("eq",),
    "node_id": ("eq", "in"),
}

# Default and maximum slot caps per (TargetUnit, OutputMode)
_CARDINALITY_BOUNDS: dict[tuple[TargetUnit, OutputMode], tuple[int, int]] = {
    (TargetUnit.PASSAGE, OutputMode.ANSWER): (FIXED_SEARCH_TOP_K, 20),
    (TargetUnit.PASSAGE, OutputMode.COMPARE): (8, 15),
    (TargetUnit.PASSAGE, OutputMode.SUMMARIZE): (8, 20),
    (TargetUnit.PASSAGE, OutputMode.LIST): (8, 25),
    (TargetUnit.PASSAGE, OutputMode.COUNT): (20, 50),
    (TargetUnit.PASSAGE, OutputMode.OPEN): (1, 5),
    (TargetUnit.DOCUMENT, OutputMode.OPEN): (1, 50),
    (TargetUnit.DOCUMENT, OutputMode.LIST): (10, 50),
    (TargetUnit.DOCUMENT, OutputMode.SUMMARIZE): (10, 50),
    (TargetUnit.DOCUMENT, OutputMode.COMPARE): (6, 20),
    (TargetUnit.DOCUMENT, OutputMode.ANSWER): (5, 25),
    (TargetUnit.DOCUMENT, OutputMode.COUNT): (50, 100),
    (TargetUnit.TOPIC, OutputMode.LIST): (10, 30),
    (TargetUnit.TOPIC, OutputMode.SUMMARIZE): (10, 30),
    (TargetUnit.TOPIC, OutputMode.ANSWER): (8, 25),
    (TargetUnit.FACT, OutputMode.ANSWER): (FIXED_SEARCH_TOP_K, 20),
    (TargetUnit.FACT, OutputMode.LIST): (10, 30),
    (TargetUnit.FACT, OutputMode.COUNT): (30, 100),
    (TargetUnit.TURN, OutputMode.ANSWER): (8, 25),
    (TargetUnit.TURN, OutputMode.LIST): (15, 50),
    (TargetUnit.TURN, OutputMode.SUMMARIZE): (20, 50),
}


@dataclass(frozen=True, slots=True)
class FieldPredicate:
    field: str
    op: str
    value: Any

    def is_supported(self) -> bool:
        allowed_ops = FIELD_REGISTRY.get(self.field)
        return allowed_ops is not None and self.op in allowed_ops

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "op": self.op,
            "value": self.value,
        }


@dataclass(frozen=True, slots=True)
class CardinalitySpec:
    mode: CardinalityMode
    requested_n: int | None
    effective_n: int
    max_cap: int
    count_source: CountSource
    cursor: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "requested_n": self.requested_n,
            "effective_n": self.effective_n,
            "max_cap": self.max_cap,
            "count_source": self.count_source.value,
            "cursor": self.cursor,
        }


@dataclass(frozen=True, slots=True)
class DecompositionSpec:
    mode: DecompositionMode
    subqueries: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "subqueries": list(self.subqueries),
        }


@dataclass(frozen=True, slots=True)
class RetrievalPlan:
    planner_version: str
    target: TargetUnit
    selection: SelectionMode
    constraints: tuple[FieldPredicate, ...]
    cardinality: CardinalitySpec
    order: OrderMode
    decomposition: DecompositionSpec
    output: OutputMode
    fallback_used: bool = False
    unsupported_reason: str | None = None

    @property
    def per_document_limit(self) -> int | None:
        """When target is DOCUMENT, return at most 1 representative section per document."""
        return 1 if self.target == TargetUnit.DOCUMENT else None

    def as_trace(self) -> dict[str, Any]:
        return {
            "planner_version": self.planner_version,
            "target": self.target.value,
            "selection": self.selection.value,
            "constraints": [item.as_dict() for item in self.constraints],
            "cardinality": self.cardinality.as_dict(),
            "order": self.order.value,
            "decomposition": self.decomposition.as_dict(),
            "output": self.output.value,
            "per_document_limit": self.per_document_limit,
            "fallback_used": self.fallback_used,
            "unsupported_reason": self.unsupported_reason,
        }


_DOC_UNIT_WORDS = (
    r"file|files|tệp|tập\s+tin|tài\s+liệu|văn\s+bản|hồ\s+sơ|báo\s+cáo|"
    r"chính\s+sách|quy\s+định|quy\s+trình|hợp\s+đồng|biên\s+bản|"
    r"document|documents|doc|docs|report|reports|policy|policies|contract|contracts|paper|papers|runbook|runbooks|spec|specs"
)
_TOPIC_UNIT_WORDS = r"chủ\s+đề|vấn\s+đề\s+chính|nhóm\s+chủ\s+đề|topic|topics|theme|themes"
_PASSAGE_UNIT_WORDS = (
    r"đoạn|mục|phần|kết\s+quả|bằng\s+chứng| trích\s+đoạn|"
    r"passage|passages|section|sections|chunk|chunks|result|results|snippet|snippets"
)

_EXPLICIT_DOC_COUNT_RE = re.compile(
    rf"(?i)(?:(?:\b(?:top|mở|liệt\s+kê|tìm|lấy|hiển\s+thị|cho\s+tôi|xem|list|show|open|find|get|give\s+me|return)\s+(?:ra\s+|cho\s+tôi\s+)?(\d{{1,3}})\s+(?:các\s+|những\s+)?(?:{_DOC_UNIT_WORDS})\b)"
    rf"|(?:\b(\d{{1,3}})\s+(?:{_DOC_UNIT_WORDS})\s+(?:liên\s+quan|gần\s+nhất|mới\s+nhất|đầu\s+tiên|quan\s+trọng|chính|khác\s+nhau|relevant|related|recent|latest|top|matching|distinct)\b)"
    rf"|(?:\b(?:mở|liệt\s+kê|tìm|lấy|xem|list|show|open|find|get)\s+(?:đến\s+|tới\s+|khoảng\s+|tối\s+đa\s+|up\s+to\s+)?(\d{{1,3}})\s+(?:{_DOC_UNIT_WORDS})\b))"
)
_EXPLICIT_TOPIC_COUNT_RE = re.compile(
    rf"(?i)\b(?:top|liệt\s+kê|tìm|lấy|cho\s+biết|list|show|find|get|identify)?\s*(\d{{1,3}})\s+(?:{_TOPIC_UNIT_WORDS})\b"
)
_EXPLICIT_PASSAGE_COUNT_RE = re.compile(
    rf"(?i)\b(?:top|liệt\s+kê|tìm|lấy|hiển\s+thị|list|show|find|get|return)\s+(\d{{1,3}})\s+(?:{_PASSAGE_UNIT_WORDS})\b"
)
_DOC_COUNT_QUESTION_RE = re.compile(
    rf"(?i)(?:\b(?:có\s+bao\s+nhiêu|tổng\s+số|đếm\s+số(?:\s+lượng)?|số\s+lượng)\s+(?:các\s+|những\s+)?(?:{_DOC_UNIT_WORDS})\b"
    rf"|\bhow\s+many\s+(?:[a-z_-]+\s+){{0,2}}(?:{_DOC_UNIT_WORDS})\b)"
)
_DOC_ALL_RE = re.compile(
    rf"(?i)(?:\b(?:tất\s+cả|toàn\s+bộ|mọi)\s+(?:các\s+|những\s+)?(?:{_DOC_UNIT_WORDS})\b"
    rf"|\b(?:all|every)\s+(?:the\s+)?(?:[a-z_-]+\s+){{0,2}}(?:{_DOC_UNIT_WORDS})\b)"
)
_DOC_LIST_RE = re.compile(
    rf"(?i)(?:\b(?:liệt\s+kê|danh\s+sách|kể\s+tên|cho\s+biết\s+các)\b.*\b(?:{_DOC_UNIT_WORDS})\b"
    rf"|\b(?:những|các)\s+(?:{_DOC_UNIT_WORDS})(?:\s+[^\W\d_][\w-]*){{0,3}}\s+nào\b"
    rf"|(?<!theo\s)(?<!trong\s)\b(?:hiện\s+có|gồm(?:\s+có)?|bao\s+gồm|có)\s+(?:những\s+|các\s+)?(?:{_DOC_UNIT_WORDS})(?:\s+[^\W\d_][\w-]*){{0,3}}\s+nào\b"
    rf"|\b(?:liệt\s+kê|danh\s+sách|list|show\s+all|what\s+are\s+the|which)\s+(?:các\s+|những\s+|the\s+)?(?:[^\W\d_][\w-]*\s+){{0,2}}(?:{_DOC_UNIT_WORDS})\b)",
    re.UNICODE,
)
_DOC_OPEN_RE = re.compile(
    rf"(?i)^\s*(?:hãy\s+|vui\s+lòng\s+|please\s+)?(?:mở|xem\s+nội\s+dung|hiển\s+thị\s+toàn\s+bộ|open|view)\s+(?:{_DOC_UNIT_WORDS})\b"
)
_DOC_SUMMARIZE_RE = re.compile(
    rf"(?i)\b(?:tóm\s+tắt|tổng\s+hợp|summarize|summary\s+of)\s+(?:tất\s+cả\s+|toàn\s+bộ\s+|mọi\s+|all\s+|every\s+)?(?:các\s+|những\s+|the\s+)?(?:{_DOC_UNIT_WORDS})\b"
)
_COMPARE_RE = re.compile(
    r"(?i)\b(?:so\s+sánh|khác\s+nhau\s+giữa|khác\s+gì\s+giữa|điểm\s+khác\s+biệt\s+giữa|compare|comparison\s+between|difference\s+between|differences\s+between|versus|vs\.?)\b"
)
_TIME_DESC_RE = re.compile(
    r"(?i)\b(?:mới\s+nhất|gần\s+đây\s+nhất|hiện\s+hành|latest|newest|most\s+recent)\b"
)
_TIME_ASC_RE = re.compile(
    r"(?i)\b(?:cũ\s+nhất|đầu\s+tiên|theo\s+thứ\s+tự\s+thời\s+gian|oldest|earliest|chronological)\b"
)


@dataclass(frozen=True, slots=True)
class QueryShapeSignals:
    answer_shape: AnswerShape
    target: TargetUnit
    output: OutputMode
    explicit_count: int | None
    cardinality_mode: CardinalityMode
    order: OrderMode


def extract_query_cardinality_and_shape(query: str) -> QueryShapeSignals:
    """Deterministically extract explicit count and target/output shape from a query."""
    cleaned = unicodedata.normalize("NFC", query or "").strip()
    if not cleaned:
        return QueryShapeSignals(
            answer_shape=AnswerShape.FACT_ANSWER,
            target=TargetUnit.PASSAGE,
            output=OutputMode.ANSWER,
            explicit_count=None,
            cardinality_mode=CardinalityMode.N,
            order=OrderMode.RELEVANCE,
        )

    order = OrderMode.RELEVANCE
    if _TIME_DESC_RE.search(cleaned):
        order = OrderMode.TIME_DESC
    elif _TIME_ASC_RE.search(cleaned):
        order = OrderMode.TIME_ASC

    # 1. Explicit document count (e.g., "liệt kê 20 file liên quan", "mở 20 file", "top 15 documents")
    doc_count_match = _EXPLICIT_DOC_COUNT_RE.search(cleaned)
    if doc_count_match:
        raw_num = next((g for g in doc_count_match.groups() if g is not None), None)
        count_val = int(raw_num) if raw_num is not None else None
        is_open = bool(re.search(r"(?i)\b(?:mở|open)\b", cleaned))
        if count_val == 1 and is_open:
            return QueryShapeSignals(
                answer_shape=AnswerShape.OPEN_DOCUMENT,
                target=TargetUnit.DOCUMENT,
                output=OutputMode.OPEN,
                explicit_count=1,
                cardinality_mode=CardinalityMode.ONE,
                order=order,
            )
        return QueryShapeSignals(
            answer_shape=AnswerShape.DOCUMENT_LIST,
            target=TargetUnit.DOCUMENT,
            output=OutputMode.OPEN if is_open else OutputMode.LIST,
            explicit_count=count_val,
            cardinality_mode=CardinalityMode.N,
            order=order,
        )

    # 2. Explicit topic count
    topic_count_match = _EXPLICIT_TOPIC_COUNT_RE.search(cleaned)
    if topic_count_match:
        count_val = int(topic_count_match.group(1))
        return QueryShapeSignals(
            answer_shape=AnswerShape.SUMMARIZE_SET,
            target=TargetUnit.TOPIC,
            output=OutputMode.LIST,
            explicit_count=count_val,
            cardinality_mode=CardinalityMode.N,
            order=order,
        )

    # 3. Explicit passage/result count
    passage_count_match = _EXPLICIT_PASSAGE_COUNT_RE.search(cleaned)
    if passage_count_match:
        count_val = int(passage_count_match.group(1))
        return QueryShapeSignals(
            answer_shape=AnswerShape.FACT_ANSWER,
            target=TargetUnit.PASSAGE,
            output=OutputMode.LIST if count_val > FIXED_SEARCH_TOP_K else OutputMode.ANSWER,
            explicit_count=count_val,
            cardinality_mode=CardinalityMode.N,
            order=order,
        )

    # 4. Document count question ("có bao nhiêu file/tài liệu", "how many documents")
    if _DOC_COUNT_QUESTION_RE.search(cleaned):
        return QueryShapeSignals(
            answer_shape=AnswerShape.EXHAUSTIVE_COUNT,
            target=TargetUnit.DOCUMENT,
            output=OutputMode.COUNT,
            explicit_count=None,
            cardinality_mode=CardinalityMode.ALL,
            order=order,
        )

    # 5. Document summarize / all documents
    if _DOC_SUMMARIZE_RE.search(cleaned):
        is_all = bool(_DOC_ALL_RE.search(cleaned))
        return QueryShapeSignals(
            answer_shape=AnswerShape.SUMMARIZE_SET,
            target=TargetUnit.DOCUMENT,
            output=OutputMode.SUMMARIZE,
            explicit_count=None,
            cardinality_mode=CardinalityMode.ALL if is_all else CardinalityMode.N,
            order=order,
        )

    # 6. Document list ("liệt kê các tài liệu/file", "tất cả các file", "which documents")
    if _DOC_ALL_RE.search(cleaned) or _DOC_LIST_RE.search(cleaned):
        is_all = bool(_DOC_ALL_RE.search(cleaned))
        return QueryShapeSignals(
            answer_shape=AnswerShape.DOCUMENT_LIST,
            target=TargetUnit.DOCUMENT,
            output=OutputMode.LIST,
            explicit_count=None,
            cardinality_mode=CardinalityMode.ALL if is_all else CardinalityMode.N,
            order=order,
        )

    # 7. Open a single document ("mở tài liệu X", "open file Y")
    if _DOC_OPEN_RE.search(cleaned):
        return QueryShapeSignals(
            answer_shape=AnswerShape.OPEN_DOCUMENT,
            target=TargetUnit.DOCUMENT,
            output=OutputMode.OPEN,
            explicit_count=1,
            cardinality_mode=CardinalityMode.ONE,
            order=order,
        )

    # 8. Multi-facet / multi-entity comparison
    if _COMPARE_RE.search(cleaned):
        return QueryShapeSignals(
            answer_shape=AnswerShape.MULTI_FACET_COMPARE,
            target=TargetUnit.PASSAGE,
            output=OutputMode.COMPARE,
            explicit_count=None,
            cardinality_mode=CardinalityMode.N,
            order=order,
        )

    return QueryShapeSignals(
        answer_shape=AnswerShape.FACT_ANSWER,
        target=TargetUnit.PASSAGE,
        output=OutputMode.ANSWER,
        explicit_count=None,
        cardinality_mode=CardinalityMode.N,
        order=order,
    )


def _shape_to_target_output(
    answer_shape: str | None,
    signals: QueryShapeSignals,
) -> tuple[TargetUnit, OutputMode, CardinalityMode]:
    """Resolve (TargetUnit, OutputMode, CardinalityMode) combining deterministic signals and router answer_shape."""
    # Explicit user counts or strong structural document cues take precedence
    if signals.explicit_count is not None or signals.answer_shape != AnswerShape.FACT_ANSWER:
        return signals.target, signals.output, signals.cardinality_mode

    normalized = (answer_shape or "").strip().lower()
    if normalized == AnswerShape.DOCUMENT_LIST.value:
        return TargetUnit.DOCUMENT, OutputMode.LIST, CardinalityMode.N
    if normalized == AnswerShape.EXHAUSTIVE_COUNT.value:
        return TargetUnit.DOCUMENT, OutputMode.COUNT, CardinalityMode.ALL
    if normalized == AnswerShape.SUMMARIZE_SET.value:
        return TargetUnit.DOCUMENT, OutputMode.SUMMARIZE, CardinalityMode.N
    if normalized == AnswerShape.OPEN_DOCUMENT.value:
        return TargetUnit.DOCUMENT, OutputMode.OPEN, CardinalityMode.ONE
    if normalized == AnswerShape.MULTI_FACET_COMPARE.value:
        return TargetUnit.PASSAGE, OutputMode.COMPARE, CardinalityMode.N
    return TargetUnit.PASSAGE, OutputMode.ANSWER, CardinalityMode.N


def build_retrieval_plan(
    query: str,
    *,
    requested_top_k: int | None = None,
    answer_shape: str | None = None,
    strategy_mode: str | None = None,
    clauses: tuple[str, ...] = (),
    multi_hop: bool = False,
    exact_lookup: bool = False,
    constraints: tuple[FieldPredicate, ...] = (),
    cursor: str | None = None,
) -> RetrievalPlan:
    """Compile query + routing signals into a validated 7-axis RetrievalPlan."""
    query = unicodedata.normalize("NFC", query or "")
    signals = extract_query_cardinality_and_shape(query)
    target, output, card_mode = _shape_to_target_output(answer_shape, signals)

    if strategy_mode == "GLOBAL_TOPIC" and target == TargetUnit.PASSAGE and output == OutputMode.ANSWER:
        target = TargetUnit.TOPIC
        output = OutputMode.SUMMARIZE

    # Validate constraints against FIELD_REGISTRY
    valid_constraints: list[FieldPredicate] = []
    fallback_used = False
    unsupported_reason: str | None = None
    for pred in constraints:
        if pred.is_supported():
            valid_constraints.append(pred)
        else:
            fallback_used = True
            unsupported_reason = f"unsupported_predicate:{pred.field}:{pred.op}"

    # Determine SelectionMode
    if exact_lookup or strategy_mode == "EXACT":
        selection = SelectionMode.LOOKUP
    elif valid_constraints and output in {OutputMode.LIST, OutputMode.COUNT} and not query.strip():
        selection = SelectionMode.FILTER
    else:
        selection = SelectionMode.RELEVANCE

    # Determine DecompositionSpec
    effective_subqueries = tuple(c for c in clauses if c.strip()) if len(clauses) >= 2 else ()
    if effective_subqueries:
        decomp = DecompositionSpec(mode=DecompositionMode.PARALLEL, subqueries=effective_subqueries)
    elif multi_hop or strategy_mode == "MULTI_HOP":
        decomp = DecompositionSpec(mode=DecompositionMode.SEQUENTIAL, subqueries=())
    else:
        decomp = DecompositionSpec(mode=DecompositionMode.NONE, subqueries=())

    # Resolve CardinalitySpec with strict precedence: API > QUERY > DEFAULT
    default_n, max_cap = _CARDINALITY_BOUNDS.get(
        (target, output),
        (FIXED_SEARCH_TOP_K, 20),
    )
    if (
        target == TargetUnit.PASSAGE
        and output == OutputMode.COMPARE
        and len(decomp.subqueries) >= 2
    ):
        default_n = min(10, max(6, 3 * len(decomp.subqueries)))
        max_cap = max(max_cap, 15)

    if requested_top_k is not None:
        if requested_top_k < 1:
            raise ValueError("top_k must be positive")
        # When an API caller explicitly passes top_k, honor it up to the plan's max_cap
        # (or up to 50 if the caller explicitly requests a larger set via API).
        api_cap = max(max_cap, 50) if requested_top_k > max_cap else max_cap
        effective_n = min(max(1, int(requested_top_k)), api_cap)
        resolved_mode = CardinalityMode.ONE if effective_n == 1 else CardinalityMode.N
        cardinality = CardinalitySpec(
            mode=resolved_mode,
            requested_n=int(requested_top_k),
            effective_n=effective_n,
            max_cap=api_cap,
            count_source=CountSource.API,
            cursor=cursor,
        )
    elif signals.explicit_count is not None:
        effective_n = min(max(1, signals.explicit_count), max_cap)
        resolved_mode = CardinalityMode.ONE if effective_n == 1 else card_mode
        cardinality = CardinalitySpec(
            mode=resolved_mode,
            requested_n=signals.explicit_count,
            effective_n=effective_n,
            max_cap=max_cap,
            count_source=CountSource.QUERY,
            cursor=cursor,
        )
    else:
        effective_default_n = (
            max_cap
            if (target == TargetUnit.DOCUMENT and output == OutputMode.LIST and card_mode == CardinalityMode.ALL)
            else default_n
        )
        cardinality = CardinalitySpec(
            mode=card_mode,
            requested_n=None,
            effective_n=effective_default_n,
            max_cap=max_cap,
            count_source=CountSource.DEFAULT,
            cursor=cursor,
        )

    return RetrievalPlan(
        planner_version=RETRIEVAL_PLAN_VERSION,
        target=target,
        selection=selection,
        constraints=tuple(valid_constraints),
        cardinality=cardinality,
        order=signals.order,
        decomposition=decomp,
        output=output,
        fallback_used=fallback_used,
        unsupported_reason=unsupported_reason,
    )


_CATALOG_META_TOKENS: frozenset[str] = frozenset(
    {
        "liệt", "kê", "danh", "sách", "kể", "tên", "tất", "cả", "toàn", "bộ", "mọi",
        "tài", "liệu", "file", "files", "document", "documents", "doc", "docs", "tệp",
        "tập", "tin", "văn", "bản", "hồ", "sơ", "báo", "cáo", "spec", "specs", "runbook", "runbooks",
        "dự", "án", "project", "projects", "workspace", "kho", "tri", "thức", "knowledge", "base",
        "scope", "phạm", "vi", "nguồn", "source", "sources", "hiện", "tại", "có", "gồm",
        "bao", "nhiêu", "mấy", "tổng", "cộng", "con", "số", "lượng", "đếm", "sẵn", "sàng",
        "tra", "cứu", "tìm", "kiếm", "xem", "cho", "biết", "tôi", "hỏi", "hãy", "vui", "lòng", "please",
        "list", "show", "all", "every", "how", "many", "available", "ready", "indexed", "searchable",
        "search", "search_ready", "in", "the", "of", "for", "and", "or", "what", "are", "which", "current",
        "của", "trong", "ở", "thuộc", "là", "gì", "nào", "những", "các", "đã", "chưa", "đang", "được",
        "và", "hay", "đầy", "đủ", "cụ", "thể", "từng", "hết", "rõ", "nêu", "ra", "kèm", "trích", "dẫn",
        "nhé", "nội", "dung", "quan", "chính", "này", "đó", "một",
    }
)
_SOURCE_NAME_ALIASES: dict[str, tuple[str, ...]] = {
    "database": ("db", "cơ", "sở", "dữ", "liệu"),
    "design": ("thiết", "kế", "ui", "ux"),
    "documentation": ("doc", "docs", "báo", "cáo"),
    "frontend": ("fe",),
    "backend": ("be",),
    "general": ("chung",),
    "research": ("nghiên", "cứu"),
}
_TOPIC_FILTER_CUES_RE = re.compile(
    r"\b(?:nói\s+về|viết\s+về|bàn\s+về|liên\s+quan|đề\s+cập|chứa|"
    r"about|regarding|related\s+to|concerning|containing|mentioning)\b",
    re.IGNORECASE | re.UNICODE,
)
_BOUND_SCOPE_NAME_RE = re.compile(
    r"\b(?:project|dự\s+án|workspace)\s+[\w-]+",
    re.IGNORECASE | re.UNICODE,
)


def is_unfiltered_scope_catalog_query(
    query: str,
    source_names: Iterable[str] = (),
) -> bool:
    """Return True when a DOCUMENT LIST/COUNT query asks for the entire bound scope without a narrow topic filter."""
    normalized_q = unicodedata.normalize("NFC", query or "").strip()
    if not normalized_q:
        return False
    signals = extract_query_cardinality_and_shape(normalized_q)
    if (
        signals.target != TargetUnit.DOCUMENT
        or signals.output not in {OutputMode.LIST, OutputMode.COUNT}
        or signals.explicit_count is not None
    ):
        return False
    if re.search(r'["“”`]|\b[\w-]+\.(?:md|pdf|docx|txt|json|ya?ml|csv|xlsx)\b', normalized_q, re.IGNORECASE):
        return False
    if _TOPIC_FILTER_CUES_RE.search(normalized_q):
        return False
    stripped_q = _BOUND_SCOPE_NAME_RE.sub(" ", normalized_q)
    allowed = set(_CATALOG_META_TOKENS)
    for raw_name in source_names:
        for tok in re.findall(r"[^\W_]+", unicodedata.normalize("NFC", raw_name or "").lower(), flags=re.UNICODE):
            allowed.add(tok)
            allowed.update(_SOURCE_NAME_ALIASES.get(tok, ()))
    tokens = re.findall(r"[^\W_]+", stripped_q.lower(), flags=re.UNICODE)
    leftover = [tok for tok in tokens if tok not in allowed]
    return len(leftover) == 0


def group_sections_by_document(
    sections: list[Any],
    *,
    limit: int,
    relative_floor: float = 0.50,
    absolute_floor: float = 0.20,
) -> list[Any]:
    """Group ACL-verified sections by document identity (1 representative section per document).

    Enforces a dynamic relevance cutoff so that if a user asks for 20 documents
    but only 7 documents are genuinely relevant, we return 7 instead of padding
    with 13 irrelevant documents.
    """
    if not sections or limit <= 0:
        return []

    best_by_doc: dict[str, Any] = {}
    ordered_doc_keys: list[str] = []
    for section in sections:
        doc_key = str(
            getattr(section, "document_id", None)
            or getattr(section, "document_version_id", None)
            or getattr(section, "document_name", None)
            or getattr(section, "chunk_id", None)
            or ""
        )
        if not doc_key:
            continue
        score = float(getattr(section, "score", 0.0) or 0.0)
        existing = best_by_doc.get(doc_key)
        if existing is None:
            ordered_doc_keys.append(doc_key)
            best_by_doc[doc_key] = section
        elif score > float(getattr(existing, "score", 0.0) or 0.0):
            best_by_doc[doc_key] = section

    if not ordered_doc_keys:
        return []

    representatives = sorted(
        (best_by_doc[k] for k in ordered_doc_keys),
        key=lambda sec: (
            -float(getattr(sec, "score", 0.0) or 0.0),
            int(getattr(sec, "rank", 0) or 0),
            str(getattr(sec, "document_id", None) or ""),
        ),
    )
    top_score = float(getattr(representatives[0], "score", 0.0) or 0.0)
    min_score = max(absolute_floor, top_score * relative_floor)

    selected: list[Any] = []
    for sec in representatives:
        if len(selected) >= limit:
            break
        score = float(getattr(sec, "score", 0.0) or 0.0)
        if selected and score < min_score:
            continue
        rank = len(selected)
        if hasattr(sec, "model_copy"):
            selected.append(sec.model_copy(update={"rank": rank}))
        else:
            selected.append(sec)
    return selected
