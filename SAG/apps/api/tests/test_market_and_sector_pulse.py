"""Phase 2 TDD Suite: Deterministic Scope-Gated Market & Sector Pulse Reader (`0` LLM tokens, `0%` PIT leak).

Covers:
1. L1 `resolve_query_scope_lens` across COMPANY, SECTOR, MARKET, and OPEN_STRATEGY queries (zero anchoring bias).
2. L1/L2 strict PIT cutoff (`as_of_date`) on Market Tape (`VNINDEX`, ADTV liquidity, foreign flow, macro, regime)
   and Sector Breadth/Valuation (`full_sector_count` vs `indexed_bellwether_tickers` separation).
3. L2 Stage-7 `[CALC-*]` trace registration guaranteeing 100% numeric audit pass on Market/Sector snapshots.
4. L2 graceful offline degradation when `core_reader=None`.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import CanonicalBlock, Document, DocumentVersion, KnowledgeUnit, Source
from sag_api.enums import SourceStatus
from sag_api.research.market_pulse import (
    build_scope_gated_briefing,
    clear_market_pulse_cache,
    register_pulse_evidence_into_ledger,
    resolve_query_scope_lens,
)
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext


def _utc(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


def _principal() -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-pulse",
        organization_id="aiinvest-default",
        allowed_project_ids=frozenset({"*"}),
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-pulse",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"all", "*"}),
        conversation_scope="all",
    )


class _PitFakeCoreReader:
    """Implements full `CoreMetricsReaderProtocol` with historical vs future rows to verify `0%` PIT leakage."""

    async def fetch_issuer_profiles(self, tickers=None):
        rows = [
            {
                "ticker": "ACB",
                "company_name": "Ngân hàng Á Châu",
                "sector": "BANKS",
                "industry": "Ngân hàng",
                "market_cap": 115_000e9,
            },
            {
                "ticker": "MBB",
                "company_name": "Ngân hàng Quân Đội",
                "sector": "BANKS",
                "industry": "Ngân hàng",
                "market_cap": 135_000e9,
            },
            {
                "ticker": "TCB",
                "company_name": "Techcombank",
                "sector": "BANKS",
                "industry": "Ngân hàng",
                "market_cap": 165_000e9,
            },
            {
                "ticker": "STB",
                "company_name": "Sacombank",
                "sector": "BANKS",
                "industry": "Ngân hàng",
                "market_cap": 68_000e9,
            },
            {
                "ticker": "FPT",
                "company_name": "CTCP FPT",
                "sector": "TECHNOLOGY",
                "industry": "Công nghệ",
                "market_cap": 195_000e9,
            },
            {
                "ticker": "MWG",
                "company_name": "Thế Giới Di Động",
                "sector": "RETAIL_TRADE",
                "industry": "Bán lẻ",
                "market_cap": 98_000e9,
            },
            {
                "ticker": "FRT",
                "company_name": "FPT Retail",
                "sector": "RETAIL_TRADE",
                "industry": "Bán lẻ",
                "market_cap": 25_000e9,
            },
        ]
        if tickers:
            want = {t.upper() for t in tickers}
            return [r for r in rows if r["ticker"] in want]
        return rows

    async def fetch_pit_market_caps(self, as_of_date: date, *, tickers=None):
        del tickers
        if as_of_date <= date(2025, 12, 31):
            return {
                "ACB": 95_000e9,
                "MBB": 105_000e9,
                "TCB": 120_000e9,
                "STB": 55_000e9,
                "FPT": 150_000e9,
                "MWG": 72_000e9,
                "FRT": 19_000e9,
            }
        return {
            "ACB": 115_000e9,
            "MBB": 135_000e9,
            "TCB": 165_000e9,
            "STB": 68_000e9,
            "FPT": 195_000e9,
            "MWG": 98_000e9,
            "FRT": 25_000e9,
        }

    async def fetch_pit_financial_ratios(self, as_of_date: date, *, tickers=None, lookback_years=7):
        del tickers, lookback_years
        all_rows = [
            # Historical 2025 Q3 (published 2025-10-25)
            {
                "symbol": "ACB",
                "ratio_date": date(2025, 9, 30),
                "frequency": "quarterly",
                "pe": 6.4,
                "pb": 1.35,
                "roe": 0.215,
                "roa": 0.021,
                "debt_equity": 8.2,
                "current_ratio": None,
                "gross_margin": None,
                "net_margin": 0.42,
                "fcf_yield": None,
                "ev_ebitda": None,
                "yoy_revenue_growth": 0.09,
                "yoy_earnings_growth": 0.08,
                "published_date": date(2025, 10, 25),
                "effective_published_date": date(2025, 10, 25),
            },
            {
                "symbol": "MBB",
                "ratio_date": date(2025, 9, 30),
                "frequency": "quarterly",
                "pe": 5.9,
                "pb": 1.25,
                "roe": 0.225,
                "roa": 0.022,
                "debt_equity": 8.8,
                "current_ratio": None,
                "gross_margin": None,
                "net_margin": 0.44,
                "fcf_yield": None,
                "ev_ebitda": None,
                "yoy_revenue_growth": 0.14,
                "yoy_earnings_growth": 0.16,
                "published_date": date(2025, 10, 26),
                "effective_published_date": date(2025, 10, 26),
            },
            {
                "symbol": "TCB",
                "ratio_date": date(2025, 9, 30),
                "frequency": "quarterly",
                "pe": 7.1,
                "pb": 1.18,
                "roe": 0.168,
                "roa": 0.024,
                "debt_equity": 5.9,
                "current_ratio": None,
                "gross_margin": None,
                "net_margin": 0.46,
                "fcf_yield": None,
                "ev_ebitda": None,
                "yoy_revenue_growth": 0.17,
                "yoy_earnings_growth": 0.20,
                "published_date": date(2025, 10, 24),
                "effective_published_date": date(2025, 10, 24),
            },
            {
                "symbol": "STB",
                "ratio_date": date(2025, 9, 30),
                "frequency": "quarterly",
                "pe": 7.8,
                "pb": 1.22,
                "roe": 0.175,
                "roa": 0.013,
                "debt_equity": 11.5,
                "current_ratio": None,
                "gross_margin": None,
                "net_margin": 0.31,
                "fcf_yield": None,
                "ev_ebitda": None,
                "yoy_revenue_growth": 0.05,
                "yoy_earnings_growth": -0.06,
                "published_date": date(2025, 10, 28),
                "effective_published_date": date(2025, 10, 28),
            },
            {
                "symbol": "FPT",
                "ratio_date": date(2025, 9, 30),
                "frequency": "quarterly",
                "pe": 21.5,
                "pb": 5.4,
                "roe": 0.278,
                "roa": 0.118,
                "debt_equity": 1.05,
                "current_ratio": 1.32,
                "gross_margin": 0.385,
                "net_margin": 0.142,
                "fcf_yield": 0.038,
                "ev_ebitda": 14.2,
                "yoy_revenue_growth": 0.185,
                "yoy_earnings_growth": 0.205,
                "published_date": date(2025, 10, 22),
                "effective_published_date": date(2025, 10, 22),
            },
            # 2026 Q3 (published 2026-10-25 — MUST NOT leak when as_of_date == 2025-11-15!)
            {
                "symbol": "FPT",
                "ratio_date": date(2026, 9, 30),
                "frequency": "quarterly",
                "pe": 24.8,
                "pb": 6.1,
                "roe": 0.289,
                "roa": 0.125,
                "debt_equity": 0.96,
                "current_ratio": 1.45,
                "gross_margin": 0.394,
                "net_margin": 0.156,
                "fcf_yield": 0.041,
                "ev_ebitda": 15.6,
                "yoy_revenue_growth": 0.168,
                "yoy_earnings_growth": 0.224,
                "published_date": date(2026, 10, 25),
                "effective_published_date": date(2026, 10, 25),
            },
        ]
        return [r for r in all_rows if r["effective_published_date"] <= as_of_date]

    async def fetch_pit_financial_statements(
        self,
        as_of_date: date,
        *,
        tickers=None,
        lookback_years=5,
        prefer_annual=True,
        statement_types=("BS", "IS", "CF"),
    ):
        del as_of_date, tickers, lookback_years, prefer_annual, statement_types
        return []

    async def fetch_pit_market_and_macro_tape(self, as_of_date: date, *, tickers=None) -> dict[str, Any]:
        del tickers
        if as_of_date <= date(2025, 12, 31):
            return {
                "latest_trade_date": date(2025, 11, 14),
                "vnindex_close": 1245.5,
                "vnindex_ret_1d": -0.0085,
                "vnindex_ret_1m": -0.032,
                "vnindex_ret_3m": 0.021,
                "liquidity_vs_20d_ratio": 0.88,
                "advancers": 142,
                "decliners": 218,
                "foreign_net_5d_vnd": -2_450_000_000_000.0,
                "foreign_net_20d_vnd": -8_120_000_000_000.0,
                "foreign_net_5d_by_sector": {"BANKS": -620_000_000_000.0, "TECHNOLOGY": 180_000_000_000.0},
                "sector_ret_1m": {"BANKS": -0.018, "TECHNOLOGY": 0.045, "RETAIL_TRADE": -0.025},
                "regime": {
                    "date": date(2025, 11, 14),
                    "current_regime": "RANGE_BOUND",
                    "vix_vn_analog": 16.4,
                    "breadth_above_ma50_pct": 38.5,
                },
                "macro": {
                    "usd_vnd_exchange": {
                        "latest": 25_410.0,
                        "date": date(2025, 11, 14),
                        "change_1m_pct": 0.012,
                        "unit": "VND",
                    },
                    "vninbr_interbank_rate": {
                        "latest": 4.65,
                        "date": date(2025, 11, 14),
                        "change_1m_pct": 0.08,
                        "unit": "%",
                    },
                },
            }
        return {
            "latest_trade_date": date(2026, 11, 18),
            "vnindex_close": 1388.2,
            "vnindex_ret_1d": 0.0064,
            "vnindex_ret_1m": 0.048,
            "vnindex_ret_3m": 0.092,
            "liquidity_vs_20d_ratio": 1.18,
            "advancers": 245,
            "decliners": 112,
            "foreign_net_5d_vnd": 1_350_000_000_000.0,
            "foreign_net_20d_vnd": 3_800_000_000_000.0,
            "foreign_net_5d_by_sector": {
                "BANKS": 890_000_000_000.0,
                "TECHNOLOGY": 410_000_000_000.0,
                "RETAIL_TRADE": 220_000_000_000.0,
            },
            "sector_ret_1m": {"BANKS": 0.056, "TECHNOLOGY": 0.082, "RETAIL_TRADE": 0.061},
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
                },
                "oil_price_brent": {
                    "latest": 72.4,
                    "date": date(2026, 11, 18),
                    "change_1m_pct": -0.042,
                    "unit": "USD/bbl",
                },
            },
        }


def test_l1_resolve_query_scope_lens_prevents_anchoring_bias() -> None:
    profiles = [
        {"ticker": "FPT", "company_name": "CTCP FPT", "sector": "TECHNOLOGY", "industry": "Công nghệ"},
        {"ticker": "ACB", "company_name": "Ngân hàng Á Châu", "sector": "BANKS", "industry": "Ngân hàng"},
    ]

    r_co = resolve_query_scope_lens(
        query="Phân tích chi tiết động lực tăng trưởng của FPT quý gần nhất",
        explicit_tickers=[],
        fallback_tickers=[],
        catalog_profiles=profiles,
    )
    assert r_co.lens == "COMPANY"
    assert r_co.matched_tickers == ["FPT"]

    r_sec = resolve_query_scope_lens(
        query="Ngành ngân hàng hiện tại đang phân hóa thế nào?",
        explicit_tickers=[],
        fallback_tickers=[],
        catalog_profiles=profiles,
    )
    assert r_sec.lens == "SECTOR"
    assert "BANKS" in r_sec.matched_sectors

    r_mkt = resolve_query_scope_lens(
        query="Thị trường chứng khoán hiện tại đang ra sao, dòng tiền ở đâu?",
        explicit_tickers=[],
        fallback_tickers=[],
        catalog_profiles=profiles,
    )
    assert r_mkt.lens == "MARKET"

    r_open = resolve_query_scope_lens(
        query="Giờ mua cổ phiếu gì tốt?",
        explicit_tickers=[],
        fallback_tickers=[],
        catalog_profiles=profiles,
    )
    assert r_open.lens == "OPEN_STRATEGY"


@pytest.mark.asyncio
async def test_l2_scope_gated_briefing_pit_safety_and_bellwether_separation(tmp_path) -> None:
    clear_market_pulse_cache()
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pulse.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)

    async with sf() as s:
        s.add(Source(id="src-1", name="HOSE", sag_source_config_id="cfg-1", status=SourceStatus.ACTIVE))
        for tk, vid, pub in (
            ("ACB", "ver-acb-25", _utc(2025, 10, 26)),
            ("FPT", "ver-fpt-26", _utc(2026, 10, 24)),  # 2026 doc -> MUST NOT leak into 2025 replay!
        ):
            s.add(
                Document(
                    id=f"doc-{tk}",
                    source_id="src-1",
                    project_id=tk,
                    tenant_id="aiinvest-default",
                    filename=f"{tk}.pdf",
                    storage_path=f"/{tk}.pdf",
                    is_active=True,
                )
            )
            s.add(
                DocumentVersion(
                    id=vid,
                    document_id=f"doc-{tk}",
                    version_no=1,
                    file_hash=f"fh-{vid}",
                    status="READY",
                    search_status="SEARCH_READY",
                    knowledge_status="DATA_READY",
                    source_published_at=pub,
                    observed_at=pub,
                    valid_from=pub,
                    valid_to=_utc(9999, 12, 31),
                    metadata_json={
                        "security_partition_id": "all",
                        "sub_doc_slot": "AGM_TARGETS",
                        "fiscal_year": pub.year,
                        "fiscal_quarter": 0,
                    },
                )
            )
        await s.flush()
        s.add_all(
            [
                CanonicalBlock(
                    id="blk-acb",
                    document_version_id="ver-acb-25",
                    ordinal=1,
                    block_type="paragraph",
                    normalized_text=(
                        "Phần thảo luận ĐHĐCĐ ACB: Ban lãnh đạo ưu tiên giữ tỷ lệ nợ xấu dưới 1,5% "
                        "và mở rộng CASA bán lẻ."
                    ),
                    content_hash="ch-acb",
                    page_from=12,
                    page_to=12,
                    section_path="AGM_TARGETS > PHẦN THẢO LUẬN",
                ),
                CanonicalBlock(
                    id="blk-fpt-26",
                    document_version_id="ver-fpt-26",
                    ordinal=1,
                    block_type="paragraph",
                    normalized_text="SCIC chuyển giao 50,16% vốn FTEL sang Bộ Công an năm 2026.",
                    content_hash="ch-fpt",
                    page_from=176,
                    page_to=176,
                    section_path="BCTC_AUDITED > CẤU TRÚC TẬP ĐOÀN",
                ),
            ]
        )
        await s.flush()
        s.add_all(
            [
                KnowledgeUnit(
                    id="ku-acb-25",
                    document_version_id="ver-acb-25",
                    tenant_id="aiinvest-default",
                    project_id="ACB",
                    source_id="src-1",
                    security_partition_id="all",
                    ordinal=1,
                    text=(
                        "Phần thảo luận ĐHĐCĐ ACB: Ban lãnh đạo ưu tiên giữ tỷ lệ nợ xấu dưới 1,5% "
                        "và mở rộng CASA bán lẻ."
                    ),
                    content_hash="ch-acb",
                    input_checksum="ic-acb",
                    checksum="c-acb",
                    is_current=True,
                    provenance_json={
                        "document_id": "doc-ACB",
                        "evidence_block_ids": ["blk-acb"],
                        "page_from": 12,
                        "page_to": 12,
                        "section_path": "AGM_TARGETS > PHẦN THẢO LUẬN",
                        "sub_doc_slot": "AGM_TARGETS",
                    },
                    features_json={"entities": ["ACB", "CASA"], "structural_signals": ["AGM_QA"]},
                    valid_from=_utc(2025, 10, 26),
                    valid_to=_utc(9999, 12, 31),
                ),
                KnowledgeUnit(
                    id="ku-fpt-26",
                    document_version_id="ver-fpt-26",
                    tenant_id="aiinvest-default",
                    project_id="FPT",
                    source_id="src-1",
                    security_partition_id="all",
                    ordinal=1,
                    text="SCIC chuyển giao 50,16% vốn FTEL sang Bộ Công an năm 2026.",
                    content_hash="ch-fpt",
                    input_checksum="ic-fpt",
                    checksum="c-fpt",
                    is_current=True,
                    provenance_json={
                        "document_id": "doc-FPT",
                        "evidence_block_ids": ["blk-fpt-26"],
                        "page_from": 176,
                        "page_to": 176,
                        "section_path": "BCTC_AUDITED > CẤU TRÚC TẬP ĐOÀN",
                        "sub_doc_slot": "BCTC_AUDITED",
                    },
                    features_json={"entities": ["FTEL", "Bộ Công an"], "structural_signals": ["CONSOLIDATION_SCOPE"]},
                    valid_from=_utc(2026, 10, 24),
                    valid_to=_utc(9999, 12, 31),
                ),
            ]
        )
        await s.commit()

    reader = _PitFakeCoreReader()
    profiles = tuple(await reader.fetch_issuer_profiles())

    def _make_ctx(as_of: date, avail: frozenset[str], core=reader) -> ResearchToolContext:
        return ResearchToolContext(
            as_of_date=as_of,
            principal=_principal(),
            chat_mode="GENERAL",
            session_factory=sf,
            session_ledger=FinancialZeroMemLedger(request_as_of_date=as_of),
            evidence=ResearchEvidenceLedger(),
            available_tickers=avail,
            catalog_profiles=profiles,
            core_reader=core,
        )

    # 1) Historical PIT replay at 2025-11-15 -> ZERO 2026 leakage (`1388.2` or `Bộ Công an` MUST NOT appear!)
    ctx_2025 = _make_ctx(date(2025, 11, 15), frozenset({"ACB", "MBB", "TCB"}))
    mkt_2025 = await build_scope_gated_briefing(
        ctx_2025,
        query="Thị trường hiện tại đang như thế nào?",
        explicit_tickers=[],
        fallback_tickers=[],
    )
    assert mkt_2025.lens == "MARKET"
    assert "1.245,5" in mkt_2025.briefing_text or "1,245.5" in mkt_2025.briefing_text
    assert "1388.2" not in mkt_2025.briefing_text and "1,388.2" not in mkt_2025.briefing_text
    assert "Bộ Công an" not in mkt_2025.briefing_text
    from sag_api.research.quote_verifier import verify_numeric_claims_against_evidence

    await register_pulse_evidence_into_ledger(mkt_2025, ctx_2025.evidence)
    num_chk = verify_numeric_claims_against_evidence(
        "VN-Index tại 1.245,5 điểm và lãi suất liên ngân hàng ở mức 4,65% [CALC-1].",
        sections_by_cid={},
        calculator_traces=ctx_2025.evidence.calculator_traces,
    )
    assert num_chk.unsupported_claims == []

    # 2) Sector query at 2026-11-28 -> Full Core PG count (4 banks) vs Indexed Bellwether count (3)
    ctx_2026 = _make_ctx(date(2026, 11, 28), frozenset({"ACB", "MBB", "TCB", "FPT"}))
    sec_2026 = await build_scope_gated_briefing(
        ctx_2026,
        query="Ngành ngân hàng hiện tại thế nào?",
        explicit_tickers=[],
        fallback_tickers=[],
    )
    assert sec_2026.lens == "SECTOR"
    assert sec_2026.full_universe_count == 4  # ACB, MBB, TCB, STB in Core PG
    assert set(sec_2026.bellwether_tickers) == {"ACB", "MBB", "TCB"}  # STB has no PDF in SAG!
    assert any(p.unit_id == "ku-acb-25" for p in sec_2026.structural_pointers)
    # Registering pulse evidence populates `[CALC-*]` traces so Stage 7 audits 100% of numbers
    lines, badges = await register_pulse_evidence_into_ledger(sec_2026, ctx_2026.evidence)
    assert lines and any(b.startswith("[CALC-") for b in badges)

    # 3) Company query on FPT -> Scope-gated strictly to FPT (NO VNINDEX or unrelated sector chatter!)
    co_2026 = await build_scope_gated_briefing(
        ctx_2026,
        query="Tại sao cơ cấu lợi nhuận FPT thay đổi trong năm 2026?",
        explicit_tickers=[],
        fallback_tickers=[],
    )
    assert co_2026.lens == "COMPANY"
    assert "VNINDEX" not in co_2026.briefing_text
    assert any(p.unit_id == "ku-fpt-26" for p in co_2026.structural_pointers)

    # 4) Offline degradation (`core_reader=None`) -> never crashes
    ctx_offline = _make_ctx(date(2026, 11, 28), frozenset({"FPT"}), core=None)
    off_bundle = await build_scope_gated_briefing(
        ctx_offline,
        query="Thị trường hiện tại thế nào?",
        explicit_tickers=[],
        fallback_tickers=[],
    )
    assert off_bundle.lens == "MARKET"
    assert off_bundle.briefing_text

    await eng.dispose()
