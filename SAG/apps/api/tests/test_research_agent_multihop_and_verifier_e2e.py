"""Phase 3 + Phase 4 + Phase 5 L1-L3 E2E & Adversarial Tests: BM25, Reranker/CRAG, Parallel Tools & Stage 7."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

import httpx
import pytest

from sag_agent.types import (
    CancellationToken,
    ModelChunk,
    ModelRequest,
    ToolCall,
    Usage,
)
from sag_api.core.config import settings
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.research.agent_runner import SentenceStreamGate
from sag_api.research.chat_orchestrator import (
    ResearchChatOrchestrator,
    RetrievalResult,
    RetrievedResearchSection,
    collect_stream_result,
    evaluate_evidence_sufficiency,
)
from sag_api.research.quote_verifier import (
    CanonicalBlockRecord,
    compute_quote_hash,
    verify_citations,
    verify_numeric_claims_against_evidence,
)
from sag_api.sag import RetrievedSection
from sag_api.schemas.research_chat import HydratedTableItem, ResearchChatStreamRequest
from sag_api.services.reranker_client import CrossEncoderRerankerClient, rerank_and_grade_sections
from sag_api.services.retrieval_service import select_diverse_sections
from sag_api.services.search_index_service import COMMON_STOPWORDS, compute_sparse_bm25_vector


def test_bm25_preserves_vas_accounting_stopwords_and_emits_bigrams() -> None:
    for kept in ("phải", "không", "chưa"):
        assert kept not in COMMON_STOPWORDS

    vec_ar = compute_sparse_bm25_vector("Phải thu ngắn hạn của khách hàng FPT")
    vec_ap = compute_sparse_bm25_vector("Phải trả người bán ngắn hạn FPT")
    assert vec_ar["indices"]
    assert vec_ap["indices"]
    # "phải_thu" vs "phải_trả" MUST produce distinct sparse index signatures!
    assert set(vec_ar["indices"]) != set(vec_ap["indices"])


def test_select_diverse_sections_enforces_min_per_project_quota() -> None:
    # 5 high-score FPT chunks + 2 lower-score HPG chunks -> min_per_project_quota=2 guarantees HPG gets 2 slots
    candidates = [
        RetrievedSection(
            rank=i,
            source_id="FPT",
            source_name="FPT",
            document_id=f"d_fpt_{i}",
            document_name=f"FPT BCTC {i}",
            heading=f"FPT Section {i}",
            content=f"Doanh thu và lợi nhuận FPT phần {i} với chi tiết riêng biệt {i * 17}.",
            score=0.95 - i * 0.01,
        )
        for i in range(5)
    ] + [
        RetrievedSection(
            rank=5 + i,
            source_id="HPG",
            source_name="HPG",
            document_id=f"d_hpg_{i}",
            document_name=f"HPG BCTC {i}",
            heading=f"HPG Section {i}",
            content=f"Sản lượng thép HRC và doanh thu HPG phần {i} với số liệu kiểm toán {i * 29}.",
            score=0.60 - i * 0.01,
        )
        for i in range(2)
    ]
    selected, _meta = select_diverse_sections(
        "So sánh doanh thu FPT và HPG",
        candidates,
        limit=4,
        per_document_limit=10,
        min_per_project_quota=2,
    )
    hpg_count = sum(1 for s in selected if s.source_id == "HPG")
    fpt_count = sum(1 for s in selected if s.source_id == "FPT")
    assert len(selected) == 4
    assert hpg_count == 2
    assert fpt_count == 2


@pytest.mark.asyncio
async def test_cross_encoder_reranker_siliconflow_deepinfra_and_crag_grader() -> None:
    def _sf_handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/rerank")
        return httpx.Response(
            200,
            json={
                "results": [
                    {"index": 1, "relevance_score": 0.91},
                    {"index": 0, "relevance_score": 0.12},
                    {"index": 2, "relevance_score": 0.05},
                ]
            },
        )

    sections = [
        RetrievedResearchSection(
            ticker="FPT",
            doc_id="d1",
            document_version_id="v1",
            sub_doc_slot="S03_BS_CONSOL",
            page_from=4,
            page_to=4,
            published_date=date(2026, 3, 20),
            heading="Bảng cân đối kế toán",
            content="Tổng tài sản ngắn hạn FPT đạt 35,000 tỷ VND.",
            table_id="tbl_bs",
            score=0.50,
        ),
        RetrievedResearchSection(
            ticker="FPT",
            doc_id="d1",
            document_version_id="v1",
            sub_doc_slot="S07_NOTES",
            page_from=42,
            page_to=42,
            published_date=date(2026, 3, 20),
            heading="Thuyết minh V.20 Vay và nợ thuê tài chính",
            content="Chi tiết khoản vay ngắn hạn bằng USD và lãi suất thả nổi tại ngân hàng.",
            score=0.45,
        ),
        RetrievedResearchSection(
            ticker="FPT",
            doc_id="d1",
            document_version_id="v1",
            sub_doc_slot="S01_AR",
            page_from=12,
            page_to=12,
            published_date=date(2026, 3, 20),
            heading="Hoạt động văn hóa doanh nghiệp",
            content="Ngày hội thể thao nhân viên FPT toàn quốc.",
            score=0.48,
        ),
    ]

    async with httpx.AsyncClient(transport=httpx.MockTransport(_sf_handler)) as sf_http:
        sf_client = CrossEncoderRerankerClient(
            provider="siliconflow",
            model="Qwen/Qwen3-Reranker-0.6B",
            api_key="sf-key",
            base_url="https://api.siliconflow.com/v1",
            http_client=sf_http,
        )
        out = await rerank_and_grade_sections(
            "Chi tiết khoản vay ngân hàng USD của FPT theo Thuyết minh V.20",
            sections,
            top_k=2,
            target_tickers=["FPT"],
            protect_table_cards=True,
            min_table_cards=1,
            reranker=sf_client,
        )
        assert out.reranker_used is True
        assert out.crag_grade == "CORRECT"
        # Reranker puts V.20 footnote #1 AND table_card protection preserves tbl_bs in slot #2
        assert out.sections[0].sub_doc_slot == "S07_NOTES"
        assert any(s.table_id == "tbl_bs" for s in out.sections)

    # DeepInfra endpoint + low score -> CRAG INCORRECT
    def _di_handler(request: httpx.Request) -> httpx.Response:
        assert "/inference/" in request.url.path
        return httpx.Response(200, json={"scores": [-4.2, -3.8]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(_di_handler)) as di_http:
        di_client = CrossEncoderRerankerClient(
            provider="deepinfra",
            model="Qwen/Qwen3-Reranker-0.6B",
            api_key="di-key",
            base_url="https://api.deepinfra.com/v1",
            http_client=di_http,
        )
        out_low = await rerank_and_grade_sections(
            "Hợp đồng phóng tên lửa SpaceX của FPT",
            sections[:2],
            top_k=2,
            target_tickers=["FPT"],
            reranker=di_client,
        )
        assert out_low.crag_grade == "INCORRECT"


def test_adversarial_stage7_catches_cross_ticker_unit_laundering_and_content_mismatch() -> None:
    # HPG evidence has "18.2 tỷ VND" (MONETARY). LLM writes "Biên lợi nhuận ròng của FPT đạt 18.2% [C1]"
    # -> MUST fail with UNSUPPORTED_NUMERIC_CLAIM (no cross-ticker or cross-unit laundering!)
    hpg_sec = RetrievedResearchSection(
        ticker="HPG",
        doc_id="doc-hpg",
        document_version_id="ver-hpg-1",
        sub_doc_slot="S07_NOTES",
        page_from=18,
        page_to=18,
        published_date=date(2026, 3, 15),
        heading="Thuyết minh kho bãi HPG",
        content="Chi phí thuê kho bãi thép HPG trong kỳ là 18.2 tỷ VND.",
    )
    fpt_sec = RetrievedResearchSection(
        ticker="FPT",
        doc_id="doc-fpt",
        document_version_id="ver-fpt-1",
        sub_doc_slot="S07_NOTES",
        page_from=25,
        page_to=25,
        published_date=date(2026, 3, 20),
        heading="Thuyết minh tài sản cố định",
        content="FPT thuê văn phòng làm việc tại Cầu Giấy với thời hạn 5 năm.",
    )
    sections_by_cid = {"C1": hpg_sec, "C2": fpt_sec}

    num_res = verify_numeric_claims_against_evidence(
        "Biên lợi nhuận ròng của FPT đạt 18.2% [C1].",
        sections_by_cid=sections_by_cid,
        hydrated_tables=[],
        calculator_traces=[],
        graph_hops=[],
    )
    assert len(num_res.unsupported_claims) >= 1
    assert any(b["code"] == "UNSUPPORTED_NUMERIC_CLAIM" for b in num_res.blindspots)

    # Citation content mismatch trap: [C2] has valid quote_hash on warehouse/office lease,
    # yet answer cites [C2] to support a totally unrelated semiconductor chip claim!
    quote_txt = "FPT thuê văn phòng làm việc tại Cầu Giấy với thời hạn 5 năm."
    blocks = {
        "ver-fpt-1": [
            CanonicalBlockRecord(
                block_id="blk-1",
                document_version_id="ver-fpt-1",
                sub_doc_slot="S07_NOTES",
                page_from=25,
                page_to=25,
                normalized_text=quote_txt,
                published_date=date(2026, 3, 20),
            )
        ]
    }
    cit = {
        "citation_id": "C2",
        "ticker": "FPT",
        "doc_id": "doc-fpt",
        "document_version_id": "ver-fpt-1",
        "sub_doc_slot": "S07_NOTES",
        "page": 25,
        "published_date": "2026-03-20",
        "quote": quote_txt,
        "quote_hash": compute_quote_hash(quote_txt),
    }
    ver = verify_citations(
        [cit],
        blocks,
        date(2026, 6, 30),
        answer_text="Mảng bán dẫn xuất khẩu sang Nhật Bản tăng đột biến trong năm [C2].",
        valid_citation_ids={"C2"},
        sections_by_cid={"C2": fpt_sec},
    )
    assert len(ver.verified_citations) == 0
    assert any(b["code"] == "CITATION_CONTENT_MISMATCH" for b in ver.blindspots)
    assert "[C2 UNVERIFIED_CLAIM]" in ver.sanitized_answer


def test_evaluate_evidence_sufficiency_abstains_when_sections_empty_and_fallback_table_offtopic() -> None:
    bs_table = HydratedTableItem(
        table_id="tbl-bs",
        project_id="FPT",
        title="Bảng cân đối kế toán hợp nhất",
        sub_doc_slot="S03_BS_CONSOL",
        page_from=4,
        page_to=4,
        verification_status="VERIFIED",
        headers=[["Chỉ tiêu", "2025"]],
        rows=[{"label": "Tiền và tương đương tiền", "values": [9500.0]}],
    )
    bs_items, should_abstain = evaluate_evidence_sufficiency(
        resolved_query="Hợp đồng phóng vệ tinh vũ trụ SpaceX của FPT trị giá bao nhiêu?",
        explicit_tickers=["FPT"],
        sections=[],
        hydrated_tables=[bs_table],
        graph_hops=[],
    )
    assert should_abstain is True
    assert any(b.code == "LOW_RELEVANCE_EVIDENCE" for b in bs_items)


def test_sentence_stream_gate_blocks_tool_preamble_and_rewrites_hallucinated_cid() -> None:
    gate = SentenceStreamGate(valid_cid_ceiling=lambda: 1, lookahead_chars=180)
    gate.reset_for_turn(tools_enabled=True)
    # Short preamble held back while waiting for tool signal
    assert gate.push_delta("Để trả lời chính xác, tôi sẽ tra cứu ngay. ") == []
    gate.mark_pending_tool_call()
    assert gate.push_delta("Đang gọi tool...") == []
    assert gate.flush_turn_end(has_tool_calls=True) == []

    # Final synthesis turn: [C1] preserved, [C99] rewritten before SSE emission!
    gate.reset_for_turn(tools_enabled=False)
    emitted = gate.push_delta("Doanh thu FPT đạt 62,849 tỷ VND [C1] và biên ròng đạt 99% [C99]. ")
    emitted.extend(gate.flush_turn_end(has_tool_calls=False))
    joined = "".join(emitted)
    assert "[C1]" in joined
    assert "[C99 UNVERIFIED_CLAIM]" in joined
    assert "[C99]" not in joined


class _ParallelAgentModelProvider:
    """Real `sag_agent.types.ModelProvider` double testing PARALLEL tool execution & stream gating."""

    configured = True

    def __init__(self) -> None:
        self.turn = 0

    async def stream_turn(
        self,
        request: ModelRequest,
        cancellation: CancellationToken,
    ) -> AsyncIterator[ModelChunk]:
        self.turn += 1
        if self.turn == 1:
            # Emit short preamble, then early tool signal, then 3 parallel tool calls
            yield ModelChunk(text_delta="Để kiểm tra FPT tôi sẽ gọi công cụ. ")
            yield ModelChunk(has_pending_tool_call=True)
            yield ModelChunk(
                tool_calls=(
                    ToolCall(
                        id="tc-1",
                        name="search_corporate_filings",
                        arguments={
                            "tickers": ["FPT"],
                            "bm25_vas_queries": ["doanh thu thuần"],
                            "dense_semantic_queries": ["Doanh thu thuần FPT năm 2025"],
                            "top_k": 4,
                        },
                    ),
                    ToolCall(
                        id="tc-2",
                        name="hydrate_financial_tables",
                        arguments={"tickers": ["FPT"], "statement_kind": "IS"},
                    ),
                    ToolCall(
                        id="tc-3",
                        name="run_financial_calculator",
                        arguments={
                            "formula": "yoy(rev_2025, rev_2024)",
                            "description": "Tăng trưởng doanh thu FPT 2025",
                            "inputs": {"rev_2025": 62849.0, "rev_2024": 52618.0},
                            "format_style": "percent",
                        },
                    ),
                ),
                finish_reason="tool_calls",
                usage=Usage(input_tokens=400, output_tokens=90),
            )
            return

        # Turn 2: Final synthesis citing [C1], [TBL:tbl_fpt_is], [CALC-1] plus one hallucinated [C99]
        yield ModelChunk(
            text_delta=(
                "Doanh thu thuần FPT năm 2025 đạt 62,849 tỷ VND [C1] [TBL:tbl_fpt_is], "
                "tăng +19.44% so với năm 2024 [CALC-1]. Giả định chưa kiểm chứng [C99]."
            ),
            finish_reason="stop",
            usage=Usage(input_tokens=600, output_tokens=70),
        )


@pytest.mark.asyncio
async def test_unified_agent_parallel_tools_isolated_sessions_and_short_circuit_e2e(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "decision_provider", "disabled")

    # Concurrency detector: each session_factory() call yields a distinct object ID
    session_ids_created: list[int] = []
    active_sessions = 0
    max_concurrent_sessions = 0
    counter = 0

    @asynccontextmanager
    async def _isolated_session_factory() -> AsyncIterator[dict[str, int]]:
        nonlocal active_sessions, max_concurrent_sessions, counter
        counter += 1
        sid = counter
        session_ids_created.append(sid)
        active_sessions += 1
        max_concurrent_sessions = max(max_concurrent_sessions, active_sessions)
        try:
            await asyncio.sleep(0.02)
            yield {"session_id": sid}
        finally:
            active_sessions -= 1

    quote_text = "Doanh thu thuần hợp nhất FPT năm 2025 đạt 62,849 tỷ VND (năm 2024 là 52,618 tỷ VND)."

    class _StubRetriever:
        def __init__(self) -> None:
            self.calls = 0

        async def retrieve(self, **kwargs: Any) -> RetrievalResult:
            self.calls += 1
            await asyncio.sleep(0.03)
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="FPT",
                        doc_id="doc-fpt-2025",
                        document_version_id="ver-fpt-2025",
                        sub_doc_slot="S04_IS_CONSOL",
                        page_from=7,
                        page_to=7,
                        published_date=date(2026, 3, 20),
                        heading="Báo cáo KQKD FPT 2025",
                        content=quote_text,
                        score=0.92,
                    )
                ],
                canonical_blocks={
                    "ver-fpt-2025": [
                        CanonicalBlockRecord(
                            block_id="blk-fpt-1",
                            document_version_id="ver-fpt-2025",
                            sub_doc_slot="S04_IS_CONSOL",
                            page_from=7,
                            page_to=7,
                            normalized_text=quote_text,
                            published_date=date(2026, 3, 20),
                        )
                    ]
                },
            )

    class _StubTableStore:
        async def get_tables(self, **kwargs: Any) -> list[dict[str, Any]]:
            await asyncio.sleep(0.03)
            return [
                {
                    "table_id": "tbl_fpt_is",
                    "project_id": "FPT",
                    "title": "Báo cáo kết quả hoạt động kinh doanh FPT",
                    "sub_doc_slot": "S04_IS_CONSOL",
                    "page_from": 7,
                    "page_to": 7,
                    "currency": "VND",
                    "unit_raw": "tỷ VND",
                    "unit_multiplier": 1e9,
                    "verification_status": "VERIFIED",
                    "headers": [["Chỉ tiêu", "2025", "2024"]],
                    "rows": [{"label": "Doanh thu thuần", "values": [62849.0, 52618.0]}],
                }
            ]

    retriever = _StubRetriever()
    # Explicitly exercise the `agent_loop` runtime mode (`ToolExecutionMode.PARALLEL` contract)
    orch = ResearchChatOrchestrator(
        session=None,
        session_factory=_isolated_session_factory,
        retriever=retriever,
        table_store=_StubTableStore(),
        llm=_ParallelAgentModelProvider(),
        enable_agent_loop=True,
        brain_mode="agent_loop",
    )
    principal = VerifiedPrincipal(
        subject="u1",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="bff",
        key_id="k1",
        token_id="t1",
        issued_at=0,
        expires_at=9_999_999_999,
        tenant_id="aiinvest-default",
    )

    answer, meta, events = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(
                query="Doanh thu FPT 2025 tăng bao nhiêu %?",
                ticker="FPT",
                as_of_date=date(2026, 6, 30),
            ),
            principal=principal,
        )
    )

    # 1. Parallel isolated sessions proved (>= 2 distinct sessions ran concurrently!)
    assert len(session_ids_created) >= 2
    assert max_concurrent_sessions >= 2

    # 2. Tool preamble NEVER leaked to SSE delta events, and [C99] rewritten to [C99 UNVERIFIED_CLAIM]
    deltas_text = "".join(p.get("text", "") for ev, p in events if ev == "delta")
    assert "tôi sẽ gọi công cụ" not in deltas_text
    assert "[C99 UNVERIFIED_CLAIM]" in deltas_text

    # 2b. Live thinking, tool_call, and tool_result SSE events fired and persisted into activity_log
    thought_events = [p for ev, p in events if ev == "thought"]
    tool_call_events = [p for ev, p in events if ev == "tool_call"]
    tool_result_events = [p for ev, p in events if ev == "tool_result"]
    assert len(thought_events) >= 2
    assert len(tool_call_events) == 3
    assert len(tool_result_events) == 3
    search_res = next(p for p in tool_result_events if p.get("tool") == "search_corporate_filings")
    assert search_res["status"] == "done"
    assert len(search_res.get("selected_documents") or []) >= 1
    assert search_res["selected_documents"][0]["citation_id"] == "C1"
    assert len(meta.get("activity_log") or []) >= 5

    # 3. Stage 7 verified [C1], caught hallucinated [C99], and prepended inline warning banner
    assert answer.startswith("> ⚠️ **Cảnh báo kiểm chứng tự động (Stage 7):**")
    assert len(meta["citations"]) == 1
    assert meta["citations"][0]["verified"] is True
    assert len(meta["calculator_traces"]) == 1
    assert meta["calculator_traces"][0]["formatted"] == "19.44%"
    assert meta["telemetry"]["retrieval_mode"] == "UNIFIED_AGENT_TOOLS"
    assert meta["telemetry"]["tool_calls_count"] == 3
    assert len(meta["tool_traces"]) == 3

    # 4. Verify Tier-0 short-circuit skips retriever completely (0 calls) and marks eval_eligible=False
    async def _fake_short_circuit(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "domain_class": "chitchat_casual",
            "task_action": "direct_reply",
            "reasoning_budget_tier": "zero_tool",
            "is_short_circuit": True,
            "short_circuit_kind": "chitchat_casual",
            "eval_eligible": False,
            "confidence": 0.96,
        }

    monkeypatch.setattr("sag_api.services.decision_router.aroute_research_query", _fake_short_circuit)
    prev_calls = retriever.calls
    sc_ans, sc_meta, _sc_events = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(query="Xin chào bạn!", as_of_date=date(2026, 6, 30)),
            principal=principal,
        )
    )
    assert retriever.calls == prev_calls  # 0 Qdrant/PG retrieval calls!
    assert sc_meta["eval_eligible"] is False
    assert sc_meta["route_intent"] == "chitchat_casual"
    assert sc_meta["telemetry"]["retrieval_mode"] == "ZERO_DATA_SHORT_CIRCUIT"
    assert len(sc_ans) > 0


def test_sentence_stream_gate_extracts_think_and_strips_meta_preamble() -> None:
    gate = SentenceStreamGate(valid_cid_ceiling=lambda: 3, lookahead_chars=20)
    gate.reset_for_turn(tools_enabled=False)
    out = gate.push_delta("<think>Kiểm tra doanh thu FPT và kế hoạch ĐHĐCĐ.</think>\n")
    thoughts = gate.drain_extracted_thoughts()
    assert out == []
    assert len(thoughts) == 1
    assert "Kiểm tra doanh thu FPT" in thoughts[0]

    out2 = gate.push_delta("Tôi đã có đủ dữ liệu. Hãy tổng hợp:\nDoanh thu thuần FPT đạt 70.113 tỷ [C1]. ")
    out3 = gate.flush_turn_end(has_tool_calls=False)
    prose = "".join([*out2, *out3])
    assert "Tôi đã có đủ dữ liệu" not in prose
    assert "<think>" not in prose and "</think>" not in prose
    assert "70.113 tỷ [C1]" in prose


def test_decision_router_normalizes_clarify_request_when_tickers_or_clear() -> None:
    from sag_api.services.decision_router import _research_route_from_answers

    raw_answers = {
        "domain_class": {"choice": "stock_research_data", "confidence": 0.45},
        "task_action": {"choice": "clarify_request", "confidence": 0.41},
        "evidence_source_hint": {"choice": "filings_and_tables", "confidence": 0.70},
        "reasoning_budget_tier": {"choice": "standard_2_3_steps", "confidence": 0.65},
        "temporal_scope": {"choice": "specific_period", "confidence": 0.80},
        "ambiguity_and_guard": {"choice": "underspecified_ask_clarify", "confidence": 0.40},
        "output_style": {"choice": "executive_memo", "confidence": 0.60},
        "memory_recall_need": {"choice": "none", "confidence": 0.90},
    }
    routed = _research_route_from_answers(
        "Phân tích tổng tài sản và nợ xấu của CTG năm 2025",
        raw_answers,
        chat_mode="GENERAL",
        model="Kev-4b",
        latency_ms=120.0,
        session_state={"active_tickers": ["CTG"]},
    )
    assert routed["task_action"] == "fact_lookup"
    assert routed["ambiguity_and_guard"] == "clear"
    assert routed["max_agent_turns"] >= 3


def test_select_diverse_sections_fast_on_large_chunks_with_near_dedup() -> None:
    import time

    base_a = "Doanh thu thuần hợp nhất và biên lợi nhuận gộp mảng công nghệ thông tin toàn cầu " * 45
    base_b = "Dư nợ cho vay khách hàng và tỷ lệ bao phủ nợ xấu ngân hàng thương mại " * 45
    candidates = [
        RetrievedSection(
            rank=i,
            source_id="FPT" if i % 2 == 0 else "CTG",
            source_name="FPT" if i % 2 == 0 else "CTG",
            document_id=f"doc_{i % 6}",
            document_name=f"BCTC {i % 6}",
            heading=f"Muc {i}",
            content=(base_a if i % 2 == 0 else base_b) + f" chi tiet {i * 31}",
            score=0.95 - i * 0.004,
        )
        for i in range(120)
    ]
    # Add an exact near-duplicate of item 0
    candidates.append(candidates[0].model_copy(update={"rank": 120, "chunk_id": "dup_0", "score": 0.80}))
    t0 = time.perf_counter()
    selected, stats = select_diverse_sections(
        "So sánh doanh thu FPT và dư nợ CTG",
        candidates,
        limit=12,
        per_document_limit=4,
        min_per_project_quota=4,
    )
    elapsed_ms = (time.perf_counter() - t0) * 1000
    assert len(selected) == 12
    assert stats["deduplicated_count"] >= 1
    assert elapsed_ms < 800, f"select_diverse_sections too slow: {elapsed_ms:.1f}ms"
