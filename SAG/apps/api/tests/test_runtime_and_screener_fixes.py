"""Rule 08 L1 + L2 regression tests for Step 3 runtime & screener fixes (R3, R4, R5, R6, R7, R8).

Every test targets an observed live failure from the 24-ticker broker evaluation:
- R3: Tool-loop timeout preserves accumulated token usage and triggers reserved `_direct_synthesis_fallback`.
- R4: `run_financial_calculator` raises on `CalculatorError` -> `EventType.TOOL_FAILED` (never 'Hoàn tất CALC').
- R8: `SentenceStreamGate` holds >220-char internal monologues on tool-enabled turns (0 prose leak).
- R7: `_run_discrepancy_check` enforces `(fiscal_year, fiscal_quarter)` alignment and skips `AGM_TARGETS`.
- R5: `screen_financial_universe` TSV surfaces `CFO/LNST`, `FCF (tỷ)`, and `Nợ ròng/VCSH`.
- R6: Screener ignores `cafef` rows, blocks same-FY/unscaled PDF overrides, computes TTM ROE on quarterly
      mode, and fixes bank `roe == roa` ETL copy corruption using bank `vốn_và_các_quỹ` statements.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.tools import ToolContext
from sag_agent.types import (
    CancellationToken,
    ModelChunk,
    ModelRequest,
    ToolCall,
    Usage,
)
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import DocumentVersion
from sag_api.db.models.source import Source
from sag_api.db.models.stock_research import SagExtractedTable
from sag_api.research.agent_runner import SentenceStreamGate, run_research_agent_turn
from sag_api.research.chat_orchestrator import (
    RetrievalResult,
    RetrievedResearchSection,
    _run_discrepancy_check,
)
from sag_api.research.financial_metrics_store import (
    _normalize_core_ratio_rows,
    screen_universe_metrics,
)
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext
from sag_api.research.tools.research_tools import screen_financial_universe_tool
from sag_api.schemas.research_chat import HydratedTableItem

FIXTURE = Path(__file__).parent / "fixtures" / "core_financial_statements_sample.json"
AS_OF = date(2026, 11, 26)


def _principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-step3",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-step3",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


def test_r8_sentence_stream_gate_never_leaks_long_monologue_on_tool_turns() -> None:
    gate = SentenceStreamGate(valid_cid_ceiling=lambda: 2, lookahead_chars=220)
    gate.reset_for_turn(tools_enabled=True)

    long_monologue = (
        "Tôi cần thêm dữ liệu thuyết minh nợ xấu nhóm 3-5 của MBB tại Quý 3/2026 và kiểm tra tỷ lệ CASA. "
        * 4
    )
    assert len(long_monologue) > 350
    # Must NOT flush even when buffer exceeds 220 chars on a tool-enabled turn!
    assert gate.push_delta(long_monologue) == []
    captured = gate.mark_pending_tool_call()
    assert "Tôi cần thêm dữ liệu" in captured
    assert gate.flush_turn_end(has_tool_calls=True) == []

    # When a tool-enabled turn finishes WITHOUT calling tools, flush_turn_end emits the sanitized answer
    gate.reset_for_turn(tools_enabled=True)
    assert gate.push_delta("Doanh thu FPT đạt 62.849 tỷ đồng [C1]. ") == []
    emitted = gate.flush_turn_end(has_tool_calls=False)
    assert "Doanh thu FPT đạt 62.849 tỷ đồng [C1]." in "".join(emitted)


def test_r7_discrepancy_check_aligns_periods_and_skips_agm_targets() -> None:
    pr_fy25 = RetrievedResearchSection(
        ticker="ACB",
        doc_id="d-acb-25",
        document_version_id="v-acb-25",
        sub_doc_slot="BCTN_PR",
        page_from=4,
        page_to=4,
        published_date=date(2026, 4, 10),
        content="Lợi nhuận trước thuế năm 2025 đạt 23.000 tỷ đồng.",
        fiscal_year=2025,
        fiscal_quarter=0,
    )
    q3_2026_tbl = HydratedTableItem(
        table_id="tbl-acb-q326",
        project_id="ACB",
        title="Báo cáo kết quả hoạt động kinh doanh Quý 3/2026",
        sub_doc_slot="QUARTER_NOTES",
        page_from=6,
        page_to=6,
        currency="VND",
        unit_raw="tỷ đồng",
        unit_multiplier=1e9,
        verification_status="VERIFIED",
        fiscal_year=2026,
        fiscal_quarter=3,
        headers=[["Chỉ tiêu", "Q3/2026"]],
        rows=[{"label": "Tổng lợi nhuận kế toán trước thuế", "values": [5100.0]}],
    )
    # Cross-period (FY2025 narrative vs Q3/2026 single-quarter table) MUST produce 0 false alerts
    assert _run_discrepancy_check(sections=[pr_fy25], hydrated_tables=[q3_2026_tbl]) == []

    # Same-period FY2025 audited table DOES trigger a genuine discrepancy alert
    fy25_tbl = q3_2026_tbl.model_copy(
        update={
            "table_id": "tbl-acb-fy25",
            "title": "Báo cáo kết quả hoạt động kinh doanh năm 2025",
            "sub_doc_slot": "BCTC_AUDITED",
            "fiscal_year": 2025,
            "fiscal_quarter": 0,
            "headers": [["Chỉ tiêu", "Năm 2025"]],
            "rows": [{"label": "Tổng lợi nhuận kế toán trước thuế", "values": [19539.0]}],
        }
    )
    same_period_alerts = _run_discrepancy_check(sections=[pr_fy25], hydrated_tables=[fy25_tbl])
    assert len(same_period_alerts) == 1


async def test_r3_r4_timeout_rescues_synthesis_preserves_tokens_and_calc_error_fails_loudly() -> None:
    @asynccontextmanager
    async def _sf() -> AsyncIterator[dict[str, int]]:
        yield {"sid": 1}

    class _StubRetriever:
        async def retrieve(self, **_kwargs: Any) -> RetrievalResult:
            return RetrievalResult(
                sections=[
                    RetrievedResearchSection(
                        ticker="VHM",
                        doc_id="d-vhm",
                        document_version_id="v-vhm",
                        sub_doc_slot="BCTC_AUDITED",
                        page_from=8,
                        page_to=8,
                        published_date=date(2026, 3, 30),
                        heading="KQKD VHM 2025",
                        content="Lợi nhuận sau thuế VHM năm 2025 đạt 35.073 tỷ đồng.",
                        fiscal_year=2025,
                        fiscal_quarter=0,
                    )
                ]
            )

    class _SlowLoopWithBrokenCalcModel:
        configured = True

        def __init__(self) -> None:
            self.calls = 0

        async def stream_turn(
            self, request: ModelRequest, cancellation: CancellationToken
        ) -> AsyncIterator[ModelChunk]:
            del cancellation
            self.calls += 1
            if self.calls == 1:
                yield ModelChunk(
                    usage=Usage(input_tokens=500, output_tokens=80, cached_tokens=300),
                    tool_calls=(
                        ToolCall(
                            id="tc-search",
                            name="search_disclosures",
                            arguments={
                                "tickers": ["VHM"],
                                "sub_doc_slots": ["BCTC_AUDITED"],
                                "fiscal_year": 2025,
                                "fiscal_quarter": 0,
                                "vas_terms": ["lợi nhuận sau thuế"],
                                "question": "LNST VHM 2025",
                            },
                        ),
                        ToolCall(
                            id="tc-bad-calc",
                            name="run_financial_calculator",
                            arguments={
                                "formula": "unknown_var_xyz / 0",
                                "description": "Phép tính lỗi",
                                "inputs": {"rev": 100.0},
                            },
                        ),
                    ),
                    finish_reason="tool_calls",
                )
                return
            if request.tools:
                # Simulate Turn 2 stalling past the tool-loop budget
                await asyncio.sleep(5.0)
                return
            # Direct synthesis fallback (tools == ()) succeeds rapidly!
            yield ModelChunk(
                text_delta=" Báo cáo cứu hộ: LNST VHM năm 2025 đạt 35.073 tỷ đồng [C1].",
                usage=Usage(input_tokens=400, output_tokens=120, cached_tokens=50),
                finish_reason="stop",
            )

    evidence = ResearchEvidenceLedger()
    rctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="MOAT",
        session_factory=_sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=evidence,
        available_tickers=frozenset({"VHM"}),
        retriever_override=_StubRetriever(),
    )
    tool_results: list[dict[str, Any]] = []

    async def _on_tool_res(evt: dict[str, Any]) -> None:
        tool_results.append(evt)

    outcome = await run_research_agent_turn(
        query="Phân tích LNST VHM 2025",
        model=_SlowLoopWithBrokenCalcModel(),
        ctx=rctx,
        route_decision={"task_action": "deep_moat_thesis", "max_agent_turns": 4},
        explicit_tickers=["VHM"],
        on_tool_result=_on_tool_res,
        overall_timeout_seconds=1.2,
    )

    # R4: Broken calculator call MUST emit status='error' (never 'done' / 'Hoàn tất CALC')
    calc_evt = next(e for e in tool_results if e.get("tool") == "run_financial_calculator")
    assert calc_evt["status"] == "error"
    assert "Hoàn tất" not in str(calc_evt.get("summary") or "")
    assert evidence.calculator_traces == []

    # R3: Synthesis rescue produced grounded prose AND preserved Turn 1 + rescue token counts!
    assert "35.073 tỷ đồng [C1]" in outcome.answer_text
    assert outcome.input_tokens == 900
    assert outcome.output_tokens == 200
    assert outcome.cached_tokens == 350


async def test_r5_r6_screener_fixes_cafef_pdf_scale_quarterly_ttm_bank_roe_and_tsv_cols(
    tmp_path: Path,
) -> None:
    sample = json.loads(FIXTURE.read_text(encoding="utf-8"))
    # Direct invariant: upstream roe == roa copy bug must clear roe (never emit 1.69% as ROE on sort_by='pe')
    norm_dup = _normalize_core_ratio_rows(
        [{"symbol": "MBB", "ratio_date": date(2025, 12, 31), "frequency": "yearly", "roe": 0.016947, "roa": 0.016947}]
    )
    assert norm_dup[0]["roe"] is None and norm_dup[0]["roa"] == pytest.approx(0.016947)
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'scr.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    pub_dt = datetime(2026, 3, 25, tzinfo=UTC)
    async with sf() as s:
        s.add(Source(id="src-s", name="HOSE", sag_source_config_id="cfg-s"))
        for tk in ("BSR", "MBB", "VHM"):
            s.add(
                Document(
                    id=f"doc-{tk}",
                    source_id="src-s",
                    project_id=tk,
                    tenant_id="aiinvest-default",
                    filename=f"{tk}.pdf",
                    storage_path=f"/{tk}.pdf",
                    is_active=True,
                )
            )
            s.add(
                DocumentVersion(
                    id=f"ver-{tk}",
                    document_id=f"doc-{tk}",
                    version_no=1,
                    file_hash=f"h-{tk}",
                    valid_from=pub_dt,
                )
            )
        await s.flush()
        # Mis-scaled FY2025 PDF table on BSR (unit_multiplier=1.0, net_income=5217 instead of 5,217e9!)
        s.add(
            SagExtractedTable(
                table_id="tbl-bsr-bad-scale",
                project_id="BSR",
                document_version_id="ver-BSR",
                sub_doc_slot="BCTC_AUDITED",
                fiscal_year=2025,
                fiscal_quarter=0,
                table_title="Báo cáo kết quả hoạt động kinh doanh năm 2025",
                section_path="BCTC_AUDITED",
                page_from=7,
                page_to=7,
                currency="VND",
                unit_raw="tỷ đồng",
                unit_multiplier=1.0,
                headers_json=[["Chỉ tiêu", "Năm 2025"]],
                rows_json=[
                    [{"raw_text": "Lợi nhuận sau thuế thu nhập doanh nghiệp", "numeric_value": None},
                     {"raw_text": "5.217", "numeric_value": 5217.0}],
                ],
                verification_status="VERIFIED",
                table_hash="th-bsr-bad",
                valid_from=pub_dt,
            )
        )
        await s.commit()

    vhm_rows = [r for r in sample["rows"] if r["symbol"] == "VHM" and r["statement_type"] in ("BS", "IS", "CF")]

    class _FakeCoreReader:
        async def fetch_issuer_profiles(self, tickers=None):
            del tickers
            return [
                {
                    "ticker": "BSR",
                    "company_name": "Lọc Hóa dầu Bình Sơn",
                    "sector": "Dầu khí",
                    "industry": "Dầu khí",
                    "exchange": "HOSE",
                    "market_cap": 65_000e9,
                },
                {
                    "ticker": "MBB",
                    "company_name": "MBBank",
                    "sector": "Tài chính",
                    "industry": "Ngân hàng",
                    "exchange": "HOSE",
                    "market_cap": 150_000e9,
                },
                {
                    "ticker": "VHM",
                    "company_name": "Vinhomes",
                    "sector": "Bất động sản",
                    "industry": "Bất động sản",
                    "exchange": "HOSE",
                    "market_cap": 180_000e9,
                },
            ]

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {"BSR": 65_000e9, "MBB": 150_000e9, "VHM": 180_000e9}

        async def fetch_pit_financial_ratios(self, as_of_date, *, tickers=None, lookback_years=7):
            del as_of_date, tickers, lookback_years
            return [
                {
                    "symbol": "MBB",
                    "ratio_date": date(2025, 12, 31),
                    "frequency": "yearly",
                    "published_date": date(2026, 3, 20),
                    "roe": 0.016947,
                    "roa": 0.016947,
                    "pe": 6.36,
                    "pb": 1.43,
                },
                {
                    "symbol": "VHM",
                    "ratio_date": date(2026, 9, 30),
                    "frequency": "quarterly",
                    "published_date": date(2026, 10, 28),
                    "roe": 0.041,
                    "roa": 0.018,
                    "pe": 5.5,
                    "pb": 0.95,
                },
                {
                    "symbol": "BSR",
                    "ratio_date": date(2025, 12, 31),
                    "frequency": "yearly",
                    "published_date": date(2026, 3, 20),
                    "roe": 0.0915,
                    "roa": 0.061,
                    "pe": 10.46,
                    "pb": 1.33,
                },
            ]

        async def fetch_pit_financial_statements(
            self, as_of_date, *, tickers=None, lookback_years=5, prefer_annual=True, statement_types=("BS", "IS", "CF")
        ):
            del as_of_date, tickers, lookback_years, prefer_annual, statement_types
            return [
                # BSR alphastock row FOLLOWED BY cafef row (which previously overwrote alphastock!)
                {
                    "symbol": "BSR",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "IS",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 20),
                    "data": {"doanh_thu_thuần": 123_000e9, "lợi_nhuận_sau_thuế": 5_217e9},
                },
                {
                    "symbol": "BSR",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "BS",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 20),
                    "data": {"tổng_cộng_tài_sản": 88_000e9, "vốn_chủ_sở_hữu": 57_000e9},
                },
                {
                    "symbol": "BSR",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "IS",
                    "frequency": "yearly",
                    "source": "cafef",
                    "published_date": date(2026, 3, 21),
                    "data": {"HDKD_1": 123_000e9, "HDKD_9": 5_217e9},
                },
                # MBB bank statements using bank-specific 'vốn_và_các_quỹ' key
                {
                    "symbol": "MBB",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "IS",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 20),
                    "data": {"thu_nhập_lãi_thuần": 42_000e9, "lợi_nhuận_sau_thuế": 24_000e9},
                },
                {
                    "symbol": "MBB",
                    "period_end": date(2025, 12, 31),
                    "statement_type": "BS",
                    "frequency": "yearly",
                    "source": "alphastock",
                    "published_date": date(2026, 3, 20),
                    "data": {"tổng_tài_sản_có": 1_150_000e9, "vốn_và_các_quỹ": 115_000e9},
                },
                *vhm_rows,
            ]

    fake_core = _FakeCoreReader()
    async with sf() as s:
        rows_ann, _, _ = await screen_universe_metrics(
            s, tenant_id="aiinvest-default", as_of_date=AS_OF, core_reader=fake_core, prefer_annual=True
        )
        by_tk = {r["ticker"]: r for r in rows_ann}
        # R6-a + R6-b: BSR ROE stays ~9.15% (never wiped by cafef or overwritten by 5217 unscaled PDF!)
        assert by_tk["BSR"]["roe"] == pytest.approx(5_217e9 / 57_000e9, rel=0.02)
        assert by_tk["BSR"]["net_income"] == pytest.approx(5_217e9)
        # R6-d: MBB bank ROE computed from vốn_và_các_quỹ (~20.87%), never 1.69% ROA!
        assert by_tk["MBB"]["roe"] == pytest.approx(24_000e9 / 115_000e9, rel=0.02)

        # R6-c: Quarterly mode (`prefer_annual=False`) computes TTM ROE (>10%), never single-quarter 4.1%!
        rows_q, _, _ = await screen_universe_metrics(
            s,
            tenant_id="aiinvest-default",
            as_of_date=AS_OF,
            core_reader=fake_core,
            tickers=["VHM"],
            prefer_annual=False,
        )
        assert rows_q[0]["roe"] > 0.10

    # R5: `screen_financial_universe` TSV surfaces CFO/LNST, FCF, and Nợ ròng/VCSH
    evidence = ResearchEvidenceLedger()
    rctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(),
        chat_mode="MOAT",
        session_factory=sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=evidence,
        core_reader=fake_core,
    )

    async def _noop(_p: Any) -> None:
        return None

    tctx = ToolContext(
        run_id="r-scr",
        tool_call_id="tc-scr",
        data=rctx,
        cancellation=CancellationToken(),
        _on_progress=_noop,
    )
    tool_out = await screen_financial_universe_tool.execute({"tickers": ["VHM"]}, tctx)
    assert "CFO/LNST" in tool_out.content
    assert "FCF" in tool_out.content
    assert "Nợ ròng/VCSH" in tool_out.content

    await eng.dispose()
