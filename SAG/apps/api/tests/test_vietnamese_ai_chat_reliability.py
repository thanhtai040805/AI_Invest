"""Regression tests for Vietnamese AI Chat retrieval, NFD normalization, catalog completeness, and entity admission."""

from __future__ import annotations

import unicodedata
from types import SimpleNamespace

import pytest

from sag_api.sag import RetrievedSection, SearchOutcome
from sag_api.sag.search_unit_store import build_sparse_query_vector
from sag_api.services.query_analysis import analyze_query, extract_query_features
from sag_api.services.query_strategy_planner import plan_query
from sag_api.services.retrieval_plan import (
    CardinalityMode,
    OutputMode,
    TargetUnit,
    build_retrieval_plan,
)
from sag_api.services.retrieval_service import select_diverse_sections
from sag_api.services.search_index_service import compute_sparse_bm25_vector
from sag_api.tools.base import ToolContext
from sag_api.tools.builtin import SearchContextTool


@pytest.mark.parametrize(
    "nfc_query",
    [
        "Theo tài liệu 02_FRONTEND_NEXTJS.md, frontend quản lý trạng thái như thế nào?",
        "Các collection trong cơ sở dữ liệu IAM gồm những gì và quan hệ ra sao?",
        "Dự án Database hiện có bao nhiêu tài liệu?",
    ],
)
def test_vietnamese_nfd_and_nfc_produce_identical_analysis_and_sparse_vectors(
    nfc_query: str,
) -> None:
    """Decomposed Vietnamese Unicode (NFD) must normalize identically to precomposed NFC across retrieval."""
    composed = unicodedata.normalize("NFC", nfc_query)
    decomposed = unicodedata.normalize("NFD", nfc_query)
    assert composed != decomposed

    assert analyze_query(decomposed) == analyze_query(composed)
    assert extract_query_features(decomposed) == extract_query_features(composed)
    assert build_retrieval_plan(decomposed) == build_retrieval_plan(composed)
    assert build_sparse_query_vector(decomposed) == build_sparse_query_vector(composed)
    assert compute_sparse_bm25_vector(decomposed) == compute_sparse_bm25_vector(composed)


def test_vietnamese_document_list_and_catalog_query_shape_detection() -> None:
    """Vietnamese catalog questions with modifiers must compile to DOCUMENT+LIST without false positives."""
    design_plan = plan_query("Trong project Design có những tài liệu thiết kế nào?").retrieval_plan
    assert design_plan.target == TargetUnit.DOCUMENT
    assert design_plan.output == OutputMode.LIST
    assert design_plan.per_document_limit == 1

    fe_plan = plan_query("Dự án Frontend hiện có những tài liệu nào sẵn sàng tra cứu?").retrieval_plan
    assert fe_plan.target == TargetUnit.DOCUMENT
    assert fe_plan.output == OutputMode.LIST

    all_list_plan = plan_query("Liệt kê tất cả các tài liệu hiện có trong dự án Database").retrieval_plan
    assert all_list_plan.target == TargetUnit.DOCUMENT
    assert all_list_plan.output == OutputMode.LIST
    assert all_list_plan.cardinality.mode == CardinalityMode.ALL
    assert all_list_plan.cardinality.effective_n == 50

    # Factual citation attribution questions must remain PASSAGE + ANSWER
    fact_q1 = plan_query("Theo tài liệu nào frontend sử dụng Next.js?").retrieval_plan
    assert fact_q1.target == TargetUnit.PASSAGE
    assert fact_q1.output == OutputMode.ANSWER

    fact_q2 = plan_query("Trong tài liệu nào mô tả kiến trúc BFF?").retrieval_plan
    assert fact_q2.target == TargetUnit.PASSAGE
    assert fact_q2.output == OutputMode.ANSWER


