"""Rule 08 L1 + L2 + L3 E2E & Adversarial Tests for the Brief-Centric Brain (`brief_synthesizer.py` + HTTP SSE).

Covers:
1. L2/L3 Full FastAPI HTTP `/api/v1/research-chat/stream` E2E journey with Continuum BFF headers
   on the default `research_brain_mode == "brief_centric"` (Zero-Orphan Entrypoint Rule).
2. Adversarial Stage 7 Surgical Repair (`Component 7`): when synthesis mentally adds `65.251 + 6.079 = 77.072 tỷ`,
   Stage 7 catches `77.072 tỷ` as `UNSUPPORTED_NUMERIC_CLAIM`, triggers 1 surgical repair pass on ONLY the flagged line,
   replaces it with the verified `65.251,3 tỷ đồng [CALC-1]` figure, and re-runs Stage 7 to confirm 0 residual errors.
3. Pure ambiguity (`"Cổ phiếu nào tốt?"`) returns a 1-sentence clarifying question with 0 tool/synthesis calls.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.types import CancellationToken, ModelChunk, ModelRequest, Usage
from sag_api.core.config import settings
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import CanonicalBlock, DocumentVersion
from sag_api.db.models.source import Source
from sag_api.main import create_app


def _bff_headers() -> dict[str, str]:
    return {
        "X-SAG-BFF-Service-Token": settings.bff_service_token or "test-bff-token",
        "X-Continuum-User-Id": "u-brief-e2e",
        "X-Continuum-Organization-Id": "aiinvest-default",
        "X-Continuum-Project-Id": "all",
        "X-Continuum-Security-Partition": "all",
        "X-Continuum-Allowed-Project-Ids": "*",
    }


def _parse_sse_frames(raw_body: str) -> list[tuple[str, dict[str, Any]]]:
    frames: list[tuple[str, dict[str, Any]]] = []
    for block in raw_body.replace("\r\n", "\n").split("\n\n"):
        ev_name = ""
        data_txt = ""
        for ln in block.splitlines():
            if ln.startswith("event:"):
                ev_name = ln.split(":", 1)[1].strip()
            elif ln.startswith("data:"):
                data_txt = ln.split(":", 1)[1].strip()
        if ev_name and data_txt:
            frames.append((ev_name, json.loads(data_txt)))
    return frames


class _VhmCoreReader:
    async def fetch_issuer_profiles(self, tickers=None):
        del tickers
        return [
            {
                "ticker": "VHM",
                "company_name": "Vinhomes",
                "sector": "Bất động sản",
                "industry": "Bất động sản",
                "market_cap": 180_000e9,
            }
        ]

    async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
        del as_of_date, tickers
        return {"VHM": 180_000e9}

    async def fetch_pit_financial_ratios(self, as_of_date, *, tickers=None, lookback_years=7):
        del as_of_date, tickers, lookback_years
        return []

    async def fetch_pit_financial_statements(
        self,
        as_of_date,
        *,
        tickers=None,
        lookback_years=6,
        prefer_annual=False,
        statement_types=("IS", "BS", "CF", "RATIOS"),
    ):
        del as_of_date, tickers, lookback_years, prefer_annual, statement_types
        return [
            {
                "symbol": "VHM",
                "period_end": date(2026, 9, 30),
                "statement_type": "BS",
                "frequency": "quarterly",
                "source": "alphastock",
                "published_date": date(2026, 10, 28),
                "data": {
                    "bs_total_assets": 510_000e9,
                    "bs_equity": 215_000e9,
                    "bs_short_term_borrowings": 65_251.3e9,
                    "bs_long_term_borrowings": 42_100.0e9,
                    "i_tiền_và_các_khoản_tương_đương_tiền": 21_400.0e9,
                },
            },
            {
                "symbol": "VHM",
                "period_end": date(2026, 9, 30),
                "statement_type": "IS",
                "frequency": "quarterly",
                "source": "alphastock",
                "published_date": date(2026, 10, 28),
                "data": {
                    "is_net_revenue": 33_300.0e9,
                    "is_net_profit_after_tax": 8_980.0e9,
                },
            },
        ]


async def test_brief_brain_http_sse_surgical_stage7_repair_fixes_double_counted_vhm_debt(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "decision_provider", "disabled")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    monkeypatch.setattr(settings, "research_brain_mode", "brief_centric")
    monkeypatch.setattr(settings, "user_memory_mode", "off")

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'brief_e2e.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    pub_dt = datetime(2026, 10, 28, tzinfo=UTC)
    note_quote = "Trong tổng vay ngắn hạn 65.251,3 tỷ đồng có 6.079 tỷ đồng trái phiếu dài hạn đến hạn trả."
    async with sf() as s:
        s.add(Source(id="src-vhm", name="HOSE", sag_source_config_id="cfg-vhm"))
        s.add(
            Document(
                id="doc-vhm-q3",
                source_id="src-vhm",
                project_id="VHM",
                tenant_id="aiinvest-default",
                filename="VHM_Q3_2026.pdf",
                storage_path="/VHM_Q3_2026.pdf",
                is_active=True,
            )
        )
        s.add(
            DocumentVersion(
                id="ver-vhm-q3",
                document_id="doc-vhm-q3",
                version_no=1,
                file_hash="h-vhm-q3",
                source_published_at=pub_dt,
                valid_from=pub_dt,
                metadata_json={"sub_doc_slot": "QUARTER_NOTES", "fiscal_year": 2026, "fiscal_quarter": 3},
            )
        )
        s.add(
            CanonicalBlock(
                id="blk-vhm-1",
                document_version_id="ver-vhm-q3",
                ordinal=1,
                block_type="paragraph",
                section_path="Thuyết minh V.20 Vay ngắn hạn",
                page_from=28,
                page_to=28,
                normalized_text=note_quote,
                content_hash="ch-vhm-1",
            )
        )
        await s.commit()

    class _MentalMathThenSurgicalRepairLLM:
        configured = True

        def __init__(self) -> None:
            self.roles_called: list[str] = []

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            sys_prompt = str(request.messages[0].content)
            if "Chief Research Brief Architect" in sys_prompt:
                self.roles_called.append("brief")
                brief_json = json.dumps(
                    {
                        "user_concern": "Kiểm tra áp lực nợ vay ngắn hạn đến hạn của VHM trong Q3/2026",
                        "answer_shape": "verdict_with_reasons",
                        "entities": {"tickers": ["VHM"], "anchor_ticker": None, "candidate_search": None},
                        "premises_to_check": [],
                        "claims": [
                            {
                                "id": "K1",
                                "statement": "Quy mô vay ngắn hạn và tổng nợ vay chịu lãi của VHM Q3/2026",
                                "evidence": ["fact:st_borrowings", "fact:lt_borrowings", "fact:cash_and_equivalents"],
                                "period": "Q3/2026",
                            },
                            {
                                "id": "K2",
                                "statement": "Chi tiết trái phiếu đến hạn trả trong thuyết minh vay ngắn hạn VHM",
                                "evidence": ["doc:QUARTER_NOTES|vay ngắn hạn trái phiếu đến hạn trả"],
                                "period": "Q3/2026",
                            },
                        ],
                        "ambiguities": [],
                        "assumptions": [],
                        "needs_clarification": False,
                        "clarifying_question": None,
                        "budget_tier": "standard",
                    },
                    ensure_ascii=False,
                )
                yield ModelChunk(
                    text_delta=brief_json, usage=Usage(input_tokens=1100, output_tokens=200, cached_tokens=900)
                )
                return

            if "Forensic Disclosure Mini-Reader" in sys_prompt:
                self.roles_called.append("mini_reader")
                mr_json = json.dumps(
                    {
                        "status": "CONFIRMED",
                        "findings": [f"{note_quote} [C1]"],
                        "anomaly_followup_query": None,
                    },
                    ensure_ascii=False,
                )
                yield ModelChunk(text_delta=mr_json, usage=Usage(input_tokens=800, output_tokens=90, cached_tokens=700))
                return

            if "Senior Institutional Equity Analyst" in sys_prompt:
                self.roles_called.append("synthesis")
                # Deliberately reproduce the exact live Q2 VHM mental-addition defect (`77.072 tỷ` unsupported!)
                bad_draft = (
                    "### Kết luận trọng tâm\n"
                    "- Tổng nghĩa vụ nợ ngắn hạn VHM lên tới ~77.072 tỷ đồng do cộng gộp khoản đến hạn [CALC-2].\n"
                    "- Thuyết minh nêu rõ trong 65.251,3 tỷ đồng vay ngắn hạn đã gồm 6.079 tỷ đồng trái phiếu "
                    "dài hạn đến hạn trả [C1]."
                )
                yield ModelChunk(
                    text_delta=bad_draft, usage=Usage(input_tokens=1400, output_tokens=160, cached_tokens=800)
                )
                return

            if "Stage 7 Forensic Auditor & Surgical Editor" in sys_prompt:
                self.roles_called.append("stage7_repair")
                user_txt = str(request.messages[1].content)
                assert "77.072" in user_txt
                rep_json = json.dumps(
                    {
                        "repairs": [
                            {
                                "idx": 1,
                                "fixed_line": (
                                    "- Tổng vay và nợ thuê tài chính ngắn hạn của VHM cuối Q3/2026 là 65.251,3 tỷ đồng "
                                    "(tổng nợ vay chịu lãi ngắn + dài hạn đạt 107.351,3 tỷ đồng) [CALC-1] [CALC-2]."
                                ),
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
                yield ModelChunk(
                    text_delta=rep_json, usage=Usage(input_tokens=900, output_tokens=80, cached_tokens=600)
                )
                return

            raise AssertionError(f"Unexpected system prompt: {sys_prompt[:80]}")

    app = create_app()
    fake_llm = _MentalMathThenSurgicalRepairLLM()
    app.state.llm = fake_llm
    app.state.engine_manager = None
    trace_events: list[tuple[str, dict[str, Any]]] = []
    app.state.brain_trace_sink = lambda stage, payload: trace_events.append((stage, payload))

    import sag_api.api.v1.research_chat as rc_router
    import sag_api.core.db as core_db

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with sf() as s:
            yield s

    app.dependency_overrides[rc_router.get_session] = _override_session
    app.dependency_overrides[rc_router.get_core_metrics_reader] = lambda: _VhmCoreReader()
    monkeypatch.setattr(core_db, "SessionLocal", sf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research-chat/stream",
            headers=_bff_headers(),
            json={
                "query": "Soi áp lực nợ ngắn hạn đến hạn của VHM Q3/2026",
                "mode": "MOAT",
                "as_of_date": "2026-11-26",
            },
        )
        assert resp.status_code == 200
        frames = _parse_sse_frames(resp.text)

    done_payload = next(data for ev, data in frames if ev == "done")
    final_ans = done_payload["answer"]
    meta = done_payload["metadata"]

    # Verified 4-step role trace: brief -> mini_reader -> synthesis -> stage7_repair!
    assert fake_llm.roles_called == ["brief", "mini_reader", "synthesis", "stage7_repair"]
    # Mental-math `77.072` is completely repaired out, `65.251,3` and `107.351,3` are verified with 0 Stage 7 errors!
    assert "77.072" not in final_ans
    assert "65.251,3 tỷ đồng" in final_ans
    assert "107.351,3 tỷ đồng" in final_ans
    assert meta["telemetry"]["retrieval_mode"] == "BRIEF_CENTRIC_BRAIN"
    assert meta["telemetry"]["unsupported_numeric_claims_count"] == 0
    assert meta["telemetry"]["rejected_citations_count"] == 0
    assert meta["telemetry"]["input_tokens"] == 1100 + 800 + 1400 + 900

    # Full step-by-step brain trace is recorded through the real HTTP entrypoint
    stages = [s for s, _ in trace_events]
    for required in (
        "brief.request",
        "brief.attempt",
        "brief.final",
        "locator",
        "mini_reader",
        "claim.card",
        "synthesis",
        "stage7.audit",
        "stage7.repair",
    ):
        assert required in stages, required
    assert stages.index("brief.final") < stages.index("synthesis") < stages.index("stage7.repair")
    synth_evt = next(p for s, p in trace_events if s == "synthesis")
    assert "77.072" in synth_evt["raw_model_output"]
    assert "<VERIFIED_CLAIM_CARDS>" in synth_evt["user_prompt"]
    audits = [p for s, p in trace_events if s == "stage7.audit"]
    assert [a["audit_pass"] for a in audits] == [1, 2]
    assert any("77.072" in u["claim_text"] for u in audits[0]["unsupported_claims"])
    assert audits[1]["unsupported_claims"] == []
    repair_evt = next(p for s, p in trace_events if s == "stage7.repair")
    assert repair_evt["outcome"] == "APPLIED"
    assert "77.072" in repair_evt["applied"][0]["before"]
    assert "65.251,3" in repair_evt["applied"][0]["after"]
    k1_card = next(p for s, p in trace_events if s == "claim.card" and p["claim_id"] == "K1")
    assert "fact:st_borrowings" in k1_card["evidence_specs"]
    assert "### [K1]" in k1_card["rendered_for_synthesis"]

    await eng.dispose()


async def test_brief_brain_pure_ambiguity_asks_single_question_with_zero_extra_calls(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "decision_provider", "disabled")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    monkeypatch.setattr(settings, "research_brain_mode", "brief_centric")
    monkeypatch.setattr(settings, "user_memory_mode", "off")

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'brief_ambig.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    class _AmbiguousBriefOnlyLLM:
        configured = True

        def __init__(self) -> None:
            self.calls = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            self.calls += 1
            yield ModelChunk(
                text_delta=json.dumps(
                    {
                        "user_concern": "Người hỏi tìm cổ phiếu tốt nhưng chưa nêu tiêu chí",
                        "answer_shape": "clarification_needed",
                        "entities": {"tickers": [], "anchor_ticker": None, "candidate_search": None},
                        "premises_to_check": [],
                        "claims": [],
                        "ambiguities": ["tăng trưởng, định giá rẻ hay cổ tức cao"],
                        "assumptions": [],
                        "needs_clarification": True,
                        "clarifying_question": (
                            "Bạn ưu tiên cổ phiếu tốt theo tăng trưởng lợi nhuận, định giá rẻ "
                            "hay cổ tức tiền mặt cao?"
                        ),
                        "budget_tier": "fast",
                    },
                    ensure_ascii=False,
                ),
                usage=Usage(input_tokens=900, output_tokens=120, cached_tokens=800),
            )

    app = create_app()
    llm_double = _AmbiguousBriefOnlyLLM()
    app.state.llm = llm_double
    app.state.engine_manager = None

    import sag_api.api.v1.research_chat as rc_router
    import sag_api.core.db as core_db

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with sf() as s:
            yield s

    app.dependency_overrides[rc_router.get_session] = _override_session
    app.dependency_overrides[rc_router.get_core_metrics_reader] = lambda: _VhmCoreReader()
    monkeypatch.setattr(core_db, "SessionLocal", sf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research-chat/stream",
            headers=_bff_headers(),
            json={"query": "Cổ phiếu nào tốt?", "mode": "GENERAL", "as_of_date": "2026-11-26"},
        )
        assert resp.status_code == 200
        frames = _parse_sse_frames(resp.text)

    done_payload = next(data for ev, data in frames if ev == "done")
    assert llm_double.calls == 1
    assert "tăng trưởng lợi nhuận" in done_payload["answer"]
    assert done_payload["metadata"]["telemetry"]["playbook_id"] == "brief_clarification"

    await eng.dispose()


async def test_brief_brain_invalid_brief_twice_surfaces_fallback_banner_instead_of_silent_substitution(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(settings, "decision_provider", "disabled")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    monkeypatch.setattr(settings, "research_brain_mode", "brief_centric")
    monkeypatch.setattr(settings, "user_memory_mode", "off")

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'brief_fallback.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    class _InvalidBriefLLM:
        configured = True

        def __init__(self) -> None:
            self.calls = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del request, cancellation
            self.calls += 1
            yield ModelChunk(
                text_delta="Tôi nghĩ nên lọc theo tiền mặt.",
                usage=Usage(input_tokens=700, output_tokens=20),
            )

    app = create_app()
    llm_double = _InvalidBriefLLM()
    app.state.llm = llm_double
    app.state.engine_manager = None

    import sag_api.api.v1.research_chat as rc_router
    import sag_api.core.db as core_db

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with sf() as s:
            yield s

    app.dependency_overrides[rc_router.get_session] = _override_session
    app.dependency_overrides[rc_router.get_core_metrics_reader] = lambda: _VhmCoreReader()
    monkeypatch.setattr(core_db, "SessionLocal", sf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research-chat/stream",
            headers=_bff_headers(),
            json={"query": "Cổ phiếu nào trên sàn đang rẻ so với tiền mặt?", "as_of_date": "2026-11-26"},
        )
        assert resp.status_code == 200
        frames = _parse_sse_frames(resp.text)

    done_payload = next(data for ev, data in frames if ev == "done")
    codes = [b["code"] for b in done_payload["metadata"]["declared_blindspots"]]
    assert llm_double.calls == 2
    assert "BRIEF_FALLBACK_DEGRADED" in codes
    # User sees an explicit degraded banner + a clarifying question, never a silently swapped ROE screen
    assert "phương án dự phòng" in done_payload["answer"]
    assert "chỉ tiêu cụ thể nào" in done_payload["answer"]
    assert done_payload["metadata"]["screening_results"] == []

    await eng.dispose()


async def test_repair_stage7_matches_markdown_table_rows_with_spaced_percent_claims() -> None:
    from sag_api.research.brief_executor import ClaimVerdictCard
    from sag_api.research.brief_synthesizer import repair_stage7_flagged_lines

    class _TableRowRepairModel:
        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            user_txt = str(request.messages[1].content)
            assert '"idx": 2' in user_txt
            rep = json.dumps(
                {
                    "repairs": [
                        {
                            "idx": 2,
                            "fixed_line": (
                                "| **Phải thu ngắn hạn** | **381,513.4 tỷ đồng** [CALC-3] | "
                                "Chiếm 28.49% tổng tài sản [CALC-3]. |"
                            ),
                        }
                    ]
                },
                ensure_ascii=False,
            )
            yield ModelChunk(text_delta=rep, usage=Usage(input_tokens=400, output_tokens=60, cached_tokens=300))

    draft = (
        "| Chỉ tiêu | Giá trị | Đánh giá |\n"
        "| :--- | :--- | :--- |\n"
        "| **Phải thu ngắn hạn** | **381,513.4 tỷ đồng** [CALC-3] | Chiếm 28.5% tổng tài sản. |"
    )
    repaired, usage, changed = await repair_stage7_flagged_lines(
        answer_text=draft,
        unsupported_claims=[
            {
                "claim_text": "28.5 %",
                "unit_class": "PERCENT",
                "sentence_excerpt": "| Phải thu ngắn hạn | 381,513.4 tỷ đồng | Chiếm 28.5% tổng tài sản. |",
            }
        ],
        uncited_claims=[],
        rejected_citations=[],
        verdict_cards=[
            ClaimVerdictCard(
                claim_id="K2",
                is_premise=False,
                statement="Phải thu ngắn hạn / Tổng tài sản",
                period="Q3/2026",
                status="CONFIRMED",
                summary_lines=["[CALC-3] Phải thu ngắn hạn/Tổng TS=28.49%"],
            )
        ],
        calculator_traces=[],
        sections_by_cid={},
        model=_TableRowRepairModel(),
    )
    assert changed is True
    assert "28.49% tổng tài sản [CALC-3]" in repaired
    assert usage.requests == 1


async def test_brief_brain_streams_thinking_deduplicates_activity_and_persists_verdicts_and_thinking_ms(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Packet B Red->Green:
    1. `apply_litellm_completion_policy` keeps `thinking={'type':'disabled'}` by default on DeepSeek-V4,
       and honors opt-in `enable_thinking=True` without clobbering it.
    2. Live HTTP `/api/v1/research-chat/stream` streams incremental `thought` (`kind='reasoning', append=True`)
       chunks during Brief & Synthesis, emits quant + disclosure tool steps, deduplicates `activity_log`
       in-place (0 duplicate IDs, 0 stuck `'running'` spinners), and persists `thinking_duration_ms`,
       `reasoning_tokens`, `research_brief`, and `verdict_cards` in `done.metadata`.
    """
    from sag_api.core.config import Settings
    from sag_api.core.litellm_policy import apply_litellm_completion_policy

    cfg = Settings(
        _env_file=None,  # type: ignore[call-arg]
        llm_provider="openai",
        llm_base_url="https://api.deepinfra.com/v1/openai",
        llm_api_key="sk-test",
        llm_model="deepseek-ai/DeepSeek-V4-Flash",
    )
    default_req = apply_litellm_completion_policy(
        cfg, {"model": "openai/deepseek-ai/DeepSeek-V4-Flash", "messages": []}
    )
    assert default_req["extra_body"]["thinking"] == {"type": "disabled"}

    optin_req = apply_litellm_completion_policy(
        cfg,
        {
            "model": "openai/deepseek-ai/DeepSeek-V4-Flash",
            "messages": [],
            "extra_body": {"enable_thinking": True},
        },
    )
    assert optin_req["extra_body"]["enable_thinking"] is True
    assert optin_req["extra_body"]["chat_template_kwargs"]["enable_thinking"] is True
    assert "thinking" not in optin_req["extra_body"]

    monkeypatch.setattr(settings, "decision_provider", "disabled")
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-token")
    monkeypatch.setattr(settings, "research_brain_mode", "brief_centric")
    monkeypatch.setattr(settings, "user_memory_mode", "off")

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'brief_think.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    pub_dt = datetime(2026, 10, 28, tzinfo=UTC)
    note_quote = "Vay ngắn hạn VHM cuối Q3/2026 là 65.251,3 tỷ đồng."
    async with sf() as s:
        s.add(Source(id="src-th", name="HOSE", sag_source_config_id="cfg-th"))
        s.add(
            Document(
                id="doc-th",
                source_id="src-th",
                project_id="VHM",
                tenant_id="aiinvest-default",
                filename="VHM_Q3.pdf",
                storage_path="/VHM_Q3.pdf",
                is_active=True,
            )
        )
        s.add(
            DocumentVersion(
                id="ver-th",
                document_id="doc-th",
                version_no=1,
                file_hash="h-th",
                source_published_at=pub_dt,
                valid_from=pub_dt,
                metadata_json={"sub_doc_slot": "QUARTER_NOTES", "fiscal_year": 2026, "fiscal_quarter": 3},
            )
        )
        s.add(
            CanonicalBlock(
                id="blk-th-1",
                document_version_id="ver-th",
                ordinal=1,
                block_type="paragraph",
                section_path="Thuyết minh V.20",
                page_from=12,
                page_to=12,
                normalized_text=note_quote,
                content_hash="ch-th-1",
            )
        )
        await s.commit()

    class _StreamingThinkingBriefModel:
        configured = True

        def __init__(self) -> None:
            self.seen_enable_thinking: dict[str, bool] = {}

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            sys_prompt = str(request.messages[0].content)
            if "Chief Research Brief Architect" in sys_prompt:
                self.seen_enable_thinking["brief"] = bool(request.metadata.get("enable_thinking"))
                yield ModelChunk(reasoning_delta="Cần kiểm tra nợ vay ngắn hạn ")
                yield ModelChunk(reasoning_delta="và thuyết minh trái phiếu của VHM.")
                brief_json = json.dumps(
                    {
                        "user_concern": "Kiểm tra vay ngắn hạn VHM Q3/2026",
                        "answer_shape": "verdict_with_reasons",
                        "entities": {"tickers": ["VHM"], "anchor_ticker": None, "candidate_search": None},
                        "premises_to_check": [],
                        "claims": [
                            {
                                "id": "K1",
                                "statement": "Quy mô vay ngắn hạn VHM Q3/2026",
                                "evidence": ["fact:st_borrowings", "doc:QUARTER_NOTES|vay ngắn hạn"],
                                "period": "Q3/2026",
                            }
                        ],
                        "ambiguities": [],
                        "assumptions": [],
                        "needs_clarification": False,
                        "clarifying_question": None,
                        "budget_tier": "standard",
                    },
                    ensure_ascii=False,
                )
                yield ModelChunk(
                    text_delta=brief_json,
                    usage=Usage(input_tokens=900, output_tokens=180, cached_tokens=700, reasoning_tokens=45),
                )
                return

            if "Forensic Disclosure Mini-Reader" in sys_prompt:
                self.seen_enable_thinking["mini_reader"] = bool(request.metadata.get("enable_thinking"))
                mr_json = json.dumps(
                    {
                        "status": "CONFIRMED",
                        "findings": [f"{note_quote} [C1]"],
                        "anomaly_followup_query": None,
                    },
                    ensure_ascii=False,
                )
                yield ModelChunk(text_delta=mr_json, usage=Usage(input_tokens=500, output_tokens=60, cached_tokens=400))
                return

            if "Senior Institutional Equity Analyst" in sys_prompt:
                self.seen_enable_thinking["synthesis"] = bool(request.metadata.get("enable_thinking"))
                yield ModelChunk(reasoning_delta="Đối chiếu số CĐKT 65.251,3 tỷ ")
                yield ModelChunk(reasoning_delta="khớp hoàn toàn với thuyết minh [C1].")
                yield ModelChunk(
                    text_delta="Vay ngắn hạn của VHM cuối Q3/2026 đạt 65.251,3 tỷ đồng [CALC-1] [C1].",
                    usage=Usage(input_tokens=1000, output_tokens=120, cached_tokens=600, reasoning_tokens=55),
                )
                return

            raise AssertionError(f"Unexpected role: {sys_prompt[:60]}")

    app = create_app()
    think_llm = _StreamingThinkingBriefModel()
    app.state.llm = think_llm
    app.state.engine_manager = None

    import sag_api.api.v1.research_chat as rc_router
    import sag_api.core.db as core_db

    async def _override_session() -> AsyncIterator[AsyncSession]:
        async with sf() as s:
            yield s

    app.dependency_overrides[rc_router.get_session] = _override_session
    app.dependency_overrides[rc_router.get_core_metrics_reader] = lambda: _VhmCoreReader()
    monkeypatch.setattr(core_db, "SessionLocal", sf)

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/v1/research-chat/stream",
            headers=_bff_headers(),
            json={"query": "Kiểm tra vay ngắn hạn VHM Q3/2026", "mode": "GENERAL", "as_of_date": "2026-11-26"},
        )
        assert resp.status_code == 200
        frames = _parse_sse_frames(resp.text)

    # 1) Model requests enabled thinking on Brief & Synthesis, disabled on Mini-Reader JSON
    assert think_llm.seen_enable_thinking == {"brief": True, "mini_reader": False, "synthesis": True}

    # 2) Live SSE frames include incremental `append=True` reasoning chunks
    live_reasoning = [
        d for ev, d in frames if ev == "thought" and d.get("kind") == "reasoning" and d.get("append") is True
    ]
    assert len(live_reasoning) >= 4

    # 3) Persisted `done.metadata.activity_log` has ZERO duplicate IDs, ZERO stuck 'running' spinners,
    #    coalesced reasoning text, quant tool step, and structured verdict card metadata!
    done_meta = next(d for ev, d in frames if ev == "done")["metadata"]
    act_log = done_meta["activity_log"]
    act_ids = [item.get("call_id") or item.get("id") for item in act_log]
    assert len(act_ids) == len(set(act_ids)), f"Duplicate activity IDs found: {act_ids}"
    assert not any(item.get("status") == "running" for item in act_log), f"Stuck running item in: {act_log}"

    by_id = {(item.get("call_id") or item.get("id")): item for item in act_log}
    assert "Cần kiểm tra nợ vay ngắn hạn và thuyết minh trái phiếu của VHM." in by_id["brief-reasoning-1"]["text"]
    synth_r_txt = by_id["synthesis-reasoning-1"]["text"]
    assert "Đối chiếu số CĐKT 65.251,3 tỷ khớp hoàn toàn với thuyết minh [C1]." in synth_r_txt
    assert by_id["doc-K1-1"]["status"] == "done"
    assert "quant-K1" in by_id and by_id["quant-K1"]["status"] == "done"
    assert by_id["card-K1"]["verdict_status"] == "CONFIRMED"

    # 4) True thinking_duration_ms, reasoning_tokens, research_brief, and verdict_cards persisted
    tel = done_meta["telemetry"]
    assert isinstance(tel.get("thinking_duration_ms"), int) and tel["thinking_duration_ms"] >= 0
    assert tel["reasoning_tokens"] == 45 + 55
    assert done_meta["research_brief"]["user_concern"] == "Kiểm tra vay ngắn hạn VHM Q3/2026"
    assert len(done_meta["verdict_cards"]) == 1
    assert done_meta["verdict_cards"][0]["claim_id"] == "K1"

    await eng.dispose()

