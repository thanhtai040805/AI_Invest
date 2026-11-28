"""Rule 08 L2 tests for deterministic analyst tools (`sag_api.research.tools.analyst_tools`).

Seams exercised unmocked: tool -> `load_fact_set` -> REAL `AsyncPGCoreMetricsReader` (only the asyncpg
pool is replaced at the transport boundary with captured Core PG rows) -> analytics engines ->
`ResearchEvidenceLedger` `[CALC-n]` traces -> Stage 7 `verify_numeric_claims_against_evidence`.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from sag_agent.tools import ToolContext
from sag_agent.types import CancellationToken, ToolRisk
from sag_api.core.principal_assertion import VerifiedPrincipal
from sag_api.db import models as _all_models  # noqa: F401
from sag_api.db.base import Base
from sag_api.db.models.document import Document
from sag_api.db.models.routing_rag import DocumentVersion
from sag_api.db.models.source import Source
from sag_api.db.models.stock_research import SagExtractedTable
from sag_api.research.analytics import compute_kpi, latest_analysis_period
from sag_api.research.facts import build_fact_set, resolve_template
from sag_api.research.facts.store import effective_publication_date
from sag_api.research.financial_metrics_store import AsyncPGCoreMetricsReader
from sag_api.research.quote_verifier import verify_numeric_claims_against_evidence
from sag_api.research.session_memory import FinancialZeroMemLedger
from sag_api.research.tools.analyst_tools import (
    build_analyst_tools,
    check_plan_vs_actual_tool,
    compare_peers_tool,
    get_financial_statements_tool,
    get_sector_kpis_tool,
    scan_financial_risks_tool,
)
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext

FIXTURE = Path(__file__).parent / "fixtures" / "core_financial_statements_sample.json"
AS_OF = date(2026, 11, 26)


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fs(sample: dict[str, Any]):
    templates = {sym: resolve_template(ind) for sym, ind in sample["industries"].items()}
    return build_fact_set(sample["rows"], templates_by_ticker=templates, as_of_date=AS_OF)


class _FakeConn:
    def __init__(self, sample: dict[str, Any], calls: list[str]) -> None:
        self._sample = sample
        self._calls = calls

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self._calls.append(sql)
        if "FROM financial_statements" in sql:
            as_of, _min_date, tickers, statement_types = params
            out = []
            for r in self._sample["rows"]:
                if tickers and r["symbol"] not in tickers:
                    continue
                if r["statement_type"].upper() not in statement_types:
                    continue
                pe = date.fromisoformat(r["period_end"])
                eff = effective_publication_date(pe, r["published_date"], r["frequency"])
                if pe <= as_of and eff <= as_of:
                    out.append({**r, "effective_published_date": eff})
            return out
        if "FROM stocks" in sql:
            return [
                {"ticker": sym, "company_name": sym, "sector": None, "industry": ind}
                for sym, ind in self._sample["industries"].items()
            ]
        raise AssertionError(f"unexpected SQL: {sql[:80]}")


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)

    async def close(self) -> None:
        return None


def _principal(allowed: frozenset[str] = frozenset({"*"})) -> VerifiedPrincipal:
    return VerifiedPrincipal(
        subject="u-analyst",
        organization_id="aiinvest-default",
        allowed_project_ids=allowed,
        issuer="continuum-be-bff",
        key_id="service-token",
        token_id="tok-analyst",
        issued_at=1700000000,
        expires_at=1700000060,
        tenant_id="aiinvest-default",
        allowed_partition_ids=frozenset({"*"}),
        conversation_scope="all",
    )


def _no_db_session() -> Any:
    raise AssertionError("analyst tools must not open SAG DB sessions")


def _make(sample: dict[str, Any], allowed: frozenset[str] = frozenset({"*"})):
    calls: list[str] = []
    conn = _FakeConn(sample, calls)

    async def pool_factory(*_args: Any, **_kwargs: Any) -> _FakePool:
        return _FakePool(conn)

    reader = AsyncPGCoreMetricsReader("postgresql://ro@localhost/aiinvest", pool_factory=pool_factory)
    evidence = ResearchEvidenceLedger()
    rctx = ResearchToolContext(
        as_of_date=AS_OF,
        principal=_principal(allowed),
        chat_mode="MOAT",
        session_factory=_no_db_session,
        session_ledger=FinancialZeroMemLedger(request_as_of_date=AS_OF),
        evidence=evidence,
        core_reader=reader,
    )

    async def _noop_progress(_p: Any) -> None:
        return None

    tctx = ToolContext(
        run_id="run-analyst",
        tool_call_id="tc-analyst",
        data=rctx,
        cancellation=CancellationToken(),
        _on_progress=_noop_progress,
    )
    return tctx, evidence, calls


def _trace(evidence: ResearchEvidenceLedger, calc_id: str) -> dict[str, Any]:
    return next(t for t in evidence.calculator_traces if t["calc_id"] == calc_id)


def test_l1_registry_exposes_four_read_only_tools() -> None:
    tools = build_analyst_tools()
    assert all(t.spec.risk is ToolRisk.READ_ONLY for t in tools)
    names = [t.spec.name for t in tools]
    assert names == ["get_financial_statements", "get_sector_kpis", "scan_financial_risks", "compare_peers",
                     "check_plan_vs_actual"]


async def test_l2_sector_kpis_register_typed_calc_traces_matching_engine(sample, fs) -> None:
    tctx, evidence, _ = _make(sample)
    res = await get_sector_kpis_tool.execute({"tickers": ["acb"]}, tctx)

    period = latest_analysis_period(fs, "ACB")
    expected = compute_kpi(fs, "ACB", "roe", period)
    line = next(ln for ln in res.content.splitlines() if "ROE" in ln and "[CALC-" in ln)
    cid = re.search(r"\[(CALC-\d+)\]", line).group(1)
    tr = _trace(evidence, cid)
    assert tr["ticker"] == "ACB"
    assert tr["format_style"] == "percent"
    assert tr["result"] == pytest.approx(expected.value)
    assert all(isinstance(v, dict) and "value" in v for v in tr["inputs"].values())
    assert f"{expected.value * 100:.2f}%" in line
    assert "ACB" in evidence.touched_tickers
    # vendor ratios are labelled as vendor-defined, never mixed into computed KPIs
    assert any(t["source"] == "VENDOR_RATIO_ALPHASTOCK" for t in evidence.calculator_traces)


async def test_l2_stage7_accepts_tool_kpi_and_rejects_altered_number(sample, fs) -> None:
    tctx, evidence, _ = _make(sample)
    res = await get_sector_kpis_tool.execute({"tickers": ["ACB"], "kpi_ids": ["roe"]}, tctx)
    cid = re.search(r"\[(CALC-\d+)\] ROE", res.content).group(1)
    roe_pct = _trace(evidence, cid)["result"] * 100

    ok = verify_numeric_claims_against_evidence(
        f"ROE của ACB đạt {roe_pct:.2f}% [{cid}].",
        sections_by_cid={},
        calculator_traces=evidence.calculator_traces,
    )
    assert ok.unsupported_claims == [] and ok.uncited_claims == []

    bad = verify_numeric_claims_against_evidence(
        f"ROE của ACB đạt {roe_pct + 7.3:.2f}% [{cid}].",
        sections_by_cid={},
        calculator_traces=evidence.calculator_traces,
    )
    assert bad.unsupported_claims, "an altered KPI must not pass Stage 7"


async def test_l2_cash_flow_kpi_is_evaluated_on_audited_fy(sample) -> None:
    tctx, _evidence, _ = _make(sample)
    res = await get_sector_kpis_tool.execute({"tickers": ["PNJ"], "kpi_ids": ["cfo_to_npat"]}, tctx)
    assert "đánh giá trên báo cáo năm FY2025" in res.content


async def test_l2_fact_sheet_values_equal_fact_layer_and_cache_avoids_refetch(sample, fs) -> None:
    tctx, evidence, calls = _make(sample)
    res = await get_financial_statements_tool.execute(
        {"tickers": ["VHM"], "view": "annual_history", "items": ["npat", "not_an_item"], "periods": 2}, tctx
    )
    cid = re.search(r"\[(CALC-\d+)\] VHM", res.content).group(1)
    tr = _trace(evidence, cid)
    assert tr["inputs"]["npat@FY2025"]["value"] == pytest.approx(fs.get("VHM", "npat", "FY", 2025).fact.value)
    assert set(k.split("@")[0] for k in tr["inputs"]) == {"npat"}, "unknown items must be dropped, not invented"

    await scan_financial_risks_tool.execute({"tickers": ["VHM"]}, tctx)
    assert sum("FROM financial_statements" in s for s in calls) == 1


async def test_l2_risk_scan_flags_vnd_earnings_collapse_with_threshold(sample) -> None:
    tctx, evidence, _ = _make(sample)
    res = await scan_financial_risks_tool.execute({"tickers": ["VND"]}, tctx)
    line = next(ln for ln in res.content.splitlines() if "EARNINGS_COLLAPSE_YOY" in ln)
    assert "(ngưỡng -0.5)" in line
    cid = re.search(r"\[(CALC-\d+)\]", line).group(1)
    assert _trace(evidence, cid)["result"] < -0.5


async def test_l2_compare_peers_refuses_mixed_templates(sample) -> None:
    tctx, evidence, _ = _make(sample)
    res = await compare_peers_tool.execute({"tickers": ["ACB", "VHM"]}, tctx)
    assert res.content.startswith("MIXED_TEMPLATES")
    assert evidence.calculator_traces == []


async def test_l2_input_errors_raise_instead_of_silent_empty(sample) -> None:
    tctx, _evidence, _ = _make(sample)
    with pytest.raises(ValueError):
        await get_sector_kpis_tool.execute({"tickers": []}, tctx)
    with pytest.raises(ValueError):
        await compare_peers_tool.execute({"tickers": ["ACB"]}, tctx)


async def test_l2_acl_denied_and_unknown_tickers_are_reported(sample) -> None:
    tctx, evidence, _ = _make(sample, allowed=frozenset({"ACB", "ZZZ"}))
    res = await get_sector_kpis_tool.execute({"tickers": ["ACB", "VND", "ZZZ"]}, tctx)
    assert "Không được phép truy cập: VND." in res.content
    assert "Không có BCTC trong Core PG tại PIT cho: ZZZ." in res.content
    assert {t["ticker"] for t in evidence.calculator_traces} == {"ACB"}


# ── check_plan_vs_actual: real AGM tables in a real SQLAlchemy schema (sqlite) + real Core reader ──


@pytest.fixture
async def agm_session_factory(tmp_path: Path):
    tables = json.loads((FIXTURE.parent / "agm_targets_sample.json").read_text(encoding="utf-8"))
    eng = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agm.db'}")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sf = async_sessionmaker(eng, expire_on_commit=False, class_=AsyncSession)
    async with sf() as s:
        s.add(Source(id="src-agm", name="HOSE", sag_source_config_id="cfg-agm"))
        for ver_id in sorted({t["document_version_id"] for t in tables}):
            t0 = next(t for t in tables if t["document_version_id"] == ver_id)
            s.add(Document(id=f"doc-{ver_id}"[:36], source_id="src-agm", project_id=t0["project_id"],
                           tenant_id="aiinvest-default", filename=f"{ver_id}.pdf", storage_path="/x", is_active=True))
            s.add(DocumentVersion(id=ver_id, document_id=f"doc-{ver_id}"[:36], version_no=1, file_hash=ver_id[:64],
                                  valid_from=datetime.fromisoformat(t0["ver_valid_from"])))
        await s.flush()
        for t in tables:
            s.add(SagExtractedTable(**{
                k: t[k] for k in (
                    "table_id", "project_id", "document_version_id", "sub_doc_slot", "fiscal_year", "fiscal_quarter",
                    "table_title", "section_path", "page_from", "page_to", "currency", "unit_raw", "unit_multiplier",
                    "headers_json", "rows_json", "verification_status", "table_hash",
                )
            }, valid_from=datetime.fromisoformat(t["valid_from"])))
        await s.commit()
    yield sf
    await eng.dispose()


def _with_sessions(tctx: ToolContext, sf: Any, as_of: date = AS_OF) -> ToolContext:
    return replace(tctx, data=replace(tctx.data, session_factory=sf, as_of_date=as_of))


async def test_l2_plan_vs_actual_pnj_uses_agm_targets_and_core_quarters(sample, fs, agm_session_factory) -> None:
    tctx, evidence, _ = _make(sample)
    res = await check_plan_vs_actual_tool.execute({"tickers": ["PNJ"]}, _with_sessions(tctx, agm_session_factory))
    assert res.details["statuses"] == {"PNJ": "OK"}
    line = next(ln for ln in res.content.splitlines() if ln.startswith("[CALC-") and "trước thuế" in ln)
    cid = re.search(r"\[(CALC-\d+)\]", line).group(1)
    ytd = sum(fs.get("PNJ", "pbt", "Q", 2026, q).fact.value for q in (1, 2, 3))
    tr = _trace(evidence, cid)
    assert tr["result"] == pytest.approx(ytd / 4_270_967e6)
    assert tr["format_style"] == "percent" and tr["ticker"] == "PNJ"
    assert "3Q/2026" in line
    rev = next(ln for ln in res.content.splitlines() if "Doanh thu thuần" in ln and "[CALC-" in ln)
    assert "⚠" in rev, "non-exact revenue definition must carry a caveat"

    ok = verify_numeric_claims_against_evidence(
        f"PNJ đạt {tr['result'] * 100:.1f}% kế hoạch LNTT năm 2026 [{cid}].",
        sections_by_cid={}, calculator_traces=evidence.calculator_traces,
    )
    assert ok.unsupported_claims == []


async def test_l2_plan_vs_actual_acb_independent_agm_actual_cross_check(sample, agm_session_factory) -> None:
    tctx, evidence, _ = _make(sample)
    res = await check_plan_vs_actual_tool.execute(
        {"tickers": ["ACB"], "fiscal_year": 2025}, _with_sessions(tctx, agm_session_factory)
    )
    line = next(ln for ln in res.content.splitlines() if ln.startswith("[CALC-"))
    tr = _trace(evidence, re.search(r"\[(CALC-\d+)\]", line).group(1))
    target = tr["inputs"]["plan_target:pbt@KH2025"]["value"]
    ytd = tr["inputs"]["ytd_actual:pbt@4Q/2025"]["value"]
    assert target == pytest.approx(23_000e9), "the 23 bn mis-scaled AGM table must be rejected by the magnitude guard"
    # ACB's own AGM report states FY2025 PBT actual = 19,539 bn: Core quarters must agree within 0.5%
    assert ytd == pytest.approx(19_539e9, rel=0.005)


async def test_l2_plan_vs_actual_respects_pit_of_agm_documents(sample, agm_session_factory) -> None:
    tctx, evidence, _ = _make(sample)
    res = await check_plan_vs_actual_tool.execute(
        {"tickers": ["PNJ"], "fiscal_year": 2026}, _with_sessions(tctx, agm_session_factory, date(2026, 3, 31))
    )
    assert res.details["statuses"] == {"PNJ": "NOT_FOUND"}
    assert "NOT_FOUND" in res.content and evidence.calculator_traces == []


async def test_stage7_accepts_vendor_snapshot_percent_inputs_without_false_positives(sample) -> None:
    tctx, evidence, _ = _make(sample)
    await get_sector_kpis_tool.execute({"tickers": ["ACB"]}, tctx)
    vtr = next(t for t in evidence.calculator_traces if "nhà cung cấp" in str(t.get("description") or ""))
    cid = vtr["calc_id"]
    npl_pct = float(vtr["inputs"]["vr_npl_ratio"]["value"]) * 100.0
    ver = verify_numeric_claims_against_evidence(
        f"Tỷ lệ nợ xấu của ACB đạt {npl_pct:.2f}% [{cid}].",
        sections_by_cid={},
        calculator_traces=evidence.calculator_traces,
    )
    assert ver.unsupported_claims == []
