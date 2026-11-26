"""Rule 08 L1 + L2 tests for the deterministic financial fact layer (`sag_api.research.facts`).

L1: catalog integrity, sign policies, PIT availability rule (parity with the Core PG reader).
L2: real captured Core PG rows (tests/fixtures/core_financial_statements_sample.json) through
    `build_fact_set`, and through the REAL `AsyncPGCoreMetricsReader` + `load_fact_set` seam with
    only the asyncpg pool replaced at the transport boundary.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sag_api.research.facts import (
    CATALOG,
    LookupStatus,
    Template,
    build_fact_set,
    items_for_template,
    load_fact_set,
    resolve_template,
)
from sag_api.research.facts.catalog import SignPolicy, canonical_sign
from sag_api.research.facts.store import (
    FLAG_IDENTITY_FAIL,
    effective_publication_date,
)
from sag_api.research.financial_metrics_store import AsyncPGCoreMetricsReader, _effective_pit_pub_date

FIXTURE = Path(__file__).parent / "fixtures" / "core_financial_statements_sample.json"
AS_OF = date(2026, 11, 26)


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fact_set(sample: dict[str, Any]):
    templates = {sym: resolve_template(ind) for sym, ind in sample["industries"].items()}
    return build_fact_set(sample["rows"], templates_by_ticker=templates, as_of_date=AS_OF)


def _raw(sample: dict[str, Any], sym: str, pe: str, st: str, freq: str, key: str) -> float:
    for r in sample["rows"]:
        if (r["symbol"], r["period_end"], r["statement_type"], r["frequency"], r["source"]) == (
            sym, pe, st, freq, "alphastock",
        ):
            return float(r["data"][key])
    raise KeyError((sym, pe, st, freq, key))


# ── L1: Catalog & invariants ────────────────────────────────────────────────


def test_l1_template_resolution_from_core_industry() -> None:
    assert resolve_template("Ngân hàng") is Template.BANK
    assert resolve_template("Chứng khoán") is Template.SECURITIES
    assert resolve_template("Bảo hiểm") is Template.INSURANCE
    assert resolve_template("Bất động sản") is Template.CORPORATE
    assert resolve_template(None) is Template.CORPORATE


def test_l1_catalog_has_unique_ids_and_keys_for_every_applicable_template() -> None:
    ids = [it.item_id for it in CATALOG]
    assert len(ids) == len(set(ids))
    for it in CATALOG:
        assert it.templates, it.item_id
        for tmpl in it.templates:
            assert it.keys[tmpl], f"{it.item_id} has no keys for {tmpl}"


def test_l1_no_key_maps_to_two_items_in_same_template_and_statement() -> None:
    """Exact-key collisions would silently assign one vendor number to two meanings."""
    for tmpl in Template:
        seen: dict[tuple[str, str], str] = {}
        for it in items_for_template(tmpl):
            for key in it.keys[tmpl]:
                owner = seen.setdefault((it.statement, key), it.item_id)
                assert owner == it.item_id, f"{tmpl}: key {key!r} used by {owner} and {it.item_id}"


def test_l1_sign_policies_follow_observed_vendor_conventions() -> None:
    assert canonical_sign(SignPolicy.EXPENSE_IS, "is_cost_of_goods_sold") == -1.0
    assert canonical_sign(SignPolicy.EXPENSE_IS, "4_giá_vốn_hàng_bán") == 1.0
    assert canonical_sign(SignPolicy.OUTFLOW, "cf_payments_for_fixed_assets") == -1.0
    assert canonical_sign(SignPolicy.OUTFLOW, "1_tiền_chi_để_mua_sắm_xây_dựng_tscđ_và_các_tài_sản_dài_hạn_khác") == -1.0
    assert canonical_sign(SignPolicy.AS_REPORTED, "is_net_profit_after_tax") == 1.0


@pytest.mark.parametrize(
    ("period_end", "published", "freq"),
    [
        (date(2025, 12, 31), "2026-10-16", "yearly"),  # vendor fetch date -> statutory lag
        (date(2026, 9, 30), "2026-11-06", "quarterly"),  # realistic disclosure date kept
        (date(2026, 6, 30), None, "quarterly"),
        (date(2025, 12, 31), "2026-01-05", "yearly"),  # < 10 days -> rejected
    ],
)
def test_l1_pit_availability_rule_matches_core_reader_rule(period_end: date, published: str | None, freq: str) -> None:
    lag = 90 if freq == "yearly" else 45
    assert effective_publication_date(period_end, published, freq) == _effective_pit_pub_date(
        period_end, published, default_lag_days=lag
    )


# ── L2: Real Core PG rows ───────────────────────────────────────────────────


def test_l2_templates_and_ignored_code_keyed_sources(fact_set, sample) -> None:
    assert fact_set.templates["ACB"] is Template.BANK
    assert fact_set.templates["VND"] is Template.SECURITIES
    assert fact_set.templates["BVH"] is Template.INSURANCE
    assert fact_set.templates["VHM"] is Template.CORPORATE
    cafef_rows = sum(1 for r in sample["rows"] if r["source"] == "cafef")
    assert cafef_rows > 0
    assert fact_set.ignored_rows.get("cafef") == cafef_rows


def test_l2_bank_fy2025_vi_schema_values_and_signs(fact_set, sample) -> None:
    def fy(item: str) -> float:
        res = fact_set.get("ACB", item, "FY", 2025)
        assert res.status is LookupStatus.OK, res.detail
        assert res.fact is not None
        return res.fact.value

    assert fy("npat") == 15_624_699_000_000.0
    assert fy("nii") == 26_905_695_000_000.0
    assert fy("credit_provision_expense") == 3_334_748_000_000.0  # VI expense kept positive
    llr_key = "2_dự_phòng_rủi_ro_cho_vay_và_cho_thuê_tài_chính_khách_hàng"
    raw_llr = _raw(sample, "ACB", "2025-12-31", "BS", "yearly", llr_key)
    assert raw_llr < 0
    assert fy("loan_loss_reserve") == -raw_llr  # contra asset -> positive reserve


def test_l2_bank_q3_2026_english_schema_expenses_become_positive(fact_set, sample) -> None:
    res = fact_set.get("ACB", "credit_provision_expense", "Q", 2026, 3)
    assert res.status is LookupStatus.OK and res.fact is not None
    raw = _raw(sample, "ACB", "2026-09-30", "IS", "quarterly", "is_provision_for_credit_losses")
    assert raw < 0
    assert res.fact.value == -raw
    assert res.fact.source_key == "is_provision_for_credit_losses"


def test_l2_quarters_sum_to_fy_and_ttm_is_derived_from_four_quarters(fact_set) -> None:
    quarters = [fact_set.get("ACB", "npat", "Q", 2025, q) for q in (1, 2, 3, 4)]
    assert all(q.status is LookupStatus.OK for q in quarters)
    qsum = sum(q.fact.value for q in quarters if q.fact is not None)
    fy = fact_set.get("ACB", "npat", "FY", 2025).fact
    assert fy is not None and abs(qsum - fy.value) / fy.value < 0.001

    ttm = fact_set.get("ACB", "npat", "TTM", 2025, 4)
    assert ttm.status is LookupStatus.OK and ttm.fact is not None
    assert ttm.fact.value == pytest.approx(qsum)
    assert ttm.fact.derivation == "sum of 4 consecutive quarters"


def test_l2_corporate_cogs_positive_in_both_schemas_and_gross_profit_identity_holds(fact_set, sample) -> None:
    fy_cogs = fact_set.get("VHM", "cogs", "FY", 2025).fact
    q3_cogs = fact_set.get("VHM", "cogs", "Q", 2026, 3).fact
    assert fy_cogs is not None and fy_cogs.value > 0 and fy_cogs.source_key == "4_giá_vốn_hàng_bán"
    assert q3_cogs is not None and q3_cogs.value > 0 and q3_cogs.source_key == "is_cost_of_goods_sold"
    assert q3_cogs.value == -_raw(sample, "VHM", "2026-09-30", "IS", "quarterly", "is_cost_of_goods_sold")
    gp_checks = [
        c for c in fact_set.identity_checks
        if c["ticker"] == "VHM" and c["identity"].startswith("IS: gross_profit")
    ]
    assert gp_checks and all(c["passed"] for c in gp_checks)


def test_l2_vendor_identity_violation_is_flagged_not_altered(fact_set, sample) -> None:
    """PNJ Q3/2026: vendor tax sign is inconsistent with PBT/NPAT; the fact must stay as reported."""
    res = fact_set.get("PNJ", "npat", "Q", 2026, 3)
    assert res.status is LookupStatus.OK and res.fact is not None
    assert res.fact.value == _raw(sample, "PNJ", "2026-09-30", "IS", "quarterly", "is_net_profit_after_tax")
    assert FLAG_IDENTITY_FAIL in res.fact.flags


def test_l2_lookup_statuses_are_explicit(fact_set) -> None:
    assert fact_set.get("VHM", "nii", "FY", 2025).status is LookupStatus.NOT_APPLICABLE
    assert fact_set.get("ZZZ", "npat", "FY", 2025).status is LookupStatus.UNKNOWN_TICKER
    assert fact_set.get("ACB", "not_an_item", "FY", 2025).status is LookupStatus.UNKNOWN_ITEM
    assert fact_set.get("ACB", "npat", "FY", 2019).status is LookupStatus.NOT_FOUND


def test_l2_pit_gate_drops_quarter_not_yet_published(sample) -> None:
    templates = {sym: resolve_template(ind) for sym, ind in sample["industries"].items()}
    early = build_fact_set(sample["rows"], templates_by_ticker=templates, as_of_date=date(2026, 11, 1))
    assert early.get("ACB", "npat", "Q", 2026, 3).status is LookupStatus.NOT_FOUND
    assert early.get("ACB", "npat", "FY", 2025).status is LookupStatus.OK
    assert early.ignored_rows.get("future_published", 0) > 0


def test_l2_rows_without_source_are_rejected_not_trusted(sample) -> None:
    rows = [{k: v for k, v in r.items() if k != "source"} for r in sample["rows"] if r["symbol"] == "ACB"]
    fs = build_fact_set(rows, templates_by_ticker={"ACB": Template.BANK}, as_of_date=AS_OF)
    assert not fs.facts
    assert fs.ignored_rows.get("unknown") == len(rows)


# ── L2: Real reader seam (asyncpg pool replaced at the transport boundary) ──


class _FakeConn:
    def __init__(self, sample: dict[str, Any], calls: list[str]) -> None:
        self._sample = sample
        self._calls = calls

    async def fetch(self, sql: str, *params: Any) -> list[dict[str, Any]]:
        self._calls.append(sql)
        if "FROM financial_statements" in sql:
            as_of, _min_date, tickers = params
            out = []
            for r in self._sample["rows"]:
                if tickers and r["symbol"] not in tickers:
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

    async def __aexit__(self, *exc: Any) -> None:
        return None


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)

    async def close(self) -> None:
        return None


async def test_l2_load_fact_set_through_real_core_reader(sample) -> None:
    calls: list[str] = []
    conn = _FakeConn(sample, calls)

    async def pool_factory(*_args: Any, **kwargs: Any) -> _FakePool:
        assert kwargs["server_settings"] == {"default_transaction_read_only": "on"}
        return _FakePool(conn)

    reader = AsyncPGCoreMetricsReader("postgresql://ro@localhost/aiinvest", pool_factory=pool_factory)
    fs = await load_fact_set(reader, tickers=["acb", "VHM"], as_of_date=AS_OF)

    stmt_sql = next(s for s in calls if "FROM financial_statements" in s)
    assert "AS source" in stmt_sql, "reader must expose vendor source for source-priority selection"
    assert "IN ('yearly', 'quarterly')" in stmt_sql, "fact layer needs quarterly rows"
    assert set(fs.templates) == {"ACB", "VHM"}
    assert fs.get("ACB", "npat", "FY", 2025).status is LookupStatus.OK
    assert fs.get("VHM", "revenue_net", "Q", 2026, 3).status is LookupStatus.OK
    assert Counter(f.template for f in fs.facts.values())["BANK"] > 0
