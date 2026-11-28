"""Unit tests for the Compositional 7-Axis RetrievalPlan architecture (qsp-v2)."""

from __future__ import annotations

import pytest

from sag_api.sag import RetrievedSection
from sag_api.services.query_strategy_planner import PLANNER_VERSION, plan_query
from sag_api.services.retrieval_plan import (
    CardinalityMode,
    CountSource,
    DecompositionMode,
    FieldPredicate,
    OrderMode,
    OutputMode,
    SelectionMode,
    TargetUnit,
    build_retrieval_plan,
    group_sections_by_document,
)
from sag_api.services.retrieval_service import select_diverse_sections


def test_default_passage_answer_preserves_top5() -> None:
    """Standard factual Q&A without explicit count defaults to PASSAGE + ANSWER + k=5."""
    strat = plan_query("When must an evidence claim be rejected?")
    plan = strat.retrieval_plan
    assert strat.planner_version == PLANNER_VERSION == "qsp-v2"
    assert plan.target == TargetUnit.PASSAGE
    assert plan.selection == SelectionMode.RELEVANCE
    assert plan.output == OutputMode.ANSWER
    assert plan.cardinality.mode == CardinalityMode.N
    assert plan.cardinality.effective_n == 5
    assert plan.cardinality.count_source == CountSource.DEFAULT
    assert plan.per_document_limit is None


@pytest.mark.parametrize(
    "query,expected_target,expected_output,expected_n,expected_source,expected_per_doc",
    [
        (
            "Hãy liệt kê 20 file liên quan đến chính sách bảo mật",
            TargetUnit.DOCUMENT,
            OutputMode.LIST,
            20,
            CountSource.QUERY,
            1,
        ),
        (
            "Mở 20 file tài liệu về kiến trúc hệ thống",
            TargetUnit.DOCUMENT,
            OutputMode.OPEN,
            20,
            CountSource.QUERY,
            1,
        ),
        (
            "Find top 15 documents about disaster recovery",
            TargetUnit.DOCUMENT,
            OutputMode.LIST,
            15,
            CountSource.QUERY,
            1,
        ),
        (
            "Có bao nhiêu tài liệu quy định về phân vùng bảo mật?",
            TargetUnit.DOCUMENT,
            OutputMode.COUNT,
            50,
            CountSource.DEFAULT,
            1,
        ),
        (
            "Tóm tắt tất cả các tài liệu trong dự án",
            TargetUnit.DOCUMENT,
            OutputMode.SUMMARIZE,
            10,
            CountSource.DEFAULT,
            1,
        ),
        (
            "Mở tài liệu hướng dẫn vận hành",
            TargetUnit.DOCUMENT,
            OutputMode.OPEN,
            1,
            CountSource.QUERY,
            1,
        ),
        (
            "So sánh cơ chế phân quyền giữa phiên bản v1 và v2",
            TargetUnit.PASSAGE,
            OutputMode.COMPARE,
            8,
            CountSource.DEFAULT,
            None,
        ),
    ],
)
def test_seven_axis_retrieval_plan_compilation(
    query: str,
    expected_target: TargetUnit,
    expected_output: OutputMode,
    expected_n: int,
    expected_source: CountSource,
    expected_per_doc: int | None,
) -> None:
    strat = plan_query(query)
    plan = strat.retrieval_plan
    assert plan.target == expected_target
    assert plan.output == expected_output
    assert plan.cardinality.effective_n == expected_n
    assert plan.cardinality.count_source == expected_source
    assert plan.per_document_limit == expected_per_doc


def test_cardinality_precedence_api_over_query_over_default_and_clamping() -> None:
    # 1. Query asks for 20 files -> effective_n = 20 (QUERY)
    q = "Liệt kê 20 file liên quan đến thanh toán"
    plan_q = build_retrieval_plan(q)
    assert plan_q.cardinality.effective_n == 20
    assert plan_q.cardinality.count_source == CountSource.QUERY

    # 2. API caller explicitly passes top_k=12 -> overrides query count (API > QUERY)
    plan_api = build_retrieval_plan(q, requested_top_k=12)
    assert plan_api.cardinality.effective_n == 12
    assert plan_api.cardinality.count_source == CountSource.API

    # 3. Query asks for 500 files -> clamped to max_cap=50
    plan_clamped = build_retrieval_plan("Liệt kê 500 file liên quan")
    assert plan_clamped.cardinality.effective_n == 50
    assert plan_clamped.cardinality.max_cap == 50


