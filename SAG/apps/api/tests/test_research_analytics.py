"""Rule 08 L1 + L2 tests for deterministic analytics (KPI packs + engines) on real Core PG rows.

Accuracy is checked against an INDEPENDENT source where one exists: the vendor's own ratio
snapshot (`ratios` rows, e.g. ROE / LDR / CIR for ACB at Q3/2026) captured in the same fixture.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from sag_api.research.analytics import (
    KpiStatus,
    PeriodRef,
    PlanTarget,
    compute_kpi,
    compute_kpi_pack,
    earnings_quality,
    latest_analysis_period,
    peer_compare,
    plan_vs_actual,
    red_flag_scan,
    vendor_snapshot,
)
from sag_api.research.facts import build_fact_set, resolve_template

FIXTURE = Path(__file__).parent / "fixtures" / "core_financial_statements_sample.json"
AS_OF = date(2026, 11, 26)
Q3_26_TTM = PeriodRef("TTM", 2026, 3)


@pytest.fixture(scope="module")
def sample() -> dict[str, Any]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def fs(sample: dict[str, Any]):
    templates = {sym: resolve_template(ind) for sym, ind in sample["industries"].items()}
    return build_fact_set(sample["rows"], templates_by_ticker=templates, as_of_date=AS_OF)


def _vendor(sample: dict[str, Any], sym: str, key: str) -> float:
    for r in sample["rows"]:
        if r["symbol"] == sym and r["statement_type"] == "ratios" and r["period_end"] == "2026-09-30":
            return float(r["data"][key])
    raise KeyError(key)


def test_l1_period_labels_and_prior_year() -> None:
    assert Q3_26_TTM.label == "TTM Q3/2026"
    assert Q3_26_TTM.prior_year() == PeriodRef("TTM", 2025, 3)
    assert PeriodRef("FY", 2025).days == 365.0 and PeriodRef("Q", 2026, 3).days == 91.25


def test_l2_latest_analysis_period_is_ttm_q3_2026(fs) -> None:
    assert latest_analysis_period(fs, "ACB") == Q3_26_TTM


def test_l2_roe_formula_wiring_and_independent_vendor_agreement(fs, sample) -> None:
    kv = compute_kpi(fs, "ACB", "roe", Q3_26_TTM)
    assert kv.status is KpiStatus.OK and kv.value is not None
    npat = fs.get("ACB", "npat", "TTM", 2026, 3).fact.value
    eq_end = fs.get("ACB", "equity", "Q", 2026, 3).fact.value
    eq_begin = fs.get("ACB", "equity", "Q", 2025, 3).fact.value
    assert kv.value == pytest.approx(npat / ((eq_end + eq_begin) / 2))
    roles = sorted(i["role"] for i in kv.inputs)
    assert roles == ["flow", "stock_begin", "stock_end"]
    # independent check: vendor ROE snapshot within 1 percentage point
    assert abs(kv.value - _vendor(sample, "ACB", "rt_prt_roe")) < 0.01


def test_l2_bank_ldr_and_cir_agree_with_vendor_snapshot(fs, sample) -> None:
    ldr = compute_kpi(fs, "ACB", "loan_to_deposit", Q3_26_TTM)
    cir = compute_kpi(fs, "ACB", "cir", Q3_26_TTM)
    assert ldr.status is KpiStatus.OK and cir.status is KpiStatus.OK
    assert ldr.value == pytest.approx(_vendor(sample, "ACB", "rt_bank_ldr"), abs=0.005)
    assert abs(cir.value - abs(_vendor(sample, "ACB", "rt_bank_cir"))) < 0.01


def test_l2_cash_flow_kpis_use_audited_fy_not_quarterly_cf(fs) -> None:
    kv = compute_kpi(fs, "VHM", "cfo_to_npat", Q3_26_TTM)
    assert kv.status is KpiStatus.OK
    assert kv.period == "FY2025"
    assert "FY2025" in kv.detail
    cfo = fs.get("VHM", "cfo", "FY", 2025).fact.value
    npat = fs.get("VHM", "npat", "FY", 2025).fact.value
    assert kv.value == pytest.approx(cfo / npat)
    assert fs.get("VHM", "cfo", "TTM", 2026, 3).status.value == "NOT_FOUND"


def test_l2_not_applicable_and_insufficient_are_explicit(fs) -> None:
    assert compute_kpi(fs, "VHM", "credit_cost", Q3_26_TTM).status is KpiStatus.NOT_APPLICABLE
    old = compute_kpi(fs, "ACB", "roe", PeriodRef("TTM", 2019, 4))
    assert old.status is KpiStatus.INSUFFICIENT_DATA and old.value is None and "thiếu chỉ tiêu" in old.detail


def test_l2_kpi_pack_is_template_specific(fs) -> None:
    bank_ids = {kv.kpi_id for kv in compute_kpi_pack(fs, "ACB")}
    sec_ids = {kv.kpi_id for kv in compute_kpi_pack(fs, "VND")}
    assert {"credit_cost", "cir", "loan_to_deposit"} <= bank_ids
    assert "gross_margin" not in bank_ids
    assert {"margin_to_equity", "fvtpl_to_equity"} <= sec_ids
    assert "credit_cost" not in sec_ids


def test_l2_red_flags_detect_real_q3_2026_earnings_collapse_at_vnd(fs) -> None:
    out = red_flag_scan(fs, "VND")
    codes = {f["code"]: f for f in out["flags"]}
    assert "EARNINGS_COLLAPSE_YOY" in codes
    flag = codes["EARNINGS_COLLAPSE_YOY"]
    assert flag["observed"] < -0.5 and flag["period"] == "Q3/2026"
    assert len(flag["evidence"]) == 2  # current and prior-year quarter facts


def test_l2_red_flags_surface_pnj_quarterly_loss_and_vendor_inconsistency(fs) -> None:
    codes = {f["code"] for f in red_flag_scan(fs, "PNJ")["flags"]}
    assert "QUARTERLY_LOSS" in codes
    assert "VENDOR_DATA_INCONSISTENCY" in codes


def test_l2_earnings_quality_bank_note_and_corporate_signals(fs) -> None:
    bank = earnings_quality(fs, "ACB")
    assert "CFO của ngân hàng" in bank["note"]
    assert not any(s["code"] == "LOW_CASH_CONVERSION" for s in bank["signals"])
    pnj = earnings_quality(fs, "PNJ")
    assert any(s["code"] == "LOW_CASH_CONVERSION" for s in pnj["signals"])


def test_l2_peer_compare_rejects_mixed_templates_and_ranks_same_template(fs) -> None:
    assert peer_compare(fs, ["ACB", "VHM"])["status"] == "MIXED_TEMPLATES"
    single = peer_compare(fs, ["ACB", "ZZZ"], kpi_ids=["roe"])
    assert single["status"] == "OK" and single["unknown_tickers"] == ["ZZZ"]


def test_l2_plan_vs_actual_uses_reported_quarters_only(fs) -> None:
    npat_q = [fs.get("VHM", "npat", "Q", 2026, q).fact.value for q in (1, 2, 3)]
    target = sum(npat_q) * 1.5  # synthetic target; actuals are real
    out = plan_vs_actual(fs, "VHM", 2026, [PlanTarget("npat", target, "test")])
    row = out["rows"][0]
    assert out["quarters_reported"] == 3
    assert row["ytd_actual"] == pytest.approx(sum(npat_q))
    assert row["achievement"] == pytest.approx(1 / 1.5)
    assert row["pace"] == "BEHIND"  # 66.7% achieved vs 75% of the year elapsed
    missing = plan_vs_actual(fs, "VHM", 2019, {"npat": 1e12})
    assert missing["rows"][0]["status"] == "INSUFFICIENT_DATA"


def test_l2_vendor_snapshot_is_labeled_and_bank_specific(fs) -> None:
    snap = {d["item"]: d for d in vendor_snapshot(fs, "ACB")}
    assert {"vr_npl_ratio", "vr_casa_ratio", "vr_npl_coverage"} <= set(snap)
    assert all(d["provenance"] == "VENDOR_RATIO_ALPHASTOCK" for d in snap.values())
    assert snap["vr_npl_coverage"]["value"] > 0  # vendor negative sign normalized
