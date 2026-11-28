"""Phase 0 + Phase 6 L1-L3 Contract Tests: Quantitative PIT, Scale Calibration, Table Note Ref, & KG Macro."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_api.db.base import Base
from sag_api.db.models import Document, DocumentVersion
from sag_api.db.models.stock_research import SagExtractedTable
from sag_api.research.financial_metrics_store import (
    CoreMetricsReaderProtocol,
    _effective_pit_pub_date,
    _normalize_core_ratio_rows,
    _scale_ratio_value_with_invariants,
    _select_period_columns,
    screen_universe_metrics,
)
from sag_api.research.kg_store import (
    InMemoryKGStore,
    SQLAlchemyKGStore,
    refresh_research_knowledge_graph,
)
from sag_api.research.table_engine import (
    classify_table_columns,
    to_hydrated_table,
)


class _StubPitCoreReader(CoreMetricsReaderProtocol):
    """In-memory Core PG reader modeling historical `market_data_daily.market_cap` vs current `stocks.market_cap`."""

    def __init__(self) -> None:
        self.pit_cap_calls: list[date] = []

    async def fetch_issuer_profiles(self, tickers=None) -> list[dict[str, Any]]:
        rows = [
            {
                "ticker": "FPT",
                "company_name": "CTCP FPT",
                "sector": "Cong nghe",
                "industry": "Phan mem",
                "exchange": "HOSE",
                "market_cap": 210_000_000_000_000.0,  # 2026 mutable cap (MUST NOT leak to 2023!)
            },
            {
                "ticker": "VCB",
                "company_name": "Ngan hang Vietcombank",
                "sector": "Ngan hang",
                "industry": "Ngan hang",
                "exchange": "HOSE",
                "market_cap": 520_000_000_000_000.0,
            },
        ]
        if tickers:
            tset = {t.upper() for t in tickers}
            rows = [r for r in rows if r["ticker"] in tset]
        return rows

    async def fetch_pit_market_caps(
        self,
        as_of_date: date,
        *,
        tickers=None,
    ) -> dict[str, float]:
        self.pit_cap_calls.append(as_of_date)
        if as_of_date <= date(2023, 12, 31):
            caps = {"FPT": 95_000_000_000_000.0, "VCB": 410_000_000_000_000.0}
        else:
            caps = {"FPT": 190_000_000_000_000.0, "VCB": 500_000_000_000_000.0}
        if tickers:
            tset = {t.upper() for t in tickers}
            return {k: v for k, v in caps.items() if k in tset}
        return caps

    async def fetch_pit_financial_ratios(
        self,
        as_of_date: date,
        *,
        tickers=None,
        lookback_years: int = 7,
    ) -> list[dict[str, Any]]:
        rows = [
            # Valid 2022 annual ratio (published 2023-03-20 -> 79-day realistic lag)
            {
                "symbol": "FPT",
                "ratio_date": date(2022, 12, 31),
                "frequency": "yearly",
                "effective_published_date": date(2023, 3, 20),
                "pe": 16.5,
                "pb": 3.8,
                "roe": 25.4,  # CafeF percent scale
                "roa": 11.2,
                "debt_equity": 0.55,
                "gross_margin": 0.39,  # Mixed decimal scale
                "net_margin": 12.1,
            },
            # Backfilled 0-day lag row for 2023-12-31 -> MUST be rejected at as_of_date=2024-01-10!
            {
                "symbol": "FPT",
                "ratio_date": date(2023, 12, 31),
                "frequency": "yearly",
                "effective_published_date": date(2023, 12, 31),
                "pe": 28.0,
                "pb": 6.2,
                "roe": 31.0,
                "roa": 14.0,
                "debt_equity": 0.42,
                "gross_margin": 42.0,
                "net_margin": 14.5,
            },
            # Bank ROA = 1.2%
            {
                "symbol": "VCB",
                "ratio_date": date(2022, 12, 31),
                "frequency": "yearly",
                "effective_published_date": date(2023, 3, 25),
                "pe": 14.2,
                "pb": 2.9,
                "roe": 22.8,
                "roa": 1.2,
                "debt_equity": 10.5,
                "gross_margin": None,
                "net_margin": 45.0,
            },
        ]
        if tickers:
            tset = {t.upper() for t in tickers}
            rows = [r for r in rows if r["symbol"] in tset]
        return rows

    async def fetch_pit_financial_statements(
        self,
        as_of_date: date,
        *,
        tickers=None,
        lookback_years: int = 5,
        prefer_annual: bool = True,
    ) -> list[dict[str, Any]]:
        rows = [
            {
                "symbol": "FPT",
                "statement_type": "IS",
                "frequency": "yearly",
                "period_end": date(2022, 12, 31),
                "effective_published_date": date(2023, 3, 20),
                "data": {
                    "doanh thu thuần": 44_010_000_000_000.0,
                    "lợi nhuận gộp": 17_164_000_000_000.0,
                    "lợi nhuận sau thuế": 5_310_000_000_000.0,
                },
            },
            {
                "symbol": "FPT",
                "statement_type": "BS",
                "frequency": "yearly",
                "period_end": date(2022, 12, 31),
                "effective_published_date": date(2023, 3, 20),
                "data": {
                    "tổng tài sản": 51_650_000_000_000.0,
                    "nợ phải trả": 26_300_000_000_000.0,
                    "vốn chủ sở hữu": 25_350_000_000_000.0,
                    "vay và nợ thuê tài chính ngắn hạn": 12_400_000_000_000.0,
                    "tiền và tương đương tiền": 19_000_000_000_000.0,
                },
            },
            {
                "symbol": "FPT",
                "statement_type": "IS",
                "frequency": "quarterly",
                "period_end": date(2023, 9, 30),
                "effective_published_date": date(2023, 10, 28),
                "data": {
                    "doanh thu thuần": 13_760_000_000_000.0,
                    "lợi nhuận gộp": 5_400_000_000_000.0,
                    "lợi nhuận sau thuế": 1_740_000_000_000.0,
                },
            },
        ]
        if tickers:
            tset = {t.upper() for t in tickers}
            rows = [r for r in rows if r["symbol"] in tset]
        return rows


def test_fm2_effective_pit_pub_date_rejects_zero_day_backfill() -> None:
    # Backfilled published_date == period_date (0-day lag) MUST fall back to conservative 90d annual lag
    pub_annual = _effective_pit_pub_date(date(2025, 12, 31), date(2025, 12, 31), default_lag_days=90)
    assert pub_annual >= date(2026, 3, 30)

    # Realistic 75-day filing lag MUST be accepted verbatim
    pub_real = _effective_pit_pub_date(date(2025, 12, 31), date(2026, 3, 16), default_lag_days=90)
    assert pub_real == date(2026, 3, 16)

    # Quarterly 0-day backfill -> 45-day conservative lag
    pub_q = _effective_pit_pub_date(date(2025, 9, 30), date(2025, 9, 30), default_lag_days=45)
    assert pub_q >= date(2025, 11, 14)


def test_fm3_ratio_scale_calibration_handles_mixed_and_bank_roa() -> None:
    # Bank ROA = 1.2 (percent) -> calibrated to 0.012 without corrupting decimal 0.012
    assert _scale_ratio_value_with_invariants(
        1.2, col="roa", row_is_percent=True, peer_upper_bound=0.228
    ) == pytest.approx(0.012, abs=1e-6)
    assert _scale_ratio_value_with_invariants(
        0.012, col="roa", row_is_percent=False, peer_upper_bound=0.228
    ) == pytest.approx(0.012, abs=1e-6)

    rows = [
        {
            "symbol": "FPT",
            "ratio_date": date(2023, 12, 31),
            "frequency": "yearly",
            "roe": 25.0,
            "gross_margin": 0.39,
            "net_margin": 12.1,
        },
        {
            "symbol": "VCB",
            "ratio_date": date(2023, 12, 31),
            "frequency": "yearly",
            "roe": 22.8,
            "roa": 1.2,
            "net_margin": 45.0,
        },
    ]
    stmt_ref = {("FPT", 2023): {"net_margin": 0.1206, "gross_margin": 0.3900}}
    out = _normalize_core_ratio_rows(rows, stmt_metrics=stmt_ref)
    assert out[0]["roe"] == pytest.approx(0.25, abs=1e-4)
    assert out[0]["gross_margin"] == pytest.approx(0.39, abs=1e-4)
    assert out[0]["net_margin"] == pytest.approx(0.121, abs=1e-4)
    assert out[1]["roa"] == pytest.approx(0.012, abs=1e-4)


def test_fm5_select_period_columns_sorts_chronologically_descending() -> None:
    # Scrambled OCR column order: 2023 | 2025 | 2024 -> values indices 0, 1, 2
    headers = ["Chi tieu", "2023", "2025", "2024"]
    rows = [{"label": "Doanh thu", "values": [100.0, 150.0, 120.0]}]
    period_cols = _select_period_columns(headers, rows, None)
    assert period_cols[:2] == [1, 2]  # index 1 (2025) newest, index 2 (2024) previous


def test_table_engine_to_hydrated_table_preserves_note_ref_and_code_text() -> None:
    headers = [["Chi tieu", "Ma so", "Thuyet minh", "31/12/2025", "31/12/2024"]]
    rows = [
        [
            {"raw_text": "Phai thu ngan han cua khach hang", "numeric_value": None},
            {"raw_text": "131", "numeric_value": 131.0},
            {"raw_text": "V.03", "numeric_value": None},
            {"raw_text": "12,500", "numeric_value": 12500.0},
            {"raw_text": "10,200", "numeric_value": 10200.0},
        ],
        [
            {"raw_text": "Vay va no thue tai chinh ngan han", "numeric_value": None},
            {"raw_text": "320", "numeric_value": 320.0},
            {"raw_text": "V.20", "numeric_value": None},
            {"raw_text": "14,800", "numeric_value": 14800.0},
            {"raw_text": "11,100", "numeric_value": 11100.0},
        ],
    ]
    roles, amt_cols = classify_table_columns(headers, rows)
    assert roles[1] == "code"
    assert roles[2] == "note_ref"
    # `amt_cols` are 0-based indices into `row["values"]` (excluding col 0 label)
    assert amt_cols == [2, 3]

    hydrated = to_hydrated_table(
        {
            "table_id": "tbl_bs_notes",
            "title": "Bang can doi ke toan hop nhat",
            "sub_doc_slot": "S03_BS_CONSOL",
            "page_from": 5,
            "page_to": 5,
            "headers": headers,
            "rows": rows,
            "verification_status": "VERIFIED",
        }
    )
    assert hydrated["rows"][0]["values"][0] == "131"
    assert hydrated["rows"][0]["values"][1] == "V.03"
    assert hydrated["rows"][1]["values"][1] == "V.20"
    assert hydrated["rows"][0]["values"][2] == pytest.approx(12500.0)


@pytest.mark.asyncio
async def test_fm1_fm2_fm4_screen_universe_pit_cap_leverage_split_and_revoked_guard() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    reader = _StubPitCoreReader()

    async with session_factory() as session:
        session.add_all(
            [
                Document(
                    id="doc-fpt",
                    tenant_id="t1",
                    project_id="FPT",
                    filename="FPT_BCTC_2022.pdf",
                    storage_path="/tmp/fpt_2022.pdf",
                ),
                DocumentVersion(
                    id="ver-fpt-2022",
                    document_id="doc-fpt",
                    version_no=1,
                    file_hash="hash-fpt-2022",
                    status="READY",
                    valid_from=datetime(2023, 3, 20, tzinfo=UTC),
                    source_published_at=datetime(2023, 3, 20, tzinfo=UTC),
                ),
                # REVOKED VCB doc must NOT qualify VCB into sag_ingested scope!
                Document(
                    id="doc-vcb",
                    tenant_id="t1",
                    project_id="VCB",
                    filename="VCB_BCTC_2022.pdf",
                    storage_path="/tmp/vcb_2022.pdf",
                ),
                DocumentVersion(
                    id="ver-vcb-revoked",
                    document_id="doc-vcb",
                    version_no=1,
                    file_hash="hash-vcb-2022",
                    status="REVOKED",
                    valid_from=datetime(2023, 3, 25, tzinfo=UTC),
                    source_published_at=datetime(2023, 3, 25, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

        # Backtest date 2023-06-30: historical market_cap from fetch_pit_market_caps (NOT 2026 stocks.market_cap),
        # debt_to_equity vs liabilities_to_equity strictly separated, REVOKED VCB excluded.
        rows, total, _desc = await screen_universe_metrics(
            session,
            tenant_id="t1",
            as_of_date=date(2023, 6, 30),
            allowed_project_ids=frozenset({"*"}),
            core_reader=reader,
            scope="sag_ingested",
        )
        assert total == 1
        assert [r["ticker"] for r in rows] == ["FPT"]
        fpt = rows[0]
        assert fpt["fiscal_year"] == 2022
        assert fpt["market_cap"] == pytest.approx(95_000_000_000_000.0)
        assert fpt["debt_to_equity"] == pytest.approx(0.55, abs=0.07)
        assert fpt["liabilities_to_equity"] == pytest.approx(26_300 / 25_350, abs=0.03)
        assert fpt["debt_to_equity"] != pytest.approx(fpt["liabilities_to_equity"], abs=0.1)

        # Backtest 2024-01-10: 0-day backfill 2023-12-31 ratio MUST be rejected -> stays FY2022!
        rows_jan, _, _ = await screen_universe_metrics(
            session,
            tenant_id="t1",
            as_of_date=date(2024, 1, 10),
            allowed_project_ids=frozenset({"*"}),
            core_reader=reader,
            scope="sag_ingested",
            prefer_annual=True,
        )
        assert rows_jan[0]["fiscal_year"] == 2022

        # Quarterly prefer_annual=False picks Q3 2023 at as_of_date=2023-11-15
        rows_q, _, _ = await screen_universe_metrics(
            session,
            tenant_id="t1",
            as_of_date=date(2023, 11, 15),
            allowed_project_ids=frozenset({"*"}),
            core_reader=reader,
            scope="sag_ingested",
            prefer_annual=False,
        )
        assert rows_q[0]["fiscal_year"] == 2023
        assert rows_q[0]["net_revenue"] == pytest.approx(13_760_000_000_000.0)

        # PDF USD table persists cleanly without mixing with VND market_cap
        session.add(
            SagExtractedTable(
                table_id="tbl-fpt-usd",
                project_id="FPT",
                document_version_id="ver-fpt-2022",
                sub_doc_slot="S04_IS_CONSOL",
                fiscal_year=2022,
                table_title="Income Statement (USD)",
                currency="USD",
                unit_raw="triệu USD",
                unit_multiplier=1_000_000.0,
                headers_json=[["Metric", "2022"]],
                rows_json=[{"label": "Lợi nhuận sau thuế", "values": [220.0]}],
                verification_status="VERIFIED",
                table_hash="hash-usd",
                valid_from=datetime(2023, 3, 20, tzinfo=UTC),
                created_at=datetime(2023, 3, 20, tzinfo=UTC),
            )
        )
        await session.commit()

    await engine.dispose()


@pytest.mark.asyncio
async def test_kg_macro_edges_wired_and_get_tickers_by_macro_factor_supports_doc_only_and_pit() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

    async with session_factory() as session:
        store = SQLAlchemyKGStore(session)
        session.add_all(
            [
                Document(
                    id="doc-pvt",
                    tenant_id="t1",
                    project_id="PVT",
                    filename="PVT_BCTN.pdf",
                    storage_path="/tmp/pvt_bctn.pdf",
                ),
                DocumentVersion(
                    id="ver-pvt-ok",
                    document_id="doc-pvt",
                    version_no=1,
                    file_hash="hash-pvt-ok",
                    status="READY",
                    valid_from=datetime(2025, 4, 1, tzinfo=UTC),
                    source_published_at=datetime(2025, 4, 1, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

        mem_kg = InMemoryKGStore()
        await mem_kg.upsert_nodes(
            [
                {"node_id": "MACRO:USD_VND", "node_type": "MACRO_FACTOR", "ticker": None, "label": "Tỷ giá USD/VND"},
                {"node_id": "TICKER:PVT", "node_type": "COMPANY", "ticker": "PVT", "label": "PVT"},
                {"node_id": "TICKER:HPG", "node_type": "COMPANY", "ticker": "HPG", "label": "HPG"},
            ]
        )
        await mem_kg.upsert_macro_snapshots(
            [
                {
                    "snapshot_id": "snap-pvt",
                    "macro_factor": "usd_vnd",
                    "target_id": "TICKER:PVT",
                    "beta": 0.42,
                    "p_value": 0.01,
                    "fdr_q_value": 0.03,
                    "tercile_stable": True,
                    "audited_note_ref": {"stated_sign": 1},
                    "verification_tier": "CONFIRMED_BOTH",
                    "effective_from": date(2025, 4, 1),
                },
                {
                    "snapshot_id": "snap-hpg-doc-only",
                    "macro_factor": "usd_vnd",
                    "target_id": "TICKER:HPG",
                    "beta": 0.0,
                    "p_value": None,
                    "audited_note_ref": {"stated_sign": -1},
                    "verification_tier": "DOC_ONLY",
                    "effective_from": date(2025, 4, 1),
                },
                # Future snapshot after as_of_date -> MUST be filtered out by PIT!
                {
                    "snapshot_id": "snap-future",
                    "macro_factor": "usd_vnd",
                    "target_id": "TICKER:FPT",
                    "beta": 0.85,
                    "p_value": 0.001,
                    "verification_tier": "CONFIRMED_BOTH",
                    "effective_from": date(2026, 6, 1),
                },
            ]
        )
        all_rows = await mem_kg.get_tickers_by_macro_factor(
            "usd_vnd",
            as_of_date=date(2025, 12, 31),
            allowed_tickers=frozenset({"PVT", "HPG", "FPT"}),
        )
        assert [r["ticker"] for r in all_rows] == ["PVT", "HPG"]
        assert all_rows[0]["verification_tier"] == "CONFIRMED_BOTH"
        assert all_rows[1]["verification_tier"] == "DOC_ONLY"
        assert all_rows[1]["direction_sign"] == -1

        neg_only = await mem_kg.get_tickers_by_macro_factor(
            "usd_vnd",
            as_of_date=date(2025, 12, 31),
            direction_filter=-1,
            allowed_tickers=frozenset({"PVT", "HPG"}),
        )
        assert [r["ticker"] for r in neg_only] == ["HPG"]

        summary = await refresh_research_knowledge_graph(
            session,
            tickers=["PVT"],
            tenant_id="t1",
            as_of_date=date(2025, 12, 31),
        )
        assert "edges_upserted" in summary
        assert isinstance(store, SQLAlchemyKGStore)

    await engine.dispose()