def test_vietnamese_relation_cues_and_entity_filename_diversity_selection() -> None:
    """Queries naming a domain acronym and 'quan hệ' must admit the matching schema file alongside README."""
    query = "Các collection trong cơ sở dữ liệu IAM gồm những gì và quan hệ ra sao?"
    features = extract_query_features(query)
    assert any("quan hệ" in cue for cue in features.relation_cues)

    candidates: list[RetrievedSection] = []
    # Index overview README.md with 4 chunks (top score 0.86)
    for idx in range(4):
        candidates.append(
            RetrievedSection(
                chunk_id=f"readme-{idx}",
                document_id="doc-readme",
                document_version_id="ver-readme",
                document_name="README.md",
                heading=f"Database Overview {idx}",
                content=f"Overview of Continuum database schemas part {idx} mentioning IAM and Jira.",
                score=round(0.86 - 0.01 * idx, 4),
                rank=len(candidates),
                source_id="src-db",
                source_config_id="cfg-db",
            )
        )
    # Dedicated schema document 01_SVC_IAM_SCHEMA.md with slightly lower fused score (0.76 < 0.94 * 0.86)
    for idx in range(3):
        candidates.append(
            RetrievedSection(
                chunk_id=f"iam-{idx}",
                document_id="doc-iam",
                document_version_id="ver-iam",
                document_name="01_SVC_IAM_SCHEMA.md",
                heading=f"IAM Collections {idx}",
                content=f"IAM MongoDB collections users, organizations, memberships and relationships {idx}.",
                score=round(0.76 - 0.01 * idx, 4),
                rank=len(candidates),
                source_id="src-db",
                source_config_id="cfg-db",
            )
        )
    # Weak unrelated noise document
    candidates.append(
        RetrievedSection(
            chunk_id="noise-0",
            document_id="doc-noise",
            document_version_id="ver-noise",
            document_name="99_UNRELATED_RUNBOOK.md",
            heading="Unrelated Runbook",
            content="Completely unrelated operational checklist.",
            score=0.12,
            rank=len(candidates),
            source_id="src-db",
            source_config_id="cfg-db",
        )
    )

    selected, _ = select_diverse_sections(query, candidates, limit=8)
    selected_docs = {s.document_name for s in selected}
    assert "README.md" in selected_docs
    assert "01_SVC_IAM_SCHEMA.md" in selected_docs
    assert "99_UNRELATED_RUNBOOK.md" not in selected_docs


