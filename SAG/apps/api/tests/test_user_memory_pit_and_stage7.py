"""M2 (Tri-Temporal PIT), M3 (Clause-Scoped Stage 7), Adaptive Zero-Mem & KV-Cache Tests.

Covers:
- M2: All 4 Tri-Temporal PIT scenarios (live->backtest, backtest->live, hindsight preference
  2022->2021 forced to MARKET_BOUND, live Core DB `user_portfolio_snapshot` disabled 100% in backtest).
- M3: Clause-Scoped numeric partitioning passing legitimate `[MEM:M1]` + `[SCR:FPT]`, BLOCKING
  numeric laundering (`"Theo tiêu chí P/E < 15x [MEM:M1], FPT có P/E 15x [SCR:FPT]"`), no-comma
  laundering, mixed-badge laundering, scale laundering (`125.000đ` -> `125 tỷ`), thesis numeric
  injection, `_claim_content_overlaps_section` non-bypass, SSE `SentenceStreamGate` blocking
  `[MEM:M99]`/`[PORT:XYZ]`/`[SESS:S99]`, and `HALLUCINATED_MEMORY_ID` telemetry/banner.
- Chặng 0: Adaptive Zero-Mem token reduction (>= 75% cut & 0 old answer prose on topic shift),
  `LLMClient.stream_turn()` reading both `prompt_tokens_details.cached_tokens` &
  `prompt_cache_hit_tokens`, discounted `cost_usd`, shadow & incognito modes, and 8-axis R0 router.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.types import CancellationToken, ModelChunk, ModelRequest, ToolCall, Usage
from sag_api.core.config import settings
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.generation.llm import LLMClient
from sag_api.research.agent_runner import SentenceStreamGate
from sag_api.research.chat_orchestrator import (
    ResearchChatOrchestrator,
    RetrievalResult,
    RetrievedResearchSection,
    _compute_discounted_llm_cost_usd,
    _prepend_verification_warning_banner,
    collect_stream_result,
)
from sag_api.research.quote_verifier import (
    CanonicalBlockRecord,
    _claim_content_overlaps_section,
    compute_quote_hash,
    verify_citations,
    verify_numeric_claims_against_evidence,
)
from sag_api.research.session_memory import (
    build_financial_zeromem_ledger,
    render_session_context_block,
    should_include_recent_pairs,
)
from sag_api.research.user_memory_pipeline import (
    ExtractedTurnBundle,
    SanitizedMemoryCandidate,
    compute_content_hash,
    process_ingest_turn_payload,
    sanitize_extracted_candidate,
)
from sag_api.research.user_memory_read import smart_read_user_memories
from sag_api.schemas.research_chat import (
    DeclaredBlindspotItem,
    PortfolioPositionSnapshot,
    ResearchChatStreamRequest,
    UserPortfolioSnapshot,
)
from sag_api.services.decision_router import _research_cache_key, aroute_research_query


def _make_principal(user_id: str = "u-pit-1") -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject=user_id,
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-1",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


@pytest.mark.asyncio
async def test_m2_all_four_tri_temporal_pit_scenarios(tmp_path: Path) -> None:
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm2_pit.db'}"
    eng = create_async_engine(db_url, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    today = date(2026, 6, 30)

    async with sf() as s:
        # 1) Seed LIVE memories at 2026-06-30:
        #    - STYLE_PREF_SAFE (ATEMPORAL_FORMAT) -> exempt in backtest
        #    - USER_INVESTING_CRITERIA (MARKET_BOUND) -> BLOCKED in backtest
        #    - INVESTMENT_THESIS (MARKET_BOUND) -> BLOCKED in backtest
        live_bundle = ExtractedTurnBundle(
            memories=[
                SanitizedMemoryCandidate(
                    memory_type="STYLE_PREF_SAFE",
                    pit_sensitivity="ATEMPORAL_FORMAT",
                    scope="USER_GLOBAL",
                    content_text="Luôn trình bày ngắn gọn tối đa 3 gạch đầu dòng tóm tắt",
                    content_hash=compute_content_hash("Luôn trình bày ngắn gọn tối đa 3 gạch đầu dòng tóm tắt"),
                    confidence=0.95,
                ),
                SanitizedMemoryCandidate(
                    memory_type="USER_INVESTING_CRITERIA",
                    pit_sensitivity="MARKET_BOUND",
                    scope="USER_GLOBAL",
                    content_text="Chỉ lọc P/E < 15x và ROE > 15% năm 2026",
                    content_hash=compute_content_hash("Chỉ lọc P/E < 15x và ROE > 15% năm 2026"),
                    confidence=0.92,
                ),
                SanitizedMemoryCandidate(
                    memory_type="INVESTMENT_THESIS",
                    pit_sensitivity="MARKET_BOUND",
                    scope="TICKER_SCOPED",
                    content_text="Kỳ vọng mảng AI Factory của FPT năm 2026 thúc đẩy tăng trưởng",
                    content_hash=compute_content_hash("Kỳ vọng mảng AI Factory của FPT năm 2026 thúc đẩy tăng trưởng"),
                    tickers=["FPT"],
                    confidence=0.90,
                ),
            ],
            episode_summary="Phiên live 2026 phân tích AI Factory FPT",
            tickers=["FPT"],
        )
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="u-pit",
            payload={
                "session_id": "sess-live-2026",
                "user_content": "Trình bày 3 gạch đầu dòng, tôi lọc P/E < 15x và kỳ vọng AI Factory FPT",
                "assistant_topic_outline": "Phân tích FPT",
                "assistant_clean_for_digest": True,
                "turn_as_of_date": "2026-06-30",
                "is_backtest_sandbox": False,
                "tickers": ["FPT"],
            },
            extracted_override=live_bundle,
        )

        # 2) Seed BACKTEST SANDBOX memory at 2022-11-15 with hindsight preference ("Sau vụ SCB 2022 tránh bank")
        #    Even if extractor mislabels it as STYLE_PREF_SAFE, sanitizer MUST downgrade to MARKET_BOUND!
        hindsight_cand, err = sanitize_extracted_candidate(
            {
                "memory_type": "STYLE_PREF_SAFE",
                "modality": "ASSERTED",
                "content_text": "Sau vụ SCB 2022 tôi tránh hoàn toàn nhóm ngân hàng",
                "confidence": 0.94,
            },
            user_turn_text="Sau vụ SCB 2022 tôi tránh hoàn toàn nhóm ngân hàng",
        )
        assert err is None and hindsight_cand is not None
        assert hindsight_cand.memory_type == "USER_INVESTING_CRITERIA"
        assert hindsight_cand.pit_sensitivity == "MARKET_BOUND"

        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="u-pit",
            payload={
                "session_id": "sess-bt-2022",
                "user_content": "Sau vụ SCB 2022 tôi tránh hoàn toàn nhóm ngân hàng",
                "assistant_topic_outline": "Thảo luận nhóm ngân hàng 2022",
                "assistant_clean_for_digest": True,
                "turn_as_of_date": "2022-11-15",
                "is_backtest_sandbox": True,
                "tickers": ["VCB"],
            },
            extracted_override=ExtractedTurnBundle(
                memories=[hindsight_cand],
                episode_summary="Phiên backtest 2022 về sự kiện SCB",
                tickers=["VCB"],
            ),
        )

    live_snapshot = UserPortfolioSnapshot(
        as_of_timestamp="2026-06-30T09:00:00Z",
        positions=[PortfolioPositionSnapshot(symbol="FPT", quantity=5000, avg_cost=110000, weight_pct=35.0)],
        watchlists=["FPT", "HPG"],
    )

    async with sf() as s:
        # Scenario 1 (Live -> Backtest at 2023-06-30) + Scenario 4 (Core DB snapshot disabled in backtest):
        bt_2023 = await smart_read_user_memories(
            s,
            tenant_id="aiinvest-default",
            user_id="u-pit",
            query="Phân tích FPT và nhóm ngân hàng",
            as_of_date=date(2023, 6, 30),
            memory_recall_need="user_profile_light",
            explicit_tickers=["FPT"],
            user_memory_mode="on",
            user_portfolio_snapshot=live_snapshot,
            reference_today=today,
        )
        texts_2023 = [m.content_text for m in bt_2023.recalled_memories]
        assert any("3 gạch đầu dòng" in t for t in texts_2023), "Pure STYLE_PREF_SAFE must remain allowed"
        assert not any("AI Factory" in t for t in texts_2023), "Live 2026 thesis leaked into 2023 backtest!"
        assert not any("năm 2026" in t for t in texts_2023), "Live 2026 criteria leaked into 2023 backtest!"
        assert any(
            "SCB 2022" in t for t in texts_2023
        ), "Sandbox 2022 memory <= 2023-06-30 should be visible in 2023 sandbox"
        assert bt_2023.portfolio_snapshot_used is False
        assert bt_2023.portfolio_items == []
        assert "[PORT:FPT]" not in bt_2023.tier2_prompt_block

        # Scenario 3 (Hindsight Preference 2022 -> Backtest 2021-06-30): 0% leak!
        bt_2021 = await smart_read_user_memories(
            s,
            tenant_id="aiinvest-default",
            user_id="u-pit",
            query="Phân tích VCB và nhóm ngân hàng",
            as_of_date=date(2021, 6, 30),
            memory_recall_need="cross_session_graph",
            explicit_tickers=["VCB"],
            user_memory_mode="on",
            user_portfolio_snapshot=live_snapshot,
            reference_today=today,
        )
        texts_2021 = [m.content_text for m in bt_2021.recalled_memories]
        assert not any("SCB 2022" in t for t in texts_2021), "Hindsight 2022 preference leaked into 2021 backtest!"
        assert all(ep.session_id != "sess-bt-2022" for ep in bt_2021.linked_sessions)
        assert bt_2021.portfolio_snapshot_used is False

        # Scenario 2 (Backtest -> Live at 2026-06-30): 0% sandbox leak, live portfolio snapshot ENABLED!
        live_2026 = await smart_read_user_memories(
            s,
            tenant_id="aiinvest-default",
            user_id="u-pit",
            query="Phân tích FPT và danh mục của tôi",
            as_of_date=today,
            memory_recall_need="user_profile_light",
            explicit_tickers=["FPT"],
            user_memory_mode="on",
            user_portfolio_snapshot=live_snapshot,
            reference_today=today,
        )
        texts_live = [m.content_text for m in live_2026.recalled_memories]
        assert not any("SCB 2022" in t for t in texts_live), "Backtest sandbox memory leaked into live profile!"
        assert any("AI Factory" in t for t in texts_live)
        assert live_2026.portfolio_snapshot_used is True
        assert "FPT" in live_2026.portfolio_by_symbol
        assert "[PORT:FPT]" in live_2026.tier2_prompt_block

    await eng.dispose()


def test_m3_clause_scoped_stage7_blocks_numeric_laundering_and_passes_legitimate() -> None:
    screening = [{"ticker": "FPT", "fiscal_year": 2025, "pe": 22.4, "roe": 28.0}]
    sections = {
        "C1": RetrievedResearchSection(
            ticker="FPT",
            doc_id="d1",
            document_version_id="v1",
            sub_doc_slot="BCTC_AUDITED",
            page_from=5,
            page_to=5,
            published_date=date(2026, 3, 20),
            heading="KQKD FPT 2025",
            content="Doanh thu thuần FPT năm 2025 đạt 62.800 tỷ VND.",
            score=0.92,
        )
    }
    memories = {
        "M1": {
            "badge_id": "M1",
            "memory_type": "USER_INVESTING_CRITERIA",
            "content_text": "Chỉ lọc P/E < 15x và ROE > 15%",
        },
        "M2": {
            "badge_id": "M2",
            "memory_type": "USER_EXTERNAL_HOLDING",
            "content_text": "Bên SSI đang giữ 10.000 FPT giá vốn 125.000đ",
        },
        "M3": {
            "badge_id": "M3",
            "memory_type": "INVESTMENT_THESIS",
            "content_text": "Lo ngại cạnh tranh biên gộp FPT",
        },
    }
    portfolio = {
        "FPT": {"symbol": "FPT", "quantity": 5000, "avg_cost": 110000, "weight_pct": 25.0}
    }

    # 1) Legitimate sentence: [MEM:M1] has 15x, [SCR:FPT] has 22.4x -> PASS!
    legit = verify_numeric_claims_against_evidence(
        "Theo tiêu chí P/E < 15x [MEM:M1], FPT hiện có P/E 22.4x [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
        portfolio_snapshots=portfolio,
    )
    assert legit.unsupported_claims == []
    assert legit.uncited_claims == []

    # 2) Numeric Laundering Attack WITH comma: trying to launder 15x from M1 onto SCR:FPT -> BLOCKED!
    attack_comma = verify_numeric_claims_against_evidence(
        "Theo tiêu chí P/E < 15x [MEM:M1], FPT có P/E 15x [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
        portfolio_snapshots=portfolio,
    )
    assert len(attack_comma.unsupported_claims) >= 1
    assert any(b["code"] == "UNSUPPORTED_NUMERIC_CLAIM" for b in attack_comma.blindspots)

    # 3) Numeric Laundering Attack WITHOUT comma (badge boundary): -> BLOCKED!
    attack_nocomma = verify_numeric_claims_against_evidence(
        "Theo tiêu chí P/E < 15x [MEM:M1] nên FPT có P/E 15x [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
        portfolio_snapshots=portfolio,
    )
    assert len(attack_nocomma.unsupported_claims) >= 1

    # 4) Mixed badges on same clause: financial badge takes strict precedence -> BLOCKED!
    attack_mixed = verify_numeric_claims_against_evidence(
        "FPT có P/E 15x [MEM:M1] [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
        portfolio_snapshots=portfolio,
    )
    assert len(attack_mixed.unsupported_claims) >= 1

    # 5) Scale Laundering Attack: 125.000đ in M2 MUST NOT validate 125 tỷ in [C1] OR in [MEM:M2]!
    attack_scale_c1 = verify_numeric_claims_against_evidence(
        "Theo giá vốn 125.000đ [MEM:M2], doanh thu FPT đạt 125 tỷ VND [C1].",
        sections_by_cid=sections,
        recalled_memories=memories,
    )
    assert len(attack_scale_c1.unsupported_claims) >= 1

    attack_scale_same = verify_numeric_claims_against_evidence(
        "Bên SSI có giá vốn 125 tỷ VND [MEM:M2].",
        sections_by_cid=sections,
        recalled_memories=memories,
    )
    assert len(attack_scale_same.unsupported_claims) >= 1

    # 6) INVESTMENT_THESIS (M3) MUST NOT authorize invented corporate numbers -> BLOCKED!
    attack_thesis = verify_numeric_claims_against_evidence(
        "Doanh thu FPT đạt 99.000 tỷ VND [MEM:M3].",
        sections_by_cid=sections,
        recalled_memories=memories,
    )
    assert len(attack_thesis.unsupported_claims) >= 1

    # 7) Legitimate [PORT:FPT] + [SCR:FPT] PASS; fabricated PORT quantity BLOCKED
    legit_port = verify_numeric_claims_against_evidence(
        "Danh mục của bạn giữ 5.000 cổ phiếu FPT giá vốn 110.000 VND [PORT:FPT], với ROE đạt 28% [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        portfolio_snapshots=portfolio,
    )
    assert legit_port.unsupported_claims == []

    bad_port = verify_numeric_claims_against_evidence(
        "Danh mục của bạn giữ 99.000 cổ phiếu FPT [PORT:FPT].",
        sections_by_cid=sections,
        portfolio_snapshots=portfolio,
    )
    assert len(bad_port.unsupported_claims) >= 1

    # 8) `_claim_content_overlaps_section` MUST NOT bypass for `[MEM:M1]`!
    assert (
        _claim_content_overlaps_section(
            ["Chuyến du lịch biển mùa hè tuyệt đẹp [MEM:M1] [C1]."],
            "Doanh thu thuần FPT năm 2025 đạt 62.800 tỷ VND.",
        )
        is False
    )

    # 9) Adversarial Parenthetical / Dash / Colon / Leading-Badge / Uncited Clause Partitioning!
    # Parenthetical laundering MUST BE BLOCKED; legitimate parenthetical MUST PASS
    paren_attack = verify_numeric_claims_against_evidence(
        "FPT có P/E 15x (theo tiêu chí < 15x [MEM:M1]) [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert len(paren_attack.unsupported_claims) >= 1

    paren_legit = verify_numeric_claims_against_evidence(
        "FPT có P/E 22.4x (theo tiêu chí < 15x [MEM:M1]) [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert paren_legit.unsupported_claims == []
    assert paren_legit.uncited_claims == []

    paren_mid_attack = verify_numeric_claims_against_evidence(
        "Doanh thu FPT đạt 15x (tiêu chí < 15x [MEM:M1]) và ROE 28% [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert len(paren_mid_attack.unsupported_claims) >= 1

    dash_attack = verify_numeric_claims_against_evidence(
        "FPT có P/E 15x - theo tiêu chí P/E < 15x [MEM:M1] - so với ngành [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert len(dash_attack.unsupported_claims) >= 1

    # Leading attribution badge: legitimate criterion PASSES, corporate figure BLOCKED against M1
    lead_legit = verify_numeric_claims_against_evidence(
        "Theo [MEM:M1], tiêu chí P/E < 15x.",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert lead_legit.unsupported_claims == []
    assert lead_legit.uncited_claims == []

    colon_legit = verify_numeric_claims_against_evidence(
        "Tiêu chí [MEM:M1]: P/E < 15x; FPT hiện có P/E 22.4x [SCR:FPT].",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert colon_legit.unsupported_claims == []
    assert colon_legit.uncited_claims == []

    lead_attack = verify_numeric_claims_against_evidence(
        "Theo [MEM:M1], doanh thu FPT đạt 62.800 tỷ VND.",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert len(lead_attack.unsupported_claims) >= 1

    # Uncited corporate figure in a separate clause after [MEM:M1] MUST trigger UNCITED_NUMERIC_CLAIM!
    uncited_after_mem = verify_numeric_claims_against_evidence(
        "Theo tiêu chí P/E < 15x [MEM:M1], doanh thu FPT đạt 62.800 tỷ VND.",
        sections_by_cid=sections,
        screening_results=screening,
        recalled_memories=memories,
    )
    assert len(uncited_after_mem.uncited_claims) >= 1
    assert any(b["code"] == "UNCITED_NUMERIC_CLAIM" for b in uncited_after_mem.blindspots)


def test_m3_sentence_stream_gate_and_verify_citations_block_hallucinated_memory_ids() -> None:
    gate = SentenceStreamGate(
        valid_cid_ceiling=lambda: 1,
        valid_memory_ids=lambda: {"M1"},
        valid_portfolio_symbols=lambda: {"FPT"},
        valid_session_ids=lambda: {"S1"},
        lookahead_chars=0,
    )
    gate.reset_for_turn(tools_enabled=False)
    chunks = gate.push_delta(
        "Hợp lệ [MEM:M1] [PORT:FPT] [SESS:S1]. Giả mạo [MEM:M99] [PORT:XYZ] [SESS:S88]. "
    )
    chunks.extend(gate.flush_turn_end(has_tool_calls=False))
    streamed = "".join(chunks)
    assert "[MEM:M1]" in streamed
    assert "[PORT:FPT]" in streamed
    assert "[SESS:S1]" in streamed
    assert "[MEM:M99 UNVERIFIED_CLAIM]" in streamed
    assert "[PORT:XYZ UNVERIFIED_CLAIM]" in streamed
    assert "[SESS:S88 UNVERIFIED_CLAIM]" in streamed

    quote = "Doanh thu thuần FPT năm 2025 đạt 62.800 tỷ VND."
    blocks = {
        "v1": [
            CanonicalBlockRecord(
                document_version_id="v1",
                page_from=5,
                page_to=5,
                normalized_text=quote,
                sub_doc_slot="BCTC_AUDITED",
                published_date=date(2026, 3, 20),
            )
        ]
    }
    res = verify_citations(
        [
            {
                "citation_id": "C1",
                "doc_id": "d1",
                "document_version_id": "v1",
                "sub_doc_slot": "BCTC_AUDITED",
                "page": 5,
                "published_date": "2026-03-20",
                "quote": quote,
                "quote_hash": compute_quote_hash(quote),
            }
        ],
        blocks,
        date(2026, 6, 30),
        answer_text="FPT đạt 62.800 tỷ [C1], giả mạo [MEM:M99] và [PORT:XYZ].",
        valid_citation_ids={"C1"},
        valid_memory_ids={"M1"},
        valid_portfolio_symbols={"FPT"},
        valid_session_ids=set(),
    )
    mem_bs = [b for b in res.blindspots if b["code"] == "HALLUCINATED_MEMORY_ID"]
    assert len(mem_bs) == 2
    assert "[MEM:M99 UNVERIFIED_CLAIM]" in res.sanitized_answer
    assert "[PORT:XYZ UNVERIFIED_CLAIM]" in res.sanitized_answer

    bannered = _prepend_verification_warning_banner(
        res.sanitized_answer,
        [DeclaredBlindspotItem(**b) for b in res.blindspots],
    )
    assert bannered.startswith("> ⚠️")
    assert "[MEM]/[PORT]/[SESS]" in bannered


def test_adaptive_zeromem_cuts_tokens_on_topic_shift_and_preserves_followup() -> None:
    history = [
        {"role": "user", "content": "Phân tích chi tiết doanh thu và nợ vay FPT", "turn_as_of_date": "2026-06-30"},
        {
            "role": "assistant",
            "content": "FPT ghi nhận doanh thu 62.800 tỷ VND trong FY2025. " + ("Chi tiết phân tích FPT. " * 40),
            "turn_as_of_date": "2026-06-30",
            "primary_ticker": "FPT",
            "expanded_tickers": ["FPT"],
        },
        {"role": "user", "content": "Cơ cấu dòng tiền CFO của FPT ra sao?", "turn_as_of_date": "2026-06-30"},
        {
            "role": "assistant",
            "content": "CFO của FPT đạt 11.200 tỷ VND. " + ("Giải thích dòng tiền FPT. " * 40),
            "turn_as_of_date": "2026-06-30",
            "primary_ticker": "FPT",
            "expanded_tickers": ["FPT"],
        },
    ]
    ledger = build_financial_zeromem_ledger(
        history,
        request_as_of_date=date(2026, 6, 30),
        reference_today=date(2026, 6, 30),
    )

    # Disjoint new ticker HPG -> topic shift -> include_recent_pairs == False
    assert should_include_recent_pairs(query="Đánh giá sản lượng HRC của HPG năm 2025", ledger=ledger) is False
    full_block = render_session_context_block(ledger, include_recent_pairs=True)
    compact_block = render_session_context_block(ledger, include_recent_pairs=False)

    assert "62.800 tỷ" in full_block
    assert "62.800 tỷ" not in compact_block
    assert "11.200 tỷ" not in compact_block
    assert "FPT" in compact_block  # State pointer retains active/all tickers
    assert len(compact_block) < len(full_block) * 0.25  # > 75% reduction!
    # Deep recall still recovers verbatim FPT turns on demand!
    assert len(ledger.recall(tickers=["FPT"])["recalled_turns"]) == 2

    # Follow-up coreference -> include_recent_pairs == True
    assert should_include_recent_pairs(query="Còn biên gộp của mã đó thì sao?", ledger=ledger) is True
    assert should_include_recent_pairs(query="So sánh thêm nợ vay FPT với năm trước", ledger=ledger) is True


@pytest.mark.asyncio
async def test_llm_cached_tokens_both_formats_and_discounted_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    s_cfg = settings.model_copy(
        update={
            "llm_provider": "openai",
            "llm_api_key": "test-key",
            "llm_model": "deepseek-ai/DeepSeek-V4-Flash-0731",
        }
    )
    client = LLMClient(s_cfg)

    async def _fake_stream_prompt_cache_hit(**_kwargs: Any):
        async def _gen():
            yield {
                "choices": [{"delta": {"content": "Hello"}, "finish_reason": None}],
                "usage": {
                    "prompt_tokens": 2000,
                    "completion_tokens": 200,
                    "prompt_cache_hit_tokens": 1500,
                },
            }
            yield {"choices": [{"delta": {}, "finish_reason": "stop"}]}

        return _gen()

    monkeypatch.setattr("sag_api.generation.llm._litellm_completion", _fake_stream_prompt_cache_hit)
    from sag_agent import AgentMessage

    usages: list[Usage] = []
    async for chunk in client.stream_turn(
        ModelRequest(messages=(AgentMessage(role="user", content="hi"),)),
        CancellationToken(),
    ):
        if chunk.usage is not None:
            usages.append(chunk.usage)
    assert usages and usages[-1].cached_tokens == 1500

    # Also verify OpenAI `prompt_tokens_details.cached_tokens` format
    async def _fake_stream_details(**_kwargs: Any):
        async def _gen():
            yield {
                "choices": [{"delta": {"content": "Hi"}, "finish_reason": "stop"}],
                "usage": {
                    "prompt_tokens": 1000,
                    "completion_tokens": 100,
                    "prompt_tokens_details": {"cached_tokens": 800},
                },
            }

        return _gen()

    monkeypatch.setattr("sag_api.generation.llm._litellm_completion", _fake_stream_details)
    usages2: list[Usage] = []
    async for chunk in client.stream_turn(
        ModelRequest(messages=(AgentMessage(role="user", content="hi"),)),
        CancellationToken(),
    ):
        if chunk.usage is not None:
            usages2.append(chunk.usage)
    assert usages2 and usages2[-1].cached_tokens == 800

    # Verify discounted cost calculation:
    # DeepSeek-V4-Flash: input=$0.10/1M, cached=$0.025/1M, output=$0.30/1M
    # 2000 input (1500 cached + 500 uncached) + 200 output:
    # (500 * 0.10 + 1500 * 0.025 + 200 * 0.30) / 1e6 = (50 + 37.5 + 60) / 1e6 = 0.0001475
    cost = _compute_discounted_llm_cost_usd(
        "deepseek-ai/DeepSeek-V4-Flash-0731",
        input_tokens=2000,
        cached_tokens=1500,
        output_tokens=200,
    )
    assert cost == pytest.approx(0.0001475, rel=1e-6)


@pytest.mark.asyncio
async def test_r0_eighth_axis_and_orchestrator_shadow_vs_incognito_and_cached_cost(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Cache key MUST change when `last_turn_summary` or `has_user_memories` changes
    k1 = _research_cache_key(
        model="Kev-4b",
        query="Phân tích FPT",
        chat_mode="GENERAL",
        active_tickers=("FPT",),
        has_prior_turns=True,
        last_turn_summary="Q=FPT 2025",
        has_user_memories=False,
    )
    k2 = _research_cache_key(
        model="Kev-4b",
        query="Phân tích FPT",
        chat_mode="GENERAL",
        active_tickers=("FPT",),
        has_prior_turns=True,
        last_turn_summary="Q=HPG 2025",
        has_user_memories=False,
    )
    k3 = _research_cache_key(
        model="Kev-4b",
        query="Phân tích FPT",
        chat_mode="GENERAL",
        active_tickers=("FPT",),
        has_prior_turns=True,
        last_turn_summary="Q=FPT 2025",
        has_user_memories=True,
    )
    assert k1 != k2 and k1 != k3

    # Deterministic fallback when decision_provider == "disabled"
    monkeypatch.setattr(settings, "decision_provider", "disabled")
    r_cross = await aroute_research_query("Phiên trước tôi phân tích luận điểm FPT thế nào?")
    assert r_cross["memory_recall_need"] == "cross_session_graph"
    r_prof = await aroute_research_query("Lọc cổ phiếu theo tiêu chí của tôi")
    assert r_prof["memory_recall_need"] == "user_profile_light"
    r_none = await aroute_research_query("Doanh thu HPG năm 2025?")
    assert r_none["memory_recall_need"] == "none"

    # 8th axis from SiliconFlow MockTransport
    monkeypatch.setattr(settings, "decision_provider", "siliconflow")
    monkeypatch.setattr(settings, "siliconflow_api_key", "sf-test-key")

    def _sf_8axis(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content.decode("utf-8"))
        assert "memory_recall_need" in body["questions"]
        return httpx.Response(
            200,
            json={
                "answers": {
                    "domain_class": {"choice": "stock_research_data", "confidence": 0.95},
                    "task_action": {"choice": "fact_lookup", "confidence": 0.90},
                    "evidence_source_hint": {"choice": "filings_and_tables", "confidence": 0.90},
                    "reasoning_budget_tier": {"choice": "light_1_2_steps", "confidence": 0.90},
                    "temporal_scope": {"choice": "latest_pit", "confidence": 0.90},
                    "ambiguity_and_guard": {"choice": "clear", "confidence": 0.90},
                    "output_style": {"choice": "fact_citation", "confidence": 0.90},
                    "memory_recall_need": {"choice": "cross_session_graph", "confidence": 0.93},
                }
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(_sf_8axis)) as sf_client:
        r_8 = await aroute_research_query("Nhớ lại luận điểm FPT", http_client=sf_client)
        assert r_8["memory_recall_need"] == "cross_session_graph"

    # Orchestrator Unified Agent E2E: verify shadow mode suppresses Tier 2 prompt while recording telemetry,
    # incognito skips read, and cached_tokens is populated in telemetry!
    monkeypatch.setattr(settings, "decision_provider", "disabled")
    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm2_orch.db'}"
    eng = create_async_engine(db_url, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    async with sf() as s:
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="u-pit-1",
            payload={
                "session_id": "s-prev",
                "user_content": "Tiêu chí của tôi là P/E < 15x",
                "assistant_topic_outline": "Ghi nhận tiêu chí",
                "assistant_clean_for_digest": True,
                "turn_as_of_date": date.today().isoformat(),
                "is_backtest_sandbox": False,
                "tickers": ["FPT"],
            },
            extracted_override=ExtractedTurnBundle(
                memories=[
                    SanitizedMemoryCandidate(
                        memory_type="USER_INVESTING_CRITERIA",
                        pit_sensitivity="MARKET_BOUND",
                        scope="USER_GLOBAL",
                        content_text="Tiêu chí lọc P/E < 15x",
                        content_hash=compute_content_hash("Tiêu chí lọc P/E < 15x"),
                        confidence=0.95,
                    )
                ],
                tickers=["FPT"],
            ),
        )

    captured_prompts: list[str] = []

    class _StubModelProvider:
        configured = True

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            if request.turn == 1:
                for m in request.messages:
                    if m.role == "user":
                        captured_prompts.append(str(m.content))
                yield ModelChunk(
                    usage=Usage(input_tokens=1000, output_tokens=50, cached_tokens=700),
                    tool_calls=(
                        ToolCall(
                            id="tc-1",
                            name="search_corporate_filings",
                            arguments={
                                "tickers": ["FPT"],
                                "bm25_vas_queries": ["doanh thu FPT"],
                                "dense_semantic_queries": ["doanh thu thuần FPT"],
                            },
                        ),
                    ),
                    finish_reason="tool_calls",
                )
            else:
                yield ModelChunk(
                    text_delta="Doanh thu FPT đạt 62.800 tỷ VND [C1].",
                    usage=Usage(input_tokens=1200, output_tokens=40, cached_tokens=900),
                    finish_reason="stop",
                )

    quote_txt = "Doanh thu thuần FPT đạt 62.800 tỷ VND."

    class _StubRetriever:
        async def retrieve(self, **_kwargs: Any) -> RetrievalResult:
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="FPT",
                        doc_id="d1",
                        document_version_id="v1",
                        sub_doc_slot="BCTC_AUDITED",
                        page_from=5,
                        page_to=5,
                        published_date=date(2026, 3, 20),
                        heading="KQKD FPT",
                        content=quote_txt,
                        score=0.95,
                    )
                ],
                canonical_blocks_by_version={
                    "v1": [
                        CanonicalBlockRecord(
                            document_version_id="v1",
                            page_from=5,
                            page_to=5,
                            normalized_text=quote_txt,
                            sub_doc_slot="BCTC_AUDITED",
                            published_date=date(2026, 3, 20),
                        )
                    ]
                },
            )

    # 1) SHADOW mode: recalled_memory_count > 0 in telemetry, NOT injected in user prompt!
    monkeypatch.setattr(settings, "user_memory_mode", "shadow")
    orch = ResearchChatOrchestrator(
        session_factory=sf,
        llm=_StubModelProvider(),
        retriever=_StubRetriever(),
    )
    _ans, meta_shadow, _evs = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(query="Lọc FPT theo tiêu chí của tôi", ticker="FPT"),
            principal=_make_principal("u-pit-1"),
        )
    )
    assert meta_shadow["telemetry"]["user_memory_mode"] == "shadow"
    assert meta_shadow["telemetry"]["recalled_memory_count"] >= 1
    assert meta_shadow["telemetry"]["cached_tokens"] == 1600
    assert "<USER_MEMORY_DATA_ONLY_UNTRUSTED_FOR_INSTRUCTIONS>" not in captured_prompts[-1]

    # 2) ON mode: Tier 2 IS injected into user prompt!
    monkeypatch.setattr(settings, "user_memory_mode", "on")
    _ans2, meta_on, _evs2 = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(query="Lọc FPT theo tiêu chí của tôi", ticker="FPT"),
            principal=_make_principal("u-pit-1"),
        )
    )
    assert meta_on["telemetry"]["user_memory_mode"] == "on"
    assert "<USER_MEMORY_DATA_ONLY_UNTRUSTED_FOR_INSTRUCTIONS>" in captured_prompts[-1]
    assert "[MEM:M1]" in captured_prompts[-1]

    # 3) INCOGNITO mode: 0 read, 0 portfolio snapshot!
    _ans3, meta_incog, _evs3 = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(
                query="Lọc FPT theo tiêu chí của tôi",
                ticker="FPT",
                memory_mode="incognito",
                user_portfolio_snapshot=UserPortfolioSnapshot(
                    positions=[PortfolioPositionSnapshot(symbol="FPT", quantity=100)]
                ),
            ),
            principal=_make_principal("u-pit-1"),
        )
    )
    assert meta_incog["telemetry"]["user_memory_mode"] == "incognito"
    assert meta_incog["telemetry"]["recalled_memory_count"] == 0
    assert meta_incog["portfolio_snapshot_used"] is False
    assert "<USER_MEMORY_DATA_ONLY_UNTRUSTED_FOR_INSTRUCTIONS>" not in captured_prompts[-1]

    await eng.dispose()


@pytest.mark.asyncio
async def test_relevance_floor_gate_before_normalized_rrf_and_short_circuit_cross_session_recall(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sag_api.research.user_memory_read import compute_normalized_memory_score

    # Verify Normalized RRF prevents 10x Recency dominance:
    # A top-ranked RRF hit gets full 0.50 weight (not 0.016 unnormalized!)
    s_top = compute_normalized_memory_score(
        rrf=2.0 / 61.0,
        rrf_max=2.0 / 61.0,
        graph_overlap=0.0,
        age_days=60.0,
        memory_type="INVESTMENT_THESIS",
        reinforcement_count=1,
        pinned_by_user=False,
    )
    assert s_top >= 0.55

    db_url = f"sqlite+aiosqlite:///{tmp_path / 'm2_rrf.db'}"
    eng = create_async_engine(db_url, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    today = date.today()
    async with sf() as s:
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="u-rrf",
            payload={
                "session_id": "sess-old-rrf",
                "user_content": "Tôi lo ngại biên gộp FPT chịu áp lực chi phí AI",
                "assistant_topic_outline": "Phân tích FPT",
                "assistant_clean_for_digest": True,
                "turn_as_of_date": today.isoformat(),
                "is_backtest_sandbox": False,
                "tickers": ["FPT"],
            },
            extracted_override=ExtractedTurnBundle(
                memories=[
                    SanitizedMemoryCandidate(
                        memory_type="INVESTMENT_THESIS",
                        pit_sensitivity="MARKET_BOUND",
                        scope="TICKER_SCOPED",
                        content_text="Lo ngại biên gộp FPT chịu áp lực chi phí AI",
                        content_hash=compute_content_hash("Lo ngại biên gộp FPT chịu áp lực chi phí AI"),
                        tickers=["FPT"],
                        confidence=0.92,
                    ),
                    SanitizedMemoryCandidate(
                        memory_type="INVESTMENT_THESIS",
                        pit_sensitivity="MARKET_BOUND",
                        scope="TICKER_SCOPED",
                        content_text="Theo dõi sản lượng cảng biển GMD",
                        content_hash=compute_content_hash("Theo dõi sản lượng cảng biển GMD"),
                        tickers=["GMD"],
                        confidence=0.90,
                    ),
                ],
                episode_summary="Phiên trước đánh giá áp lực chi phí AI lên biên gộp FPT",
                tickers=["FPT"],
            ),
        )
        from sqlalchemy import select as sa_select

        from sag_api.db.models.user_memory import UserMemoryItem

        items = (
            await s.scalars(
                sa_select(UserMemoryItem)
                .where(UserMemoryItem.user_id == "u-rrf")
                .order_by(UserMemoryItem.created_at.asc())
            )
        ).all()
        fpt_mid, gmd_mid = items[0].memory_id, items[1].memory_id

    # Qdrant MockTransport returns GMD with weak score 0.21 (< 0.35 floor) and FPT with strong 0.84
    def _q_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/points/query/batch"):
            return httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "points": [
                                {"id": "p1", "score": 0.84, "payload": {"memory_id": fpt_mid}},
                                {"id": "p2", "score": 0.21, "payload": {"memory_id": gmd_mid}},
                            ]
                        },
                        {
                            "points": [
                                {"id": "p1", "score": 5.2, "payload": {"memory_id": fpt_mid}},
                                {"id": "p2", "score": 0.4, "payload": {"memory_id": gmd_mid}},
                            ]
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"result": []})

    class _FakeEmb:
        model = "Qwen/Qwen3-Embedding-0.6B"

        async def batch_generate(self, texts: list[str]) -> list[list[float]]:
            return [[0.02] * 8 for _ in texts]

    async with httpx.AsyncClient(
        base_url="http://qdrant.local:6333",
        transport=httpx.MockTransport(_q_handler),
    ) as q_client:
        async with sf() as s:
            sr = await smart_read_user_memories(
                s,
                tenant_id="aiinvest-default",
                user_id="u-rrf",
                query="Nhớ lại chi phí AI ảnh hưởng biên gộp FPT",
                as_of_date=today,
                memory_recall_need="cross_session_graph",
                explicit_tickers=["FPT"],
                user_memory_mode="on",
                qdrant_client=q_client,
                embedder=_FakeEmb(),
                reference_today=today,
            )
            recalled_ids = {m.memory_id for m in sr.recalled_memories}
            assert fpt_mid in recalled_ids
            assert gmd_mid not in recalled_ids  # Blocked by 0.35 Relevance Floor Gate!

    # Short-Circuit cross-session recall on a brand-new session (0 intra-session history!)
    monkeypatch.setattr(settings, "user_memory_mode", "on")
    monkeypatch.setattr(settings, "decision_provider", "siliconflow")
    monkeypatch.setattr(settings, "siliconflow_api_key", "sf-key")

    async def _fake_route(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "domain_class": "reformat_or_recall_chat",
            "task_action": "direct_reply",
            "reasoning_budget_tier": "zero_tool",
            "memory_recall_need": "cross_session_graph",
            "is_short_circuit": True,
            "short_circuit_kind": "reformat_or_recall_chat",
            "eval_eligible": False,
            "confidence": 0.95,
        }

    monkeypatch.setattr("sag_api.services.decision_router.aroute_research_query", _fake_route)
    orch = ResearchChatOrchestrator(session_factory=sf, llm=None)
    ans, meta, _evs = await collect_stream_result(
        orch.stream_chat(
            ResearchChatStreamRequest(query="Phiên trước tôi nhận định gì về FPT?", history=[]),
            principal=_make_principal("u-rrf"),
        )
    )
    assert "[MEM:M1]" in ans
    assert "FPT" in ans
    assert len(meta["recalled_memories"]) >= 1

    # Short-Circuit Stage 7 check: hallucinated [MEM:M99] from short-circuit LLM MUST be flagged!
    class _HallucinatingShortCircuitLLM:
        configured = True

        async def stream_complete(self, _messages: list[dict[str, str]]) -> AsyncIterator[str]:
            yield "Theo phiên trước bạn thích FPT [MEM:M1] và giữ 1 triệu VNM [MEM:M99]."

    orch_sc_bad = ResearchChatOrchestrator(session_factory=sf, llm=_HallucinatingShortCircuitLLM())
    ans_bad, meta_bad, _evs_bad = await collect_stream_result(
        orch_sc_bad.stream_chat(
            ResearchChatStreamRequest(query="Phiên trước tôi nhận định gì về FPT?", history=[]),
            principal=_make_principal("u-rrf"),
        )
    )
    assert "[MEM:M99 UNVERIFIED_CLAIM]" in ans_bad
    assert ans_bad.startswith("> ⚠️")
    assert meta_bad["telemetry"]["hallucinated_memory_ids_count"] == 1
    assert meta_bad["telemetry"]["memory_recall_need"] == "cross_session_graph"

    # USER:SELF supernode guard: unrelated VNM edge connected to USER:SELF MUST NOT leak into FPT graph recall!
    async with sf() as s:
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="u-supernode",
            payload={
                "session_id": "s-sn-fpt",
                "user_content": "Lạc quan FPT phần mềm",
                "turn_as_of_date": today.isoformat(),
                "tickers": ["FPT"],
            },
            extracted_override=ExtractedTurnBundle(
                memories=[
                    SanitizedMemoryCandidate(
                        memory_type="INVESTMENT_THESIS",
                        pit_sensitivity="MARKET_BOUND",
                        scope="TICKER_SCOPED",
                        content_text="Lạc quan tăng trưởng xuất khẩu phần mềm FPT",
                        content_hash=compute_content_hash("Lạc quan tăng trưởng xuất khẩu phần mềm FPT"),
                        tickers=["FPT"],
                        confidence=0.92,
                    )
                ],
                graph_triples=[
                    {
                        "src_type": "USER",
                        "src_key": "USER:SELF",
                        "src_name": "Nhà đầu tư",
                        "relation": "BULLISH_ON",
                        "dst_type": "TICKER",
                        "dst_key": "TICKER:FPT",
                        "dst_name": "FPT",
                        "fact_text": "Lạc quan FPT",
                    }
                ],
                tickers=["FPT"],
            ),
        )
        await process_ingest_turn_payload(
            s,
            tenant_id="aiinvest-default",
            user_id="u-supernode",
            payload={
                "session_id": "s-sn-vnm",
                "user_content": "Thận trọng VNM ngành sữa",
                "turn_as_of_date": today.isoformat(),
                "tickers": ["VNM"],
            },
            extracted_override=ExtractedTurnBundle(
                memories=[
                    SanitizedMemoryCandidate(
                        memory_type="INVESTMENT_THESIS",
                        pit_sensitivity="MARKET_BOUND",
                        scope="TICKER_SCOPED",
                        content_text="Thận trọng tiêu thụ sữa VNM chậm lại",
                        content_hash=compute_content_hash("Thận trọng tiêu thụ sữa VNM chậm lại"),
                        tickers=["VNM"],
                        confidence=0.90,
                    )
                ],
                graph_triples=[
                    {
                        "src_type": "USER",
                        "src_key": "USER:SELF",
                        "src_name": "Nhà đầu tư",
                        "relation": "BEARISH_ON",
                        "dst_type": "TICKER",
                        "dst_key": "TICKER:VNM",
                        "dst_name": "VNM",
                        "fact_text": "Thận trọng VNM",
                    }
                ],
                tickers=["VNM"],
            ),
        )
        sr_sn = await smart_read_user_memories(
            s,
            tenant_id="aiinvest-default",
            user_id="u-supernode",
            query="Xuất khẩu phần mềm FPT",
            as_of_date=today,
            memory_recall_need="cross_session_graph",
            explicit_tickers=["FPT"],
            user_memory_mode="on",
            reference_today=today,
        )
        sn_texts = [m.content_text for m in sr_sn.recalled_memories]
        assert any("FPT" in t for t in sn_texts)
        assert not any("VNM" in t for t in sn_texts), "Unrelated VNM leaked via USER:SELF Hop-2 supernode!"

    await eng.dispose()
