"""Phase 3 TDD Suite: Scope-Gated Inquiry Architect (`Stage 1`), Pointer-Guided Seed Injection,
and Quantitative-Anomaly-Triggered Pass-2 Causal Investigation (`Workflow`).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, date, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.types import CancellationToken, ModelChunk, ModelProvider, ModelRequest, Usage
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, KnowledgeUnit, Source
from sag_api.enums import SourceStatus
from sag_api.research.brief import (
    canonicalize_evidence_spec,
    validate_research_brief,
)
from sag_api.research.brief_synthesizer import humanize_visible_reasoning, run_brief_centric_turn
from sag_api.research.market_pulse import clear_market_pulse_cache
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def _principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-p3",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-p3",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"all", "*"}),
        conversation_scope="all",
    )


def test_l1_pulse_evidence_specs_and_market_sector_brief_validation() -> None:
    c1, w1 = canonicalize_evidence_spec("pulse:market")
    c2, w2 = canonicalize_evidence_spec("pulse:sector|Ngân hàng")
    assert c1 == "pulse:market" and w1 is None
    assert c2 == "pulse:sector|Ngân hàng" and w2 is None

    # Market-wide brief with `tickers=[]` and `pulse:market` MUST validate as actionable (needs_clarification=False)!
    raw_mkt = {
        "user_concern": "Đánh giá toàn cảnh thị trường hiện tại, dòng tiền và nhóm ngành dẫn dắt",
        "answer_shape": "market_outlook",
        "entities": {"tickers": [], "anchor_ticker": None, "candidate_search": None},
        "premises_to_check": [],
        "claims": [
            {
                "id": "K1",
                "statement": "Nhịp đập VN-Index, thanh khoản, khối ngoại và phân hóa lợi nhuận các nhóm ngành",
                "evidence": ["pulse:market"],
                "period": "Q3/2026",
            }
        ],
        "ambiguities": [],
        "assumptions": [],
        "needs_clarification": False,
        "clarifying_question": None,
        "budget_tier": "standard",
    }
    out = validate_research_brief(raw_mkt, as_of_date=date(2026, 11, 28))
    assert out.ok and out.brief is not None
    assert out.brief.needs_clarification is False
    assert out.brief.answer_shape == "market_outlook"

    # Visible reasoning scrubber must never leak `market_outlook`, `sector_pulse`, or `pulse:market`
    scrubbed = humanize_visible_reasoning("Chọn market_outlook và gọi `pulse:market` cùng sector_pulse.")
    assert "market_outlook" not in scrubbed
    assert "sector_pulse" not in scrubbed
    assert "pulse:market" not in scrubbed


@pytest.mark.asyncio
async def test_l3_market_turn_and_pointer_seed_injection_end_to_end(tmp_path) -> None:
    """Verify an unanchored Market-wide query (`tickers=[]`) receives the deterministic Market Snapshot
    in Stage 1, registers `[CALC-*]` traces via `pulse:market`, injects bellwether `StructuralPointer`
    `[C#]` sections without Qdrant guesswork, and completes synthesis cleanly.
    """
    clear_market_pulse_cache()
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p3_e2e.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    async with sf() as s:
        s.add(Source(id="src-p3", name="HOSE", sag_source_config_id="cfg-p3", status=SourceStatus.ACTIVE))
        s.add(
            Document(
                id="doc-fpt-agm",
                source_id="src-p3",
                project_id="FPT",
                tenant_id="aiinvest-default",
                filename="FPT_AGM_2026.pdf",
                storage_path="/fpt_agm.pdf",
                is_active=True,
            )
        )
        s.add(
            DocumentVersion(
                id="ver-fpt-agm",
                document_id="doc-fpt-agm",
                version_no=1,
                file_hash="fh-agm",
                status="READY",
                search_status="SEARCH_READY",
                knowledge_status="DATA_READY",
                source_published_at=_utc(2026, 4, 15),
                observed_at=_utc(2026, 4, 15),
                valid_from=_utc(2026, 4, 15),
                valid_to=_utc(9999, 12, 31),
                metadata_json={
                    "security_partition_id": "all",
                    "sub_doc_slot": "AGM_TARGETS",
                    "fiscal_year": 2026,
                    "fiscal_quarter": 0,
                },
            )
        )
        await s.flush()
        agm_txt = (
            "Phần thảo luận tại ĐHĐCĐ 2026: Ban lãnh đạo FPT cho biết Nghị quyết 57 và cải cách định mức "
            "nhân công CNTT khu vực công giúp biên lợi nhuận mảng CNTT trong nước cải thiện mạnh."
        )
        s.add(
            CanonicalBlock(
                id="blk-fpt-agm-30",
                document_version_id="ver-fpt-agm",
                ordinal=1,
                block_type="paragraph",
                normalized_text=agm_txt,
                content_hash="ch-agm-30",
                page_from=30,
                page_to=30,
                section_path="AGM_TARGETS > PHẦN THẢO LUẬN ĐHĐCĐ",
            )
        )
        await s.flush()
        s.add(
            KnowledgeUnit(
                id="ku-fpt-agm-30",
                document_version_id="ver-fpt-agm",
                tenant_id="aiinvest-default",
                project_id="FPT",
                source_id="src-p3",
                security_partition_id="all",
                ordinal=1,
                text=agm_txt,
                content_hash="ch-agm-30",
                input_checksum="ic-30",
                checksum="c-30",
                is_current=True,
                provenance_json={
                    "document_id": "doc-fpt-agm",
                    "evidence_block_ids": ["blk-fpt-agm-30"],
                    "page_from": 30,
                    "page_to": 30,
                    "section_path": "AGM_TARGETS > PHẦN THẢO LUẬN ĐHĐCĐ",
                    "sub_doc_slot": "AGM_TARGETS",
                },
                features_json={"entities": ["FPT", "Nghị quyết 57"], "structural_signals": ["AGM_QA", "SEGMENT_MDNA"]},
                valid_from=_utc(2026, 4, 15),
                valid_to=_utc(9999, 12, 31),
            )
        )
        await s.commit()

    class _MiniCore:
        async def fetch_issuer_profiles(self, tickers=None):
            del tickers
            return [
                {
                    "ticker": "FPT",
                    "company_name": "CTCP FPT",
                    "sector": "TECHNOLOGY",
                    "industry": "Công nghệ",
                    "market_cap": 195_000e9,
                },
                {
                    "ticker": "ACB",
                    "company_name": "ACB",
                    "sector": "BANKS",
                    "industry": "Ngân hàng",
                    "market_cap": 115_000e9,
                },
            ]

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {"FPT": 195_000e9, "ACB": 115_000e9}

        async def fetch_pit_financial_ratios(self, as_of_date, *, tickers=None, lookback_years=7):
            del as_of_date, tickers, lookback_years
            return [
                {
                    "symbol": "FPT",
                    "ratio_date": date(2026, 9, 30),
                    "frequency": "quarterly",
                    "pe": 22.0,
                    "pb": 5.8,
                    "roe": 0.28,
                    "roa": 0.12,
                    "debt_equity": 0.95,
                    "current_ratio": 1.4,
                    "gross_margin": 0.39,
                    "net_margin": 0.15,
                    "fcf_yield": 0.04,
                    "ev_ebitda": 15.0,
                    "yoy_revenue_growth": 0.17,
                    "yoy_earnings_growth": 0.21,
                    "published_date": date(2026, 10, 24),
                    "effective_published_date": date(2026, 10, 24),
                }
            ]

        async def fetch_pit_financial_statements(
            self, as_of_date, *, tickers=None, lookback_years=5, prefer_annual=True, statement_types=("BS", "IS", "CF")
        ):
            del as_of_date, tickers, lookback_years, prefer_annual, statement_types
            return []

        async def fetch_pit_market_and_macro_tape(self, as_of_date, *, tickers=None):
            del as_of_date, tickers
            return {
                "latest_trade_date": date(2026, 11, 18),
                "vnindex_close": 1388.2,
                "vnindex_ret_1d": 0.006,
                "vnindex_ret_1m": 0.048,
                "vnindex_ret_3m": 0.092,
                "liquidity_vs_20d_ratio": 1.18,
                "advancers": 245,
                "decliners": 112,
                "foreign_net_5d_vnd": 1_350_000_000_000.0,
                "foreign_net_20d_vnd": 3_800_000_000_000.0,
                "foreign_net_5d_by_sector": {"TECHNOLOGY": 410_000_000_000.0},
                "sector_ret_1m": {"TECHNOLOGY": 0.082},
                "regime": {
                    "date": date(2026, 11, 5),
                    "current_regime": "BULL_MARKET",
                    "vix_vn_analog": 13.8,
                    "breadth_above_ma50_pct": 64.2,
                },
                "macro": {
                    "usd_vnd_exchange": {
                        "latest": 25_280.0,
                        "date": date(2026, 11, 18),
                        "change_1m_pct": -0.005,
                        "unit": "VND",
                    }
                },
            }

    seen_stage1_prompts: list[str] = []

    class _ScriptedModel(ModelProvider):
        async def stream_turn(self, request: ModelRequest, cancel: CancellationToken) -> AsyncIterator[ModelChunk]:
            del cancel
            sys_txt = request.messages[0].content if request.messages else ""
            usr_txt = request.messages[-1].content if request.messages else ""
            if "Chief Research Brief Architect" in sys_txt:
                seen_stage1_prompts.append(usr_txt)
                out = json.dumps(
                    {
                        "user_concern": "Toàn cảnh thị trường và động lực chính sách của nhóm đầu ngành",
                        "answer_shape": "market_outlook",
                        "entities": {"tickers": [], "anchor_ticker": None, "candidate_search": None},
                        "premises_to_check": [],
                        "claims": [
                            {
                                "id": "K1",
                                "statement": "Nhịp đập VN-Index, thanh khoản và phân hóa ngành",
                                "evidence": ["pulse:market"],
                                "period": "Q3/2026",
                            },
                            {
                                "id": "K2",
                                "statement": "Động lực chính sách nổi bật từ ĐHĐCĐ doanh nghiệp đầu ngành",
                                "evidence": ["doc:AGM_TARGETS|Nghị quyết 57 CNTT khu vực công"],
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
            elif "Forensic Disclosure Mini-Reader" in sys_txt:
                out = json.dumps(
                    {
                        "status": "CONFIRMED",
                        "findings": [
                            "Nghị quyết 57 và cải cách định mức nhân công CNTT khu vực công giúp "
                            "biên mảng CNTT trong nước của FPT cải thiện mạnh [C1]."
                        ],
                        "anomaly_followup_query": None,
                    },
                    ensure_ascii=False,
                )
            else:
                out = (
                    "VN-Index đóng cửa tại 1.388,2 điểm (+4,8% 1M) với thanh khoản bằng 1,18x "
                    "trung bình 20 phiên [CALC-1]. Ở nhóm đầu ngành đã công bố tài liệu, FPT hưởng lợi "
                    "từ Nghị quyết 57 và cải cách định mức nhân công CNTT khu vực công [C1]."
                )
            yield ModelChunk(text_delta=out, usage=Usage(requests=1, input_tokens=120, output_tokens=60))

    core = _MiniCore()
    ctx = ResearchToolContext(
        as_of_date=date(2026, 11, 28),
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=date(2026, 11, 28)),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"FPT"}),
        catalog_profiles=tuple(await core.fetch_issuer_profiles()),
        core_reader=core,
    )

    res = await run_brief_centric_turn(
        query="Thị trường chứng khoán hiện tại đang ra sao?",
        model=_ScriptedModel(),
        ctx=ctx,
        route_decision={"task_action": "market_overview", "attach_investment_disclaimer": False},
        explicit_tickers=[],
    )

    # 1) Stage 1 prompt received the deterministic Market World-State Snapshot BEFORE planning!
    assert seen_stage1_prompts
    assert "1,388.2" in seen_stage1_prompts[0] or "1.388,2" in seen_stage1_prompts[0]
    assert "Nghị quyết 57" in seen_stage1_prompts[0]

    # 2) `pulse:market` registered `[CALC-1]` and Pointer-Guided Seed Injection populated `[C1]` even without Qdrant!
    assert len(ctx.evidence.calculator_traces) >= 1
    assert len(ctx.evidence.sections) >= 1
    assert "Nghị quyết 57" in ctx.evidence.sections[0].content
    assert "[CALC-1]" in res.outcome.answer_text and "[C1]" in res.outcome.answer_text

    await eng.dispose()


@pytest.mark.asyncio
async def test_l2_quant_anomaly_triggers_pass2_causal_drilldown(tmp_path) -> None:
    """Verify a quantitative segment margin jump (`>= 150 bps`) in `BusinessDriverEngine` automatically
    triggers code-owned Pass-2 causal escalation (`escalations_run == 1`) into `AGM_TARGETS`/`BCTC_AUDITED`
    even when Pass-1 Mini-Reader left `anomaly_followup_query=null`.
    """
    from sag_api.research.brief import BriefEntities, ClaimSpec, ResearchBrief
    from sag_api.research.brief_executor import LocatedTargets, execute_brief_claims
    from sag_api.research.market_pulse import ScopeBriefingBundle, StructuralPointer

    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'p3_hop2.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    async with sf() as s:
        s.add(Source(id="src-h2", name="HOSE", sag_source_config_id="cfg-h2", status=SourceStatus.ACTIVE))
        s.add_all(
            [
                Document(
                    id="doc-q3",
                    source_id="src-h2",
                    project_id="FPT",
                    tenant_id="aiinvest-default",
                    filename="FPT_Q3.pdf",
                    storage_path="/q3.pdf",
                    is_active=True,
                ),
                Document(
                    id="doc-agm",
                    source_id="src-h2",
                    project_id="FPT",
                    tenant_id="aiinvest-default",
                    filename="FPT_AGM.pdf",
                    storage_path="/agm.pdf",
                    is_active=True,
                ),
            ]
        )
        s.add_all(
            [
                DocumentVersion(
                    id="ver-q3",
                    document_id="doc-q3",
                    version_no=1,
                    file_hash="fh-q3",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="DATA_READY",
                    source_published_at=_utc(2026, 10, 24),
                    observed_at=_utc(2026, 10, 24),
                    valid_from=_utc(2026, 10, 24),
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": "QUARTER_NOTES",
                        "fiscal_year": 2026,
                        "fiscal_quarter": 3,
                    },
                ),
                DocumentVersion(
                    id="ver-agm",
                    document_id="doc-agm",
                    version_no=1,
                    file_hash="fh-agm",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="DATA_READY",
                    source_published_at=_utc(2026, 4, 15),
                    observed_at=_utc(2026, 4, 15),
                    valid_from=_utc(2026, 4, 15),
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": "BCTC_AUDITED",
                        "fiscal_year": 2025,
                        "fiscal_quarter": 0,
                    },
                ),
            ]
        )
        await s.flush()
        s.add_all(
            [
                CanonicalBlock(
                    id="blk-q3-1",
                    document_version_id="ver-q3",
                    ordinal=1,
                    block_type="paragraph",
                    normalized_text="Doanh thu mảng CNTT trong nước Q3/2026 đạt 2.400 tỷ đồng.",
                    content_hash="ch-q3-1",
                    page_from=12,
                    page_to=12,
                    section_path="QUARTER_NOTES > TM DOANH THU",
                ),
                CanonicalBlock(
                    id="blk-agm-1",
                    document_version_id="ver-agm",
                    ordinal=1,
                    block_type="paragraph",
                    normalized_text=(
                        "Phần thảo luận ĐHĐCĐ: Biên lợi nhuận CNTT trong nước tăng +333 bps nhờ Nghị quyết 57 "
                        "và cải cách định mức đơn giá nhân công dự án khu vực công."
                    ),
                    content_hash="ch-agm-1",
                    page_from=30,
                    page_to=30,
                    section_path="AGM_TARGETS > PHẦN THẢO LUẬN",
                ),
            ]
        )
        await s.flush()
        s.add_all(
            [
                KnowledgeUnit(
                    id="ku-q3-1",
                    document_version_id="ver-q3",
                    tenant_id="aiinvest-default",
                    project_id="FPT",
                    source_id="src-h2",
                    security_partition_id="all",
                    ordinal=1,
                    text="Doanh thu mảng CNTT trong nước Q3/2026 đạt 2.400 tỷ đồng.",
                    content_hash="ch-q3-1",
                    input_checksum="ic1",
                    checksum="c1",
                    is_current=True,
                    provenance_json={
                        "document_id": "doc-q3",
                        "evidence_block_ids": ["blk-q3-1"],
                        "page_from": 12,
                        "page_to": 12,
                        "section_path": "QUARTER_NOTES > TM DOANH THU",
                        "sub_doc_slot": "QUARTER_NOTES",
                    },
                    features_json={"entities": ["FPT"], "structural_signals": ["SEGMENT_MDNA"]},
                    valid_from=_utc(2026, 10, 24),
                    valid_to=_utc(9999, 12, 31),
                ),
                KnowledgeUnit(
                    id="ku-agm-1",
                    document_version_id="ver-agm",
                    tenant_id="aiinvest-default",
                    project_id="FPT",
                    source_id="src-h2",
                    security_partition_id="all",
                    ordinal=1,
                    text=(
                        "Phần thảo luận ĐHĐCĐ: Biên lợi nhuận CNTT trong nước tăng +333 bps nhờ Nghị quyết 57 "
                        "và cải cách định mức đơn giá nhân công dự án khu vực công."
                    ),
                    content_hash="ch-agm-1",
                    input_checksum="ic2",
                    checksum="c2",
                    is_current=True,
                    provenance_json={
                        "document_id": "doc-agm",
                        "evidence_block_ids": ["blk-agm-1"],
                        "page_from": 30,
                        "page_to": 30,
                        "section_path": "BCTC_AUDITED > THUYẾT MINH BỘ PHẬN",
                        "sub_doc_slot": "BCTC_AUDITED",
                    },
                    features_json={"entities": ["FPT", "Nghị quyết 57"], "structural_signals": ["AGM_QA"]},
                    valid_from=_utc(2026, 4, 15),
                    valid_to=_utc(9999, 12, 31),
                ),
            ]
        )
        await s.commit()

    class _TableStoreStub:
        async def get_tables(self, **_kwargs):
            return [
                {
                    "table_id": "tbl-seg-q3-26",
                    "project_id": "FPT",
                    "title": "4. Báo cáo bộ phận 9T/2026",
                    "sub_doc_slot": "QUARTER_NOTES",
                    "fiscal_year": 2026,
                    "fiscal_quarter": 3,
                    "page_from": 29,
                    "verification_status": "VERIFIED",
                    "unit_multiplier": 1_000_000.0,
                    "headers": ["Chỉ tiêu", "CNTT nước ngoài", "CNTT trong nước", "Tổng cộng"],
                    "rows": [
                        [
                            {"raw_text": "Doanh thu theo bộ phận", "numeric_value": None},
                            {"raw_text": "24.000", "numeric_value": 24000.0},
                            {"raw_text": "6.000", "numeric_value": 6000.0},
                            {"raw_text": "30.000", "numeric_value": 30000.0},
                        ],
                        [
                            {"raw_text": "Kết quả hoạt động kinh doanh theo bộ phận", "numeric_value": None},
                            {"raw_text": "4.080", "numeric_value": 4080.0},
                            {"raw_text": "720", "numeric_value": 720.0},
                            {"raw_text": "4.800", "numeric_value": 4800.0},
                        ],
                    ],
                },
                {
                    "table_id": "tbl-seg-q3-25",
                    "project_id": "FPT",
                    "title": "4. Báo cáo bộ phận 9T/2025",
                    "sub_doc_slot": "QUARTER_NOTES",
                    "fiscal_year": 2025,
                    "fiscal_quarter": 3,
                    "page_from": 30,
                    "verification_status": "VERIFIED",
                    "unit_multiplier": 1_000_000.0,
                    "headers": ["Chỉ tiêu", "CNTT nước ngoài", "CNTT trong nước", "Tổng cộng"],
                    "rows": [
                        [
                            {"raw_text": "Doanh thu theo bộ phận", "numeric_value": None},
                            {"raw_text": "20.500", "numeric_value": 20500.0},
                            {"raw_text": "5.500", "numeric_value": 5500.0},
                            {"raw_text": "26.000", "numeric_value": 26000.0},
                        ],
                        [
                            {"raw_text": "Kết quả hoạt động kinh doanh theo bộ phận", "numeric_value": None},
                            {"raw_text": "3.485", "numeric_value": 3485.0},
                            {"raw_text": "476,85", "numeric_value": 476.85},
                            {"raw_text": "3.961,85", "numeric_value": 3961.85},
                        ],
                    ],
                },
            ]

    reader_calls = 0

    class _Hop2ReaderModel(ModelProvider):
        async def stream_turn(self, request: ModelRequest, cancel: CancellationToken) -> AsyncIterator[ModelChunk]:
            nonlocal reader_calls
            del cancel, request
            reader_calls += 1
            if reader_calls == 1:
                # Pass 1 returns NO anomaly_followup_query, yet +333 bps margin jump MUST trigger code-owned Pass 2!
                out = json.dumps(
                    {
                        "status": "PARTIAL",
                        "findings": ["Doanh thu mảng CNTT trong nước Q3/2026 đạt 2.400 tỷ đồng [C1]."],
                        "anomaly_followup_query": None,
                    },
                    ensure_ascii=False,
                )
            else:
                out = json.dumps(
                    {
                        "status": "CONFIRMED",
                        "findings": [
                            "Biên lợi nhuận CNTT trong nước tăng +333 bps nhờ Nghị quyết 57 và "
                            "cải cách định mức đơn giá nhân công dự án khu vực công [C2]."
                        ],
                        "anomaly_followup_query": None,
                    },
                    ensure_ascii=False,
                )
            yield ModelChunk(text_delta=out, usage=Usage(requests=1, input_tokens=80, output_tokens=40))

    pulse = ScopeBriefingBundle(
        lens="COMPANY",
        as_of_date=date(2026, 11, 28),
        target_tickers=["FPT"],
        bellwether_tickers=["FPT"],
        structural_pointers=[
            StructuralPointer(
                unit_id="ku-q3-1",
                document_version_id="ver-q3",
                ticker="FPT",
                sub_doc_slot="QUARTER_NOTES",
                fiscal_year=2026,
                fiscal_quarter=3,
                page_from=12,
                section_path="QUARTER_NOTES > TM DOANH THU",
                signals=("SEGMENT_MDNA",),
                entities=("FPT",),
                excerpt="Doanh thu mảng CNTT trong nước Q3/2026 đạt 2.400 tỷ đồng.",
                block_ids=("blk-q3-1",),
            ),
            StructuralPointer(
                unit_id="ku-agm-1",
                document_version_id="ver-agm",
                ticker="FPT",
                sub_doc_slot="BCTC_AUDITED",
                fiscal_year=2025,
                fiscal_quarter=0,
                page_from=30,
                section_path="BCTC_AUDITED > THUYẾT MINH BỘ PHẬN",
                signals=("SEGMENT_MDNA",),
                entities=("FPT", "Nghị quyết 57"),
                excerpt="Biên lợi nhuận CNTT trong nước tăng +333 bps nhờ Nghị quyết 57...",
                block_ids=("blk-agm-1",),
            ),
        ],
    )

    brief = ResearchBrief(
        user_concern="Phân tích kết quả kinh doanh FPT",
        answer_shape="health_check",
        entities=BriefEntities(tickers=["FPT"]),
        claims=[
            ClaimSpec(
                id="K1",
                statement="Kết quả doanh thu và biên lợi nhuận theo mảng của FPT",
                evidence=["doc:QUARTER_NOTES|doanh thu mảng CNTT"],
                period="Q3/2026",
            )
        ],
    )
    ctx = ResearchToolContext(
        as_of_date=date(2026, 11, 28),
        principal=_principal(),
        chat_mode="GENERAL",
        session_factory=sf,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=date(2026, 11, 28)),
        evidence=ResearchEvidenceLedger(),
        available_tickers=frozenset({"FPT"}),
        table_store_override=_TableStoreStub(),
    )

    exec_res = await execute_brief_claims(
        brief=brief,
        located=LocatedTargets(analysis_tickers=["FPT"]),
        ctx=ctx,
        model=_Hop2ReaderModel(),
        pulse_bundle=pulse,
    )

    assert exec_res.escalations_run == 1
    assert exec_res.claim_cards[0].escalated is True
    assert any("Nghị quyết 57" in ln for ln in exec_res.claim_cards[0].summary_lines)

    await eng.dispose()