@pytest.mark.asyncio
async def test_search_context_tool_preserves_catalog_cardinality_and_exposes_filename_locator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM default top_k=5 must not truncate catalog queries, and Locator must include file=<document_name>."""
    captured_calls: list[dict[str, object]] = []

    sections = [
        RetrievedSection(
            chunk_id=f"su-{idx}",
            search_unit_id=f"su-{idx}",
            document_id=f"doc-{idx}",
            document_version_id=f"ver-{idx}",
            document_name=f"{idx:02d}_SCHEMA_DOC.md",
            section_path="## Overview",
            heading="## Overview",
            block_from_id=f"b-{idx}-1",
            block_to_id=f"b-{idx}-2",
            page_from=1,
            page_to=1,
            anchor=f"h2-overview-{idx}",
            content=f"Schema specification content for module {idx}.",
            score=round(0.90 - 0.01 * idx, 4),
            rank=idx,
            source_id="src-db",
            source_config_id="cfg-db",
            canonical_evidence_verified=True,
        )
        for idx in range(16)
    ]

    async def fake_retrieve(engine_manager, sources, query, *, principal, top_k=None, query_strategy_plan=None):
        captured_calls.append({"query": query, "top_k": top_k, "plan": query_strategy_plan})
        return SearchOutcome(
            query=query,
            sections=sections,
            stats={"ready_versions": 16, "candidates": 16},
        )

    from sag_api.services import search_unit_retrieval_service

    monkeypatch.setattr(search_unit_retrieval_service, "retrieve_search_unit_sections", fake_retrieve)

    tool = SearchContextTool()
    ctx = ToolContext(
        engine_manager=SimpleNamespace(),
        sources=[SimpleNamespace(id="src-db", name="Database", sag_source_config_id="cfg-db")],
        principal=None,
        persona={"top_k": 5},
        evidence_token_budget=16000,
    )
    nfd_catalog_q = unicodedata.normalize("NFD", "Liệt kê tất cả các tài liệu hiện có trong dự án Database")
    result = await tool.invoke(
        {"query": nfd_catalog_q, "top_k": 5},
        ctx,
    )

    assert len(captured_calls) == 1
    assert captured_calls[0]["query"] == "Liệt kê tất cả các tài liệu hiện có trong dự án Database"
    assert int(captured_calls[0]["top_k"] or 0) >= 50
    assert len(result.citations) == 16
    assert "file=00_SCHEMA_DOC.md;" in result.content
    assert "file=15_SCHEMA_DOC.md;" in result.content
    assert "16" in result.content


@pytest.mark.asyncio
async def test_initial_tool_choice_clarification_vs_followup_and_capability_greeting():
    from sag_agent import AgentTool, ToolSpec
    from sag_api.services.agent_service import _initial_tool_choice

    async def noop(args, ctx):
        return ""

    search_tool = AgentTool(
        spec=ToolSpec(name="search_context", description="Search knowledge documents", parameters={"type": "object"}),
        executor=noop,
    )
    tools = (search_tool,)

    # 1. Underspecified first-turn pronoun questions must clarify first ("none"), not blindly search
    first_turn_choice = await _initial_tool_choice(
        "Nó hoạt động ra sao?",
        tools,
        knowledge_only=True,
        scoped=True,
        route_decision={"is_chitchat": False, "confidence": 0.1},
        has_prior_turns=False,
    )
    assert first_turn_choice == "none"

    second_first_turn = await _initial_tool_choice(
        "Cái đó như thế nào?",
        tools,
        knowledge_only=True,
        scoped=True,
        route_decision={"is_chitchat": False, "confidence": 0.1},
        has_prior_turns=False,
    )
    assert second_first_turn == "none"

    # 2. In a multi-turn follow-up (has_prior_turns=True), the same pronoun question MUST search knowledge
    followup_choice = await _initial_tool_choice(
        "Nó hoạt động ra sao?",
        tools,
        knowledge_only=True,
        scoped=True,
        route_decision={"is_chitchat": False, "confidence": 0.1},
        has_prior_turns=True,
    )
    assert followup_choice == {"type": "function", "function": {"name": "search_context"}}

    # 3. Greeting + assistant capability introduction should answer directly ("none"),
    #    whereas asking about project/document capabilities MUST still call search_context!
    greeting_cap = await _initial_tool_choice(
        "Xin chào. Hãy giới thiệu ngắn gọn khả năng hỗ trợ của bạn.",
        tools,
        knowledge_only=True,
        scoped=True,
        route_decision={"is_chitchat": False, "confidence": 0.1},
        has_prior_turns=False,
    )
    assert greeting_cap == "none"

    project_cap = await _initial_tool_choice(
        "Giới thiệu khả năng hỗ trợ của hệ thống trong tài liệu dự án.",
        tools,
        knowledge_only=True,
        scoped=True,
        route_decision={"is_chitchat": False, "confidence": 0.1},
        has_prior_turns=False,
    )
    assert project_cap == {"type": "function", "function": {"name": "search_context"}}


def test_prompt_guidance_forbids_speculative_world_knowledge_for_unmentioned_aspects():
    from sag_api.generation.prompt import _GUIDANCE

    guidance = _GUIDANCE["en"].lower()
    assert "do not" in guidance or "never" in guidance
    assert "speculative" in guidance


def test_vietnamese_dmy_and_iso_date_bidirectional_lexical_bridging():
    from sag_api.services.query_analysis import analyze_query

    dmy_analysis = analyze_query("backend chưa có Project API nào đúng không? cập nhật ngày 12/10/2026")
    assert "2026-10-12" in dmy_analysis.scoring_terms
    assert "2026-10-12" in dmy_analysis.lookup_terms

    iso_analysis = analyze_query("Đối chiếu mã nguồn ngày 2026-10-22")
    assert "22/10/2026" in iso_analysis.scoring_terms
    assert "22/10/2026" in iso_analysis.lookup_terms


def test_finalize_answer_citations_accepts_non_url_parenthetical_after_bracket_and_keeps_http_links():
    from sag_api.services.agent_service import _finalize_answer_citations

    citations = [
        {
            "n": 3,
            "chunk_id": "01485117-4827-5005-8707-f0c00fb77621",
            "source_id": "src-fe",
            "document_id": "doc-04",
            "document_version_id": "ver-04",
            "document_name": "04-team-membership-workspace-fe-contract.md",
            "page_from": 1,
            "page_to": 1,
            "anchor": "h1-0",
            "snippet": "Current evidence successor — 2026-10-12: current Project APIs exist.",
        }
    ]
    # Model writes [3](Current evidence successor) without a space before '('
    answer_text = (
        "Theo bản cập nhật kế nhiệm, hiện tại Project APIs đã tồn tại [3](Current evidence successor — 2026-10-12), "
        "xem thêm [1](https://example.com/spec)."
    )
    canonical, normalized = _finalize_answer_citations(answer_text, citations)
    assert "[1](https://example.com/spec)" in canonical
    assert len(normalized) == 1
    assert normalized[0]["n"] == 3
    assert normalized[0]["mapped"] is True
    assert normalized[0]["claim_level"] == "claim"
def test_unfiltered_scope_catalog_recognizes_named_project_scope_but_rejects_topic_filter():
    from sag_api.services.retrieval_plan import is_unfiltered_scope_catalog_query

    fe_sources = ["Project 6ad8834abef68a3326035eeb"]
    assert is_unfiltered_scope_catalog_query(
        "Danh sách các tài liệu đã sẵn sàng tra cứu trong project Frontend",
        fe_sources,
    )
    assert is_unfiltered_scope_catalog_query(
        "Trong project Frontend hiện có những tài liệu nào đã sẵn sàng tra cứu? Hãy liệt kê đầy đủ tên tệp.",
        fe_sources,
    )
    assert is_unfiltered_scope_catalog_query(
        "Project Database hiện có bao nhiêu tài liệu sẵn sàng tìm kiếm? "
        "Hãy nêu con số tổng cộng và liệt kê tên các tệp đó.",
        ["Project 6addb493601e4bc926ed440c"],
    )
    assert is_unfiltered_scope_catalog_query(
        "Trong project SAG hiện có những tài liệu nào sẵn sàng tra cứu?",
        ["Project 6addb47a601e4bc926ed43a0"],
    )

    assert not is_unfiltered_scope_catalog_query(
        "Danh sách các tài liệu về Redis trong project Backend",
        ["Project 6ad7758334bd88b3f2cc844d"],
    )
    assert not is_unfiltered_scope_catalog_query(
        "Liệt kê các tài liệu xác thực JWT trong project Frontend",
        fe_sources,
    )


@pytest.mark.asyncio
async def test_search_context_catalog_query_ignores_project_acronym_anchor_and_emits_groundable_inventory(monkeypatch):
    from types import SimpleNamespace

    from sag_api.sag import RetrievedSection
    from sag_api.tools.base import ToolContext
    from sag_api.tools.builtin import SearchContextTool

    fake_sections = [
        RetrievedSection(
            chunk_id=f"su-{idx}",
            search_unit_id=f"su-{idx}",
            source_config_id="cfg-sag",
            document_id=f"doc-{idx}",
            document_version_id=f"ver-{idx}",
            document_name=fname,
            version_no=1,
            heading="Overview",
            section_path="Overview",
            content="Storage and workflow specification excerpt without project acronym.",
            score=0.75 - idx * 0.05,
            rank=idx,
            page_from=1,
            page_to=1,
            block_from_id=f"b-{idx}",
            block_to_id=f"b-{idx}",
            anchor="h1-0",
            canonical_evidence_verified=True,
        )
        for idx, fname in enumerate(
            [
                "04_STORAGE_MESSAGING_AI.md",
                "11-knowledge-gap-workflow-contract.md",
                "11_SAG_STORAGE_SCHEMA.md",
            ]
        )
    ]

    async def fake_retrieve(*_args, **_kwargs):
        return SimpleNamespace(sections=fake_sections, stats={"ready_versions": 3, "candidates": 3})

    monkeypatch.setattr(
        "sag_api.services.search_unit_retrieval_service.retrieve_search_unit_sections",
        fake_retrieve,
    )

    ctx = ToolContext(
        engine_manager=SimpleNamespace(),
        sources=[
            SimpleNamespace(id="src-sag", name="Project 6addb47a601e4bc926ed43a0", sag_source_config_id="cfg-sag")
        ],
        principal=None,
        persona={},
        citation_offset=0,
        evidence_token_budget=8000,
    )
    result = await SearchContextTool().invoke(
        {"query": "Trong project SAG hiện có những tài liệu nào sẵn sàng tra cứu?", "top_k": 5},
        ctx,
    )
    assert result.data["section_count"] == 3
    assert len(result.citations) == 3
    assert "Tổng số tài liệu sẵn sàng tra cứu trong phạm vi hiện tại: 3" in result.content
    assert "- `04_STORAGE_MESSAGING_AI.md` [1]" in result.content
    assert "- `11-knowledge-gap-workflow-contract.md` [2]" in result.content
    assert "- `11_SAG_STORAGE_SCHEMA.md` [3]" in result.content
    assert "Danh mục tài liệu SEARCH_READY đã xác minh [1]: 04_STORAGE_MESSAGING_AI.md" in result.content


@pytest.mark.asyncio
async def test_search_context_standard_query_emits_mandatory_citation_gate_instruction_without_numeric_ref_leak(
    monkeypatch,
):
    from types import SimpleNamespace

    from sag_api.sag import RetrievedSection
    from sag_api.services.agent_service import _CITATION_REFERENCE
    from sag_api.tools.base import ToolContext
    from sag_api.tools.builtin import SearchContextTool

    fake_sections = [
        RetrievedSection(
            chunk_id="su-rsc",
            search_unit_id="su-rsc",
            source_config_id="cfg-fe",
            document_id="doc-fe-02",
            document_version_id="ver-fe-02",
            document_name="02_FRONTEND_NEXTJS.md",
            version_no=1,
            heading="1. Lý do lựa chọn Next.js",
            section_path="1. Lý do lựa chọn Next.js",
            content="Giúp giảm 45% dung lượng Bundle JS ban đầu (Initial Bundle Size).",
            score=0.78,
            rank=0,
            page_from=1,
            page_to=1,
            block_from_id="b-1",
            block_to_id="b-1",
            anchor="h2-3",
            canonical_evidence_verified=True,
        )
    ]

    async def fake_retrieve(*_args, **_kwargs):
        return SimpleNamespace(sections=fake_sections, stats={"ready_versions": 3, "candidates": 1})

    monkeypatch.setattr(
        "sag_api.services.search_unit_retrieval_service.retrieve_search_unit_sections",
        fake_retrieve,
    )

    ctx = ToolContext(
        engine_manager=SimpleNamespace(),
        sources=[
            SimpleNamespace(id="src-fe", name="Project 6ad8834abef68a3326035eeb", sag_source_config_id="cfg-fe")
        ],
        principal=None,
        persona={},
        citation_offset=0,
        evidence_token_budget=8000,
    )
    result = await SearchContextTool().invoke(
        {"query": "React Server Components giúp giảm bao nhiêu phần trăm Initial Bundle Size?", "top_k": 5},
        ctx,
    )
    header, body = result.content.split("\n\n", 1)
    assert "BẮT BUỘC gắn nhãn trích dẫn" in header
    assert "[n]" in header
    assert "TUYỆT ĐỐI KHÔNG gắn" in header
    assert _CITATION_REFERENCE.findall(header) == []
    assert body.startswith("[1] 1. Lý do lựa chọn Next.js")

