"""Rule 08 Layer 1, 2 & 3 Contract Tests for Zero-Duplication Core PG + PDF Fallback Store.

Covers:
- Layer 1: Pure deterministic VAS/TT200 extraction, scale-safe Core ratio normalization,
  Core JSONB statement parsing, currency/unverified/Plan-column guards, Calculator ratios.
- Layer 2: Real `AsyncPGCoreMetricsReader` read-only pool settings & DSN secret hygiene,
  100%-universe PIT screening across Core PG valuation/fundamentals + smart fresher
  `VERIFIED` PDF overlay + Core PG outage fallback + strict tenant/ACL isolation.
- Layer 3: Full ASGI HTTP `/api/v1/research-chat/tickers` vertical slice with Continuum
  BFF headers (Core PG catalog, graceful fallback, fail-closed auth).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import patch

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from sag_api.api.v1 import research_chat as rc_api
from sag_api.core.config import settings as global_settings
from sag_api.core.db import get_session
from sag_api.core.errors import ApiError
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db.base import Base
from sag_api.db.models import Document, DocumentVersion
from sag_api.db.models.stock_research import KgNode, SagExtractedTable
from sag_api.research.financial_metrics_store import (
    AsyncPGCoreMetricsReader,
    _extract_core_statement_metrics,
    _normalize_core_ratio_rows,
    extract_metrics_from_verified_tables,
    load_org_catalog,
    screen_universe_metrics,
)


def _make_principal(
    *,
    org_id: str = "aiinvest-default",
    allowed: frozenset[str] = frozenset({"*"}),
) -> VerifiedPrincipal:
    now_ts = int(datetime.now(UTC).timestamp())
    return VerifiedPrincipal(
        subject="analyst-1",
        organization_id=org_id,
        allowed_project_ids=allowed,
        issuer="continuum-be-bff",
        key_id="k1",
        token_id="tok-metrics-1",
        issued_at=now_ts,
        expires_at=now_ts + 3600,
        tenant_id=org_id,
        allowed_partition_ids=allowed,
    )


def _cell(raw: str, num: float | None = None) -> dict[str, Any]:
    return {"raw_text": raw, "numeric_value": num}


# ── Layer 1: Pure Algorithms & Invariants ───────────────────────────────────


def test_l1_extract_metrics_from_verified_tables_and_calculator_ratios() -> None:
    """Extracts scaled VND figures, skips Plan/Unverified/USD mixing, derives CAGR & margins."""
    tables: list[dict[str, Any]] = [
        # FY2025 B02 KQKD (Plan column MUST be skipped in YoY!)
        {
            "table_id": "tbl-fpt-is-2025",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-2025",
            "sub_doc_slot": "BCTC_AUDITED",
            "fiscal_year": 2025,
            "fiscal_quarter": 0,
            "table_title": "Báo cáo kết quả hoạt động kinh doanh năm 2025",
            "section_path": "BCTC_AUDITED > KQKD",
            "page_from": 6,
            "currency": "VND",
            "unit_multiplier": 1e9,
            "verification_status": "VERIFIED",
            "valid_from": datetime(2026, 3, 25, tzinfo=UTC),
            "headers_json": [["Chỉ tiêu", "Kế hoạch 2025", "Năm 2025", "Năm 2024"]],
            "rows_json": [
                [
                    _cell("Doanh thu thuần"),
                    _cell("75.000", 75000.0),
                    _cell("62.849", 62849.0),
                    _cell("52.618", 52618.0),
                ],
                [
                    _cell("Lợi nhuận gộp"),
                    _cell("28.000", 28000.0),
                    _cell("24.000", 24000.0),
                    _cell("20.000", 20000.0),
                ],
                [
                    _cell("Lợi nhuận sau thuế thu nhập doanh nghiệp"),
                    _cell("11.000", 11000.0),
                    _cell("9.420", 9420.0),
                    _cell("7.788", 7788.0),
                ],
            ],
        },
        # FY2025 B01 Balance Sheet + B03 Cash Flow
        {
            "table_id": "tbl-fpt-bs-2025",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-2025",
            "sub_doc_slot": "BCTC_AUDITED",
            "fiscal_year": 2025,
            "fiscal_quarter": 0,
            "table_title": "Bảng cân đối kế toán tại 31/12/2025",
            "section_path": "BCTC_AUDITED > CĐKT",
            "page_from": 4,
            "currency": "VND",
            "unit_multiplier": 1e9,
            "verification_status": "VERIFIED",
            "valid_from": datetime(2026, 3, 25, tzinfo=UTC),
            "headers_json": [["Chỉ tiêu", "31/12/2025", "31/12/2024"]],
            "rows_json": [
                [_cell("TỔNG CỘNG TÀI SẢN"), _cell("68.000", 68000.0), _cell("60.000", 60000.0)],
                [_cell("A. TÀI SẢN NGẮN HẠN"), _cell("42.000", 42000.0), _cell("38.000", 38000.0)],
                [_cell("Tiền và các khoản tương đương tiền"), _cell("9.000", 9000.0), _cell("7.000", 7000.0)],
                [_cell("Đầu tư tài chính ngắn hạn"), _cell("16.000", 16000.0), _cell("12.000", 12000.0)],
                [_cell("Nợ ngắn hạn"), _cell("21.000", 21000.0), _cell("19.000", 19000.0)],
                [_cell("Vay và nợ thuê tài chính ngắn hạn"), _cell("10.000", 10000.0), _cell("9.000", 9000.0)],
                [_cell("Vay và nợ thuê tài chính dài hạn"), _cell("2.000", 2000.0), _cell("2.000", 2000.0)],
                [_cell("D. VỐN CHỦ SỞ HỮU"), _cell("32.000", 32000.0), _cell("28.000", 28000.0)],
            ],
        },
        {
            "table_id": "tbl-fpt-cf-2025",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-2025",
            "sub_doc_slot": "BCTC_AUDITED",
            "fiscal_year": 2025,
            "fiscal_quarter": 0,
            "table_title": "Lưu chuyển tiền tệ năm 2025",
            "section_path": "BCTC_AUDITED > LCTT",
            "page_from": 8,
            "currency": "VND",
            "unit_multiplier": 1e9,
            "verification_status": "VERIFIED",
            "valid_from": datetime(2026, 3, 25, tzinfo=UTC),
            "headers_json": [["Chỉ tiêu", "Năm 2025"]],
            "rows_json": [
                [_cell("Lưu chuyển tiền thuần từ hoạt động kinh doanh"), _cell("11.304", 11304.0)],
                [_cell("Tiền chi để mua sắm, xây dựng TSCĐ"), _cell("(3.200)", -3200.0)],
            ],
        },
        # FY2022 B02 anchor to verify cross-year 3Y revenue CAGR (2022 -> 2025)
        {
            "table_id": "tbl-fpt-is-2022",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-2022",
            "sub_doc_slot": "BCTC_AUDITED",
            "fiscal_year": 2022,
            "fiscal_quarter": 0,
            "table_title": "Báo cáo KQKD năm 2022",
            "section_path": "BCTC_AUDITED > KQKD",
            "page_from": 5,
            "currency": "VND",
            "unit_multiplier": 1e9,
            "verification_status": "VERIFIED",
            "valid_from": datetime(2023, 3, 25, tzinfo=UTC),
            "headers_json": [["Chỉ tiêu", "Năm 2022"]],
            "rows_json": [[_cell("Doanh thu thuần"), _cell("44.010", 44010.0)]],
        },
        # MUST BE REFUSED: TABLE_UNVERIFIED cannot inject fake metrics
        {
            "table_id": "tbl-fpt-bad",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-bad",
            "fiscal_year": 2026,
            "fiscal_quarter": 0,
            "table_title": "Báo cáo KQKD 2026",
            "currency": "VND",
            "unit_multiplier": 1e9,
            "verification_status": "TABLE_UNVERIFIED",
            "headers_json": [["Chỉ tiêu", "Năm 2026"]],
            "rows_json": [[_cell("Doanh thu thuần"), _cell("999.999", 999999.0)]],
        },
        # MUST STAY ISOLATED: USD table for same FY2025 never mixes into VND snapshot
        {
            "table_id": "tbl-fpt-usd-2025",
            "project_id": "FPT",
            "document_version_id": "ver-fpt-usd",
            "fiscal_year": 2025,
            "fiscal_quarter": 0,
            "table_title": "Income Statement USD 2025",
            "currency": "USD",
            "unit_multiplier": 1e6,
            "verification_status": "VERIFIED",
            "headers_json": [["Item", "FY2025"]],
            "rows_json": [[_cell("Net revenue"), _cell("2,500", 2500.0)]],
        },
    ]

    snaps = extract_metrics_from_verified_tables(tables, tenant_id="aiinvest-default")
    assert all(s["fiscal_year"] != 2026 for s in snaps)

    vnd_2025 = [s for s in snaps if s["fiscal_year"] == 2025 and s["currency"] == "VND"]
    assert len(vnd_2025) == 1
    s25 = vnd_2025[0]
    assert s25["net_revenue"] == pytest.approx(62849e9)
    assert s25["cash_and_st_investments"] == pytest.approx(25000e9)
    assert s25["total_debt"] == pytest.approx(12000e9)
    assert s25["fcf"] == pytest.approx(8104e9)
    assert s25["gross_margin"] == pytest.approx(24000.0 / 62849.0, rel=1e-5)
    assert s25["net_margin"] == pytest.approx(9420.0 / 62849.0, rel=1e-5)
    assert s25["roe"] == pytest.approx(9420.0 / 32000.0, rel=1e-5)
    assert s25["roa"] == pytest.approx(9420.0 / 68000.0, rel=1e-5)
    assert s25["debt_to_equity"] == pytest.approx(12000.0 / 32000.0, rel=1e-5)
    assert s25["net_debt_to_equity"] == pytest.approx((12000.0 - 25000.0) / 32000.0, rel=1e-5)
    assert s25["current_ratio"] == pytest.approx(42000.0 / 21000.0, rel=1e-5)
    assert s25["cash_to_assets"] == pytest.approx(25000.0 / 68000.0, rel=1e-5)
    assert s25["cfo_to_net_income"] == pytest.approx(11304.0 / 9420.0, rel=1e-5)
    # YoY skips "Kế hoạch 2025" (75.000) and compares 62.849 vs 52.618
    assert s25["revenue_yoy"] == pytest.approx((62849.0 - 52618.0) / 52618.0, rel=1e-5)
    assert s25["revenue_cagr_3y"] == pytest.approx((62849.0 / 44010.0) ** (1.0 / 3.0) - 1.0, rel=1e-5)
    assert s25["provenance_json"]["net_revenue"]["table_id"] == "tbl-fpt-is-2025"
    assert s25["provenance_json"]["net_revenue"]["page_from"] == 6


def test_l1_normalize_core_ratio_rows_preserves_multiples_and_scales_percent() -> None:
    """Scales pct columns > 1.5 median to decimals; NEVER divides P/E, P/B, EV/EBITDA, D/E."""
    percent_rows = [
        {
            "symbol": "FPT",
            "ratio_date": date(2025, 12, 31),
            "frequency": "yearly",
            "gross_margin": 38.2,
            "net_margin": 15.0,
            "roe": 27.5,
            "roa": 14.1,
            "yoy_revenue_growth": 19.4,
            "yoy_earnings_growth": 20.9,
            "fcf_yield": 4.2,
            "pe": 18.6,
            "pb": 4.4,
            "ev_ebitda": 11.2,
            "debt_equity": 0.42,
            "current_ratio": 1.65,
            "published_date": date(2026, 3, 20),
        },
        {
            "symbol": "PVT",
            "ratio_date": date(2025, 9, 30),
            "frequency": "quarterly",
            "gross_margin": 21.0,
            "net_margin": 9.8,
            "roe": 16.4,
            "roa": 7.5,
            "yoy_revenue_growth": 12.0,
            "yoy_earnings_growth": 10.5,
            "fcf_yield": 6.1,
            "pe": 8.4,
            "pb": 1.15,
            "ev_ebitda": 5.3,
            "debt_equity": 0.68,
            "current_ratio": 1.9,
            "published_date": None,
        },
    ]
    norm = _normalize_core_ratio_rows(percent_rows)
    by_sym = {r["ticker"]: r for r in norm}

    assert by_sym["FPT"]["gross_margin"] == pytest.approx(0.382)
    assert by_sym["FPT"]["roe"] == pytest.approx(0.275)
    assert by_sym["FPT"]["pe"] == pytest.approx(18.6)
    assert by_sym["FPT"]["pb"] == pytest.approx(4.4)
    assert by_sym["FPT"]["ev_ebitda"] == pytest.approx(11.2)
    assert by_sym["FPT"]["liabilities_to_equity"] == pytest.approx(0.42)
    assert by_sym["FPT"]["debt_to_equity"] is None
    assert by_sym["FPT"]["current_ratio"] == pytest.approx(1.65)
    assert by_sym["FPT"]["fiscal_quarter"] == 0

    assert by_sym["PVT"]["fiscal_quarter"] == 3
    assert by_sym["PVT"]["effective_published_date"] == date(2025, 11, 14)

    # Already-decimal inputs stay unscaled
    dec_norm = _normalize_core_ratio_rows(
        [
            {
                "symbol": "MWG",
                "ratio_date": date(2025, 12, 31),
                "frequency": "yearly",
                "gross_margin": 0.215,
                "roe": 0.175,
                "pe": 14.2,
                "debt_equity": 0.55,
            }
        ]
    )
    assert dec_norm[0]["gross_margin"] == pytest.approx(0.215)
    assert dec_norm[0]["roe"] == pytest.approx(0.175)
    assert dec_norm[0]["pe"] == pytest.approx(14.2)


def test_l1_extract_core_statement_metrics_parses_json_strings_and_pit() -> None:
    """Parses asyncpg raw JSON strings & snake_case VN keys while blocking post-cutoff filings."""
    rows = [
        {
            "symbol": "DCM",
            "period_end": date(2024, 12, 31),
            "statement_type": "IS",
            "frequency": "yearly",
            "published_date": date(2025, 3, 15),
            "data": json.dumps({"doanh_thu_thuần": 13456e9, "Lợi nhuận gộp": 2800e9, "Lợi nhuận sau thuế": 1420e9}),
        },
        {
            "symbol": "DCM",
            "period_end": date(2024, 12, 31),
            "statement_type": "BS",
            "frequency": "yearly",
            "published_date": date(2025, 3, 15),
            "data": {
                "tổng_cộng_tài_sản": 15200e9,
                "vốn_chủ_sở_hữu": 10100e9,
                "Tiền và tương đương tiền": 3100e9,
                "Đầu tư tài chính ngắn hạn": 4200e9,
                "Vay ngắn hạn": 800e9,
                "Vay dài hạn": 200e9,
            },
        },
        {
            "symbol": "DCM",
            "period_end": date(2024, 12, 31),
            "statement_type": "CF",
            "frequency": "yearly",
            "published_date": date(2025, 3, 15),
            "data": json.dumps({"Lưu chuyển tiền thuần từ hoạt động kinh doanh": 2100e9}),
        },
        # Future statement published after cutoff -> MUST be excluded by PIT check
        {
            "symbol": "DCM",
            "period_end": date(2025, 12, 31),
            "statement_type": "IS",
            "frequency": "yearly",
            "published_date": date(2026, 3, 20),
            "data": {"doanh_thu_thuần": 99999e9},
        },
    ]

    extracted = _extract_core_statement_metrics(rows, as_of_date=date(2025, 12, 31))
    assert ("DCM", 2025) not in extracted
    assert ("DCM", 2024) in extracted
    rec = extracted[("DCM", 2024)]
    assert rec["net_revenue"] == pytest.approx(13456e9)
    assert rec["cash_and_st_investments"] == pytest.approx(7300e9)
    assert rec["cash_to_assets"] == pytest.approx(7300.0 / 15200.0, rel=1e-5)
    assert rec["cfo_to_net_income"] == pytest.approx(2100.0 / 1420.0, rel=1e-5)


# ── Layer 2: Unmocked Seam Contracts & 100%-Universe PIT Screening ──────────


class _FakeAsyncPGConn:
    def __init__(self, log_calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._calls = log_calls

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self._calls.append((sql, args))
        if "FROM stocks" in sql:
            return [
                {
                    "ticker": "FPT",
                    "company_name": "Công ty Cổ phần FPT",
                    "sector": "Công nghệ",
                    "industry": "Phần mềm & CNTT",
                    "exchange": "HOSE",
                    "market_cap": 195_000_000_000_000,
                    "trading_status": "NORMAL",
                    "audit_opinion": "UNQUALIFIED",
                    "audit_year": 2025,
                }
            ]
        return []


class _FakeAcquireCtx:
    def __init__(self, conn: _FakeAsyncPGConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeAsyncPGConn:
        return self._conn

    async def __aexit__(self, *_args: Any) -> None:
        return None


class _FakeAsyncPGPool:
    def __init__(self, log_calls: list[tuple[str, tuple[Any, ...]]]) -> None:
        self._conn = _FakeAsyncPGConn(log_calls)
        self.closed = False

    def acquire(self) -> _FakeAcquireCtx:
        return _FakeAcquireCtx(self._conn)

    async def close(self) -> None:
        self.closed = True


@pytest.mark.asyncio
async def test_l2_asyncpg_core_metrics_reader_readonly_and_secret_hygiene(caplog: pytest.LogCaptureFixture) -> None:
    """Real `AsyncPGCoreMetricsReader` enforces read-only server settings & never logs credentials."""
    captured_pool_kwargs: list[dict[str, Any]] = []
    sql_calls: list[tuple[str, tuple[Any, ...]]] = []

    async def _fake_pool_factory(dsn: str, **kwargs: Any) -> _FakeAsyncPGPool:
        captured_pool_kwargs.append({"dsn": dsn, **kwargs})
        return _FakeAsyncPGPool(sql_calls)

    reader = AsyncPGCoreMetricsReader(
        "postgresql+asyncpg://postgres:SUPER_SECRET_PASS_999@postgres:5432/aiinvest",
        pool_factory=_fake_pool_factory,
    )
    profs = await reader.fetch_issuer_profiles(["fpt"])
    await reader.fetch_pit_financial_ratios(date(2025, 6, 30), tickers=["FPT"])
    await reader.fetch_pit_financial_statements(date(2025, 6, 30), tickers=["FPT"])
    await reader.close()

    assert profs[0]["ticker"] == "FPT"
    assert len(captured_pool_kwargs) == 1
    assert captured_pool_kwargs[0]["dsn"].startswith("postgresql://")
    assert captured_pool_kwargs[0]["server_settings"] == {"default_transaction_read_only": "on"}
    assert len(sql_calls) == 3
    assert "LEFT JOIN instrument_master" in sql_calls[0][0]
    assert sql_calls[1][1][0] == date(2025, 6, 30)
    assert sql_calls[2][1][0] == date(2025, 6, 30)

    # Credential hygiene when connection fails inside load_org_catalog
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _failing_pool_factory(_dsn: str, **_kwargs: Any) -> Any:
        raise OSError("fatal connect postgresql://postgres:SUPER_SECRET_PASS_999@postgres:5432/aiinvest")

    bad_reader = AsyncPGCoreMetricsReader(
        "postgresql://postgres:SUPER_SECRET_PASS_999@postgres:5432/aiinvest",
        pool_factory=_failing_pool_factory,
    )
    caplog.set_level(logging.WARNING, logger="sag_api.financial_metrics_store")
    async with session_factory() as session:
        session.add(
            Document(
                id="doc-1",
                tenant_id="aiinvest-default",
                project_id="FPT",
                filename="fpt.pdf",
                storage_path="/tmp/fpt.pdf",
                is_active=True,
            )
        )
        session.add(
            DocumentVersion(
                id="ver-doc-1",
                document_id="doc-1",
                version_no=1,
                file_hash="h-doc-1",
                valid_from=datetime(2025, 3, 25, tzinfo=UTC),
            )
        )
        await session.commit()
        catalog = await load_org_catalog(
            session,
            principal=_make_principal(),
            as_of_date=date(2025, 6, 30),
            core_reader=bad_reader,
        )

    assert catalog["catalog_source"] == "SAG_LOCAL_FALLBACK"
    assert catalog["core_db_warning"] == "OSError"
    assert "SUPER_SECRET_PASS_999" not in caplog.text
    assert "postgres:5432" not in caplog.text
    await engine.dispose()


class _FakeCoreMetricsReader:
    """In-memory Core PG double simulating `stocks`, `financial_ratios`, `financial_statements`."""

    def __init__(
        self,
        *,
        profiles: list[dict[str, Any]],
        ratios: list[dict[str, Any]],
        statements: list[dict[str, Any]],
        pit_market_caps: dict[str, float] | None = None,
    ) -> None:
        self._profiles = profiles
        self._ratios = ratios
        self._statements = statements
        self._pit_market_caps = dict(pit_market_caps or {})
        self.profile_calls: list[list[str] | None] = []
        self.statement_calls: list[tuple[date, list[str] | None]] = []

    async def fetch_pit_market_caps(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
    ) -> dict[str, float]:
        del as_of_date
        wanted = {t.strip().upper() for t in tickers} if tickers is not None else None
        out: dict[str, float] = {}
        for p in self._profiles:
            sym = str(p["ticker"]).upper()
            if wanted is not None and sym not in wanted:
                continue
            if sym in self._pit_market_caps:
                out[sym] = self._pit_market_caps[sym]
            elif p.get("market_cap") is not None:
                out[sym] = float(p["market_cap"])
        return out

    async def fetch_issuer_profiles(self, tickers: Sequence[str] | None = None) -> list[dict[str, Any]]:
        norm = [t.strip().upper() for t in tickers] if tickers is not None else None
        self.profile_calls.append(norm)
        if norm is None:
            return list(self._profiles)
        wanted = set(norm)
        return [p for p in self._profiles if str(p["ticker"]).upper() in wanted]

    async def fetch_pit_financial_ratios(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 7,
    ) -> list[dict[str, Any]]:
        del lookback_years
        wanted = {t.strip().upper() for t in tickers} if tickers is not None else None
        out = []
        for r in self._ratios:
            sym = str(r["symbol"]).upper()
            if wanted is not None and sym not in wanted:
                continue
            r_date = r["ratio_date"]
            pub_d = r.get("published_date") or date.fromordinal(r_date.toordinal() + 90)
            if r_date <= as_of_date and pub_d <= as_of_date:
                out.append(r)
        return out

    async def fetch_pit_financial_statements(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 5,
        prefer_annual: bool = True,
    ) -> list[dict[str, Any]]:
        del lookback_years, prefer_annual
        norm = [t.strip().upper() for t in tickers] if tickers is not None else None
        self.statement_calls.append((as_of_date, norm))
        wanted = set(norm) if norm is not None else None
        out = []
        for s in self._statements:
            sym = str(s["symbol"]).upper()
            if wanted is not None and sym not in wanted:
                continue
            pe = s["period_end"]
            pub_d = s.get("published_date") or date.fromordinal(pe.toordinal() + 90)
            if pe <= as_of_date and pub_d <= as_of_date:
                out.append(s)
        return out


@pytest.mark.asyncio
async def test_l2_screen_universe_10_tickers_valuation_pit_hybrid_and_acl() -> None:
    """Screens >6 tickers via Core PG + smarter VERIFIED PDF overlay, zero PIT/tenant leak."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    org_a_tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "PDFONLY"]
    core_tickers = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III"]

    core_profiles = []
    core_ratios = []
    core_statements = []
    for idx, tk in enumerate(core_tickers):
        is_winner = tk == "III"
        sector = "Vận tải - Cảng biển" if tk in {"III", "BBB", "DDD"} else "Bán lẻ - Tiêu dùng"
        core_profiles.append(
            {
                "ticker": tk,
                "company_name": f"Công ty Cổ phần {tk}",
                "sector": sector,
                "industry": "Vận tải biển" if sector.startswith("Vận tải") else "Bán lẻ",
                "exchange": "HOSE",
                "market_cap": 12_000_000_000_000 if is_winner else (3_000_000_000_000 + idx * 5e11),
            }
        )
        # Ticker #9 ("III") has strictly best fundamentals + cheap valuation (pe=8.2, roe=29.0%)
        roe_val = 29.0 if is_winner else (10.0 + idx)
        gm_val = 42.0 if is_winner else (15.0 + idx)
        de_val = 0.25 if is_winner else 1.4
        pe_val = 8.2 if is_winner else (14.0 + idx)
        core_ratios.append(
            {
                "symbol": tk,
                "ratio_date": date(2024, 12, 31),
                "frequency": "yearly",
                "published_date": date(2025, 3, 25),
                "gross_margin": gm_val,
                "net_margin": 22.0 if is_winner else 7.0,
                "roe": roe_val,
                "roa": 18.0 if is_winner else 5.0,
                "debt_equity": de_val,
                "current_ratio": 2.4 if is_winner else 1.1,
                "pe": pe_val if tk != "AAA" else None,  # AAA gets latest P/E from quarterly row!
                "pb": 1.6,
                "ev_ebitda": 5.9,
                "yoy_revenue_growth": 26.0 if is_winner else 4.0,
                "fcf_yield": 8.0,
            }
        )
        if tk == "AAA":
            # Latest PIT quarterly row has current P/E=10.5
            core_ratios.append(
                {
                    "symbol": "AAA",
                    "ratio_date": date(2025, 3, 31),
                    "frequency": "quarterly",
                    "published_date": date(2025, 4, 28),
                    "gross_margin": 19.0,
                    "roe": 12.0,
                    "pe": 10.5,
                    "pb": 1.4,
                }
            )
            # Future row MUST NOT LEAK at as_of_date=2026-04-01
            core_ratios.append(
                {
                    "symbol": "AAA",
                    "ratio_date": date(2026, 6, 30),
                    "frequency": "quarterly",
                    "published_date": date(2026, 7, 25),
                    "gross_margin": 95.0,
                    "roe": 95.0,
                    "pe": 1.0,
                }
            )
        core_statements.append(
            {
                "symbol": tk,
                "period_end": date(2024, 12, 31),
                "statement_type": "IS",
                "frequency": "yearly",
                "published_date": date(2025, 3, 25),
                "data": {
                    "doanh_thu_thuần": 5000e9 if is_winner else 2000e9,
                    "lợi_nhuận_sau_thuế": 1100e9 if is_winner else 140e9,
                },
            }
        )
        core_statements.append(
            {
                "symbol": tk,
                "period_end": date(2024, 12, 31),
                "statement_type": "CF",
                "frequency": "yearly",
                "published_date": date(2025, 3, 25),
                "data": {"lưu_chuyển_tiền_thuần_từ_hoạt_động_kinh_doanh": 1350e9 if is_winner else 90e9},
            }
        )

    # Org B SECRET issuer (must never leak into Org A)
    core_profiles.append(
        {
            "ticker": "SECRET",
            "company_name": "Secret Corp",
            "sector": "Vận tải - Cảng biển",
            "industry": "Vận tải biển",
            "exchange": "HOSE",
            "market_cap": 50_000_000_000_000,
        }
    )
    core_ratios.append(
        {
            "symbol": "SECRET",
            "ratio_date": date(2024, 12, 31),
            "frequency": "yearly",
            "published_date": date(2025, 3, 20),
            "roe": 99.0,
            "pe": 2.0,
        }
    )

    fake_core = _FakeCoreMetricsReader(
        profiles=core_profiles,
        ratios=core_ratios,
        statements=core_statements,
    )

    async with session_factory() as session:
        for tk in org_a_tickers:
            doc_id = f"doc-{tk.lower()}"
            session.add(
                Document(
                    id=doc_id,
                    tenant_id="aiinvest-default",
                    project_id=tk,
                    filename=f"{tk}.pdf",
                    storage_path=f"/tmp/{tk}.pdf",
                    is_active=True,
                )
            )
            if tk not in {"AAA", "CCC", "PDFONLY"}:
                # Baseline PIT-valid FY2024 version (BBB also gets a future 2026 version below)
                session.add(
                    DocumentVersion(
                        id=f"ver-{tk.lower()}-2024",
                        document_id=doc_id,
                        version_no=1,
                        file_hash=f"h-{tk.lower()}-24",
                        valid_from=datetime(2025, 3, 25, tzinfo=UTC),
                        source_published_at=datetime(2025, 3, 25, tzinfo=UTC),
                    )
                )
        # Org B tenant isolation
        session.add(
            Document(
                id="doc-secret",
                tenant_id="org-b",
                project_id="SECRET",
                filename="secret.pdf",
                storage_path="/tmp/secret.pdf",
                is_active=True,
            )
        )
        # Newer FY2025 VERIFIED PDF for AAA (Core PG only had FY2024!) -> triggers Hybrid overlay
        session.add(
            DocumentVersion(
                id="ver-aaa-2025",
                document_id="doc-aaa",
                version_no=1,
                file_hash="h-aaa-25",
                valid_from=datetime(2026, 3, 25, tzinfo=UTC),
                source_published_at=datetime(2026, 3, 25, tzinfo=UTC),
            )
        )
        # Future version for BBB (must NOT leak at 2026-04-01)
        session.add(
            DocumentVersion(
                id="ver-bbb-future",
                document_id="doc-bbb",
                version_no=2,
                file_hash="h-bbb-26",
                valid_from=datetime(2026, 5, 10, tzinfo=UTC),
            )
        )
        # Revoked version for CCC (must NOT leak)
        session.add(
            DocumentVersion(
                id="ver-ccc-revoked",
                document_id="doc-ccc",
                version_no=1,
                file_hash="h-ccc-rev",
                valid_from=datetime(2026, 3, 1, tzinfo=UTC),
                status="REVOKED",
            )
        )
        # PDFONLY ticker missing from Core PG completely
        session.add(
            DocumentVersion(
                id="ver-pdfonly-2025",
                document_id="doc-pdfonly",
                version_no=1,
                file_hash="h-pdfonly-25",
                valid_from=datetime(2026, 3, 28, tzinfo=UTC),
            )
        )
        session.add_all(
            [
                SagExtractedTable(
                    table_id="tbl-aaa-is-2025",
                    project_id="AAA",
                    document_version_id="ver-aaa-2025",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                    table_title="Báo cáo kết quả kinh doanh năm 2025",
                    section_path="BCTC_AUDITED",
                    page_from=9,
                    page_to=9,
                    currency="VND",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "Năm 2025"]],
                    rows_json=[
                        [_cell("Doanh thu thuần"), _cell("8.500", 8500.0)],
                        [_cell("Lợi nhuận gộp"), _cell("3.400", 3400.0)],
                        [_cell("Lợi nhuận sau thuế thu nhập doanh nghiệp"), _cell("1.200", 1200.0)],
                    ],
                    verification_status="VERIFIED",
                    table_hash="th-aaa-25",
                    valid_from=datetime(2026, 3, 25, tzinfo=UTC),
                ),
                SagExtractedTable(
                    table_id="tbl-aaa-bs-2025",
                    project_id="AAA",
                    document_version_id="ver-aaa-2025",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                    table_title="Bảng cân đối kế toán 2025",
                    section_path="BCTC_AUDITED",
                    page_from=5,
                    page_to=5,
                    currency="VND",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "31/12/2025"]],
                    rows_json=[
                        [_cell("D. VỐN CHỦ SỞ HỮU"), _cell("5.000", 5000.0)],
                        [_cell("Vay ngắn hạn"), _cell("1.000", 1000.0)],
                    ],
                    verification_status="VERIFIED",
                    table_hash="th-aaa-bs-25",
                    valid_from=datetime(2026, 3, 25, tzinfo=UTC),
                ),
                SagExtractedTable(
                    table_id="tbl-bbb-future",
                    project_id="BBB",
                    document_version_id="ver-bbb-future",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2026,
                    fiscal_quarter=0,
                    table_title="Báo cáo KQKD 2026",
                    page_from=1,
                    page_to=1,
                    currency="VND",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "2026"]],
                    rows_json=[[_cell("Doanh thu thuần"), _cell("999.999", 999999.0)]],
                    verification_status="VERIFIED",
                    table_hash="th-bbb-fut",
                    valid_from=datetime(2026, 5, 10, tzinfo=UTC),
                ),
                SagExtractedTable(
                    table_id="tbl-ccc-revoked",
                    project_id="CCC",
                    document_version_id="ver-ccc-revoked",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2026,
                    fiscal_quarter=0,
                    table_title="Báo cáo KQKD 2026",
                    page_from=1,
                    page_to=1,
                    currency="VND",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "2026"]],
                    rows_json=[[_cell("Doanh thu thuần"), _cell("888.888", 888888.0)]],
                    verification_status="VERIFIED",
                    table_hash="th-ccc-rev",
                    valid_from=datetime(2026, 3, 1, tzinfo=UTC),
                ),
                SagExtractedTable(
                    table_id="tbl-pdfonly-2025",
                    project_id="PDFONLY",
                    document_version_id="ver-pdfonly-2025",
                    sub_doc_slot="BCTC_AUDITED",
                    fiscal_year=2025,
                    fiscal_quarter=0,
                    table_title="Bảng CĐKT & KQKD PDFONLY",
                    page_from=3,
                    page_to=3,
                    currency="VND",
                    unit_multiplier=1e9,
                    headers_json=[["Chỉ tiêu", "Năm 2025"]],
                    rows_json=[
                        [_cell("Doanh thu thuần"), _cell("2.200", 2200.0)],
                        [_cell("Lợi nhuận gộp"), _cell("700", 700.0)],
                        [_cell("Lợi nhuận sau thuế thu nhập doanh nghiệp"), _cell("310", 310.0)],
                        [_cell("D. VỐN CHỦ SỞ HỮU"), _cell("1.500", 1500.0)],
                    ],
                    verification_status="VERIFIED",
                    table_hash="th-pdfonly",
                    valid_from=datetime(2026, 3, 28, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

        # 1. Default 4-pillar quality screen across PIT-active org tickers:
        # CCC only has a REVOKED DocumentVersion -> strictly excluded (9 PIT-active tickers, #9 "III" #1!)
        ranked, total_matched, desc = await screen_universe_metrics(
            session,
            tenant_id="aiinvest-default",
            as_of_date=date(2026, 4, 1),
            core_reader=fake_core,
            use_default_quality_screen=True,
            limit=5,
        )
        assert total_matched == 9
        assert all(r["ticker"] != "CCC" for r in ranked)
        assert ranked[0]["ticker"] == "III"
        assert all(r["ticker"] != "SECRET" for r in ranked)
        assert "Core PG Read-Only" in desc

        # 2. Combined Valuation (`pe <= 11`) + Fundamentals (`roe >= 0.20`) + Sector ("vận tải biển")
        screened, match_cnt, _ = await screen_universe_metrics(
            session,
            tenant_id="aiinvest-default",
            as_of_date=date(2026, 4, 1),
            core_reader=fake_core,
            sectors=["vận tải biển"],
            filters=[
                {"metric": "pe", "op": "<=", "value": 11.0},
                {"metric": "roe", "op": ">=", "value": 0.20},
                {"metric": "market_cap", "op": ">=", "value": 1e12},
            ],
            sort_by="roe",
            sort_dir="desc",
        )
        assert match_cnt == 1
        assert screened[0]["ticker"] == "III"
        assert screened[0]["pe"] == pytest.approx(8.2)

        # 3. Verify Hybrid PDF freshness overlay on AAA & PDF-only ticker presence
        hybrid_rows, _, _ = await screen_universe_metrics(
            session,
            tenant_id="aiinvest-default",
            as_of_date=date(2026, 4, 1),
            core_reader=fake_core,
            tickers=["AAA", "PDFONLY", "BBB"],
            sort_by="net_revenue",
            sort_dir="desc",
        )
        by_tk = {r["ticker"]: r for r in hybrid_rows}
        assert by_tk["AAA"]["fiscal_year"] == 2025
        assert by_tk["AAA"]["source_kind"] == "HYBRID_CORE_AND_VERIFIED_PDF"
        assert by_tk["AAA"]["roe"] == pytest.approx(1200.0 / 5000.0, rel=1e-5)
        assert by_tk["AAA"]["pe"] == pytest.approx(10.5)  # Preserved from Core quarterly row!
        # B01 Balance Sheet (page 5) is processed first by _table_priority_score before B02 (page 9)
        assert by_tk["AAA"]["source_table_id"] == "tbl-aaa-bs-2025"
        assert by_tk["BBB"]["fiscal_year"] == 2024  # Future 2026 PDF table did not leak!
        assert by_tk["PDFONLY"]["source_kind"] == "VERIFIED_TABLE"
        assert by_tk["PDFONLY"]["net_revenue"] == pytest.approx(2200e9)

    await engine.dispose()


@pytest.mark.asyncio
async def test_l2_screen_universe_core_pg_outage_falls_back_to_sag_verified() -> None:
    """When Core PG times out, screening fails safe to SAG VERIFIED PDF tables without crashing."""

    class _DownCoreReader:
        async def fetch_issuer_profiles(self, tickers=None):
            raise TimeoutError("Core DB unavailable")

        async def fetch_pit_financial_ratios(self, as_of_date, *, tickers=None, lookback_years=7):
            raise TimeoutError("Core DB unavailable")

        async def fetch_pit_financial_statements(
            self, as_of_date, *, tickers=None, lookback_years=5, prefer_annual=True
        ):
            raise TimeoutError("Core DB unavailable")

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            raise TimeoutError("Core DB unavailable")

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add(
            Document(
                id="doc-hpg",
                tenant_id="aiinvest-default",
                project_id="HPG",
                filename="hpg.pdf",
                storage_path="/tmp/hpg.pdf",
                is_active=True,
            )
        )
        session.add(
            DocumentVersion(
                id="ver-hpg",
                document_id="doc-hpg",
                version_no=1,
                file_hash="h-hpg",
                valid_from=datetime(2025, 4, 1, tzinfo=UTC),
            )
        )
        session.add(
            KgNode(
                node_id="company:HPG",
                node_type="COMPANY",
                label="Tập đoàn Hòa Phát",
                ticker="HPG",
                metadata_json={"sector": "Thép", "industry": "Vật liệu xây dựng"},
                effective_from=date(2025, 1, 1),
            )
        )
        session.add(
            SagExtractedTable(
                table_id="tbl-hpg-2025",
                project_id="HPG",
                document_version_id="ver-hpg",
                sub_doc_slot="BCTC_AUDITED",
                fiscal_year=2025,
                fiscal_quarter=0,
                table_title="Báo cáo KQKD 2025",
                page_from=4,
                page_to=4,
                currency="VND",
                unit_multiplier=1e9,
                headers_json=[["Chỉ tiêu", "Năm 2025"]],
                rows_json=[
                    [_cell("Doanh thu thuần"), _cell("140.000", 140000.0)],
                    [_cell("Lợi nhuận gộp"), _cell("19.600", 19600.0)],
                ],
                verification_status="VERIFIED",
                table_hash="th-hpg",
                valid_from=datetime(2025, 4, 1, tzinfo=UTC),
            )
        )
        await session.commit()

        rows, cnt, desc = await screen_universe_metrics(
            session,
            tenant_id="aiinvest-default",
            as_of_date=date(2025, 6, 30),
            core_reader=_DownCoreReader(),
            sectors=["thép"],
            sort_by="gross_margin",
        )
        assert cnt == 1
        assert rows[0]["ticker"] == "HPG"
        assert rows[0]["sector"] == "Thép"
        assert rows[0]["gross_margin"] == pytest.approx(19600.0 / 140000.0, rel=1e-5)
        assert "SAG VERIFIED PDF" in desc

    await engine.dispose()


# ── Layer 3: Full ASGI HTTP Vertical Slice `/api/v1/research-chat/tickers` ─


@pytest.mark.asyncio
async def test_l3_asgi_tickers_endpoint_core_catalog_fallback_and_auth() -> None:
    """Real FastAPI ASGI route serves Core PG sectors/profiles, falls back on outage, fails closed."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async with session_factory() as session:
        session.add_all(
            [
                Document(
                    id="doc-fpt-l3",
                    tenant_id="aiinvest-default",
                    project_id="FPT",
                    filename="fpt.pdf",
                    storage_path="/tmp/fpt.pdf",
                    is_active=True,
                ),
                Document(
                    id="doc-mwg-l3",
                    tenant_id="aiinvest-default",
                    project_id="MWG",
                    filename="mwg.pdf",
                    storage_path="/tmp/mwg.pdf",
                    is_active=True,
                ),
                Document(
                    id="doc-inactive-l3",
                    tenant_id="aiinvest-default",
                    project_id="INACT",
                    filename="inact.pdf",
                    storage_path="/tmp/inact.pdf",
                    is_active=False,
                ),
                Document(
                    id="doc-secret-l3",
                    tenant_id="other-org",
                    project_id="SECRET",
                    filename="secret.pdf",
                    storage_path="/tmp/secret.pdf",
                    is_active=True,
                ),
            ]
        )
        session.add_all(
            [
                DocumentVersion(
                    id="ver-fpt-l3",
                    document_id="doc-fpt-l3",
                    version_no=1,
                    file_hash="h-fpt-l3",
                    valid_from=datetime(2025, 4, 1, tzinfo=UTC),
                ),
                DocumentVersion(
                    id="ver-mwg-l3",
                    document_id="doc-mwg-l3",
                    version_no=1,
                    file_hash="h-mwg-l3",
                    valid_from=datetime(2025, 4, 1, tzinfo=UTC),
                ),
            ]
        )
        session.add(
            SagExtractedTable(
                table_id="tbl-fpt-l3",
                project_id="FPT",
                document_version_id="ver-fpt-l3",
                sub_doc_slot="BCTC_AUDITED",
                fiscal_year=2025,
                fiscal_quarter=0,
                table_title="KQKD",
                page_from=1,
                page_to=1,
                headers_json=[["A", "2025"]],
                rows_json=[[_cell("Doanh thu thuần"), _cell("1", 1.0)]],
                verification_status="VERIFIED",
                table_hash="th-fpt-l3",
                valid_from=datetime(2025, 4, 1, tzinfo=UTC),
            )
        )
        await session.commit()

    fake_core = _FakeCoreMetricsReader(
        profiles=[
            {
                "ticker": "FPT",
                "company_name": "Công ty Cổ phần FPT",
                "sector": "Công nghệ",
                "industry": "Phần mềm",
                "exchange": "HOSE",
                "market_cap": 195_000_000_000_000,
            },
            {
                "ticker": "MWG",
                "company_name": "CTCP Đầu tư Thế Giới Di Động",
                "sector": "Bán lẻ",
                "industry": "Bán lẻ tổng hợp",
                "exchange": "HOSE",
                "market_cap": 95_000_000_000_000,
            },
            {
                "ticker": "EXTRA_NON_INGESTED",
                "company_name": "Not Injected Corp",
                "sector": "Tài chính",
                "industry": "Ngân hàng",
                "exchange": "HOSE",
                "market_cap": 1_000_000_000_000,
            },
        ],
        ratios=[],
        statements=[],
    )

    class _DownCoreReader:
        async def fetch_issuer_profiles(self, tickers=None):
            raise RuntimeError("Core DB offline")

        async def fetch_pit_financial_ratios(self, as_of_date, *, tickers=None, lookback_years=7):
            return []

        async def fetch_pit_financial_statements(
            self, as_of_date, *, tickers=None, lookback_years=5, prefer_annual=True
        ):
            return []

        async def fetch_pit_market_caps(self, as_of_date, *, tickers=None):
            return {}

    app = FastAPI()

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Any, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    app.include_router(rc_api.router, prefix="/api/v1")

    async def _override_session():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _override_session
    app.dependency_overrides[rc_api.get_core_metrics_reader] = lambda: fake_core

    bff_headers = {
        "X-SAG-BFF-Service-Token": "contract-bff-secret",
        "X-Continuum-User-Id": "analyst-1",
        "X-Continuum-Organization-Id": "aiinvest-default",
        "X-Continuum-Project-Id": "all",
        "X-Continuum-Security-Partition": "all",
        "X-Continuum-Allowed-Project-Ids": "*",
    }

    with patch.object(global_settings, "bff_service_token", "contract-bff-secret"):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test-sag",
        ) as client:
            # 1. Fail-closed auth without BFF token
            unauth = await client.get("/api/v1/research-chat/tickers")
            assert unauth.status_code in (401, 403, 503)

            # 2. Enriched Core PG Read-Only catalog (active org tickers only, zero inactive/secret)
            resp = await client.get(
                "/api/v1/research-chat/tickers?as_of_date=2025-06-30",
                headers=bff_headers,
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["organization_id"] == "aiinvest-default"
            assert data["tickers"] == ["FPT", "MWG"]
            assert "INACT" not in data["tickers"]
            assert "SECRET" not in data["tickers"]
            assert data["catalog_source"] == "CORE_DB_READONLY"
            assert data["sectors"] == ["Bán lẻ", "Công nghệ"]
            prof_map = {p["ticker"]: p for p in data["profiles"]}
            assert prof_map["FPT"]["company_name"] == "Công ty Cổ phần FPT"
            assert prof_map["FPT"]["latest_fiscal_year"] == 2025
            assert prof_map["FPT"]["market_cap"] == pytest.approx(195e12)
            assert prof_map["FPT"]["has_sag_docs"] is True

            # 3. Core PG outage degrades gracefully to SAG_LOCAL_FALLBACK with 200 OK
            app.dependency_overrides[rc_api.get_core_metrics_reader] = lambda: _DownCoreReader()
            fb_resp = await client.get("/api/v1/research-chat/tickers", headers=bff_headers)
            assert fb_resp.status_code == 200
            fb_data = fb_resp.json()
            assert fb_data["catalog_source"] == "SAG_LOCAL_FALLBACK"
            assert fb_data["tickers"] == ["FPT", "MWG"]

    await engine.dispose()