def test_field_predicate_validation_and_fallback() -> None:
    valid_pred = FieldPredicate(field="document_name", op="contains", value="runbook")
    invalid_pred = FieldPredicate(field="unknown_column", op="regex", value=".*")
    plan = build_retrieval_plan(
        "Find runbook",
        constraints=(valid_pred, invalid_pred),
    )
    assert plan.constraints == (valid_pred,)
    assert plan.fallback_used is True
    assert plan.unsupported_reason == "unsupported_predicate:unknown_column:regex"


def test_temporal_order_and_parallel_clause_decomposition() -> None:
    strat = plan_query(
        "Liệt kê 10 tài liệu mới nhất về bảo mật, và ai là người phê duyệt chính sách?"
    )
    plan = strat.retrieval_plan
    assert plan.target == TargetUnit.DOCUMENT
    assert plan.order == OrderMode.TIME_DESC
    assert plan.cardinality.effective_n == 10
    assert plan.decomposition.mode == DecompositionMode.PARALLEL
    assert len(plan.decomposition.subqueries) == 2


def test_document_target_groups_one_section_per_document_and_cuts_off_weak_tail() -> None:
    """When user asks for 20 files but only 6 documents match (with multiple chunks each) and 2 are noise,
    return exactly 6 distinct documents (1 representative chunk per document)."""
    candidates: list[RetrievedSection] = []
    # 6 genuinely relevant documents, each having 3 chunks
    for doc_idx in range(6):
        base_score = round(0.92 - 0.04 * doc_idx, 4)
        for chunk_idx in range(3):
            candidates.append(
                RetrievedSection(
                    chunk_id=f"doc-{doc_idx}-c-{chunk_idx}",
                    document_id=f"doc-{doc_idx}",
                    document_version_id=f"ver-{doc_idx}",
                    document_name=f"policy_{doc_idx}.md",
                    heading=f"Policy {doc_idx} Section {chunk_idx}",
                    content=f"Distinct security requirement {doc_idx} clause {chunk_idx} token_{doc_idx}_{chunk_idx}",
                    score=round(base_score - 0.02 * chunk_idx, 4),
                    rank=len(candidates),
                    source_id="src-1",
                    source_config_id="cfg-1",
                )
            )
    # 2 irrelevant tail documents with very low score (0.12 and 0.08)
    for noise_idx in range(2):
        candidates.append(
            RetrievedSection(
                chunk_id=f"noise-{noise_idx}",
                document_id=f"doc-noise-{noise_idx}",
                document_version_id=f"ver-noise-{noise_idx}",
                document_name=f"unrelated_{noise_idx}.md",
                heading="Unrelated",
                content=f"Completely unrelated cafeteria menu {noise_idx}",
                score=round(0.12 - 0.04 * noise_idx, 4),
                rank=len(candidates),
                source_id="src-1",
                source_config_id="cfg-1",
            )
        )

    # Test via select_diverse_sections with per_document_limit=1 and limit=20
    selected, _ = select_diverse_sections(
        "Liệt kê 20 file liên quan đến chính sách bảo mật",
        candidates,
        limit=20,
        per_document_limit=1,
    )
    assert len(selected) == 6
    assert [s.document_id for s in selected] == [f"doc-{i}" for i in range(6)]
    assert [s.chunk_id for s in selected] == [f"doc-{i}-c-0" for i in range(6)]

    # Test via group_sections_by_document helper directly
    grouped = group_sections_by_document(candidates, limit=20)
    assert len(grouped) == 6
    assert [s.document_id for s in grouped] == [f"doc-{i}" for i in range(6)]
