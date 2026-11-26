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
    for block in raw_body.split("\n\n"):
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
