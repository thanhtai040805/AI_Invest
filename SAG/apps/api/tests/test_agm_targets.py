"""Rule 08 L1/L2 tests for deterministic AGM plan-target extraction (real captured AGM_TARGETS tables)."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import pytest

from sag_api.research.analytics.agm_targets import classify_label, extract_plan_targets

FIXTURE = Path(__file__).parent / "fixtures" / "agm_targets_sample.json"


@pytest.fixture(scope="module")
def by_ticker() -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in json.loads(FIXTURE.read_text(encoding="utf-8")):
        out[t["project_id"]].append(t)
    return out


def test_l1_label_classification_rejects_non_plan_rows() -> None:
    assert classify_label("Doanh thu thuần") == ("revenue_net", True)
    assert classify_label("Tổng doanh thu") == ("revenue_net", False)
    assert classify_label("5. Lợi nhuận trước thuế") == ("pbt", True)
    assert classify_label("Lợi nhuận sau thuế thuộcvề cổ đông Công ty mẹ") == ("npat_parent", True)
    assert classify_label("LNTT riêng lẻ") is None
    assert classify_label("Lợi nhuận sau thuế chưa phân phối năm 2025") is None
    assert classify_label("Doanh thu hoạt động tài chính") is None
    assert classify_label("Tỷ suất LNST/VĐL") is None
    assert classify_label("Doanh thu hóa đơn") is None


def test_l2_frt_plan_with_row_unit_cell(by_ticker) -> None:
    ex = extract_plan_targets(by_ticker["FRT"], 2026)
    assert ex.targets["revenue_net"].value == pytest.approx(59_500e9)
    assert ex.targets["revenue_net"].exact_definition is True
    assert ex.targets["pbt"].value == pytest.approx(1_550e9)


def test_l2_hah_adjusted_plan_supersedes_original(by_ticker) -> None:
    # At 2025-12-31 only the Dec-2025 EGM tables exist (both 4,556 original and 5,057 adjusted have equal
    # 3-table support), so `adjusted=True` is the sole tiebreaker preventing a false CONFLICT.
    egm_2025_only = [t for t in by_ticker["HAH"] if t["ver_valid_from"][:10] <= "2025-12-31"]
    assert len(egm_2025_only) >= 3
    ex = extract_plan_targets(egm_2025_only, 2025)
    assert "revenue_net" not in ex.conflicts
    rev = ex.targets["revenue_net"]
    assert rev.adjusted is True and rev.value == pytest.approx(5_057e9)
    assert any(c.value == pytest.approx(4_556e9) for c in ex.candidates), "original plan must still be seen"


def test_l2_pnj_misaligned_negative_column_rejected_and_majority_wins(by_ticker) -> None:
    ex = extract_plan_targets(by_ticker["PNJ"], 2026)
    assert any(r["reason"] == "NON_POSITIVE" for r in ex.rejected)
    assert ex.targets["pbt"].value == pytest.approx(4_270_967e6)
    assert ex.targets["npat"].value == pytest.approx(3_408_995e6)
    assert ex.targets["revenue_net"].value == pytest.approx(48_660_134e6)
    assert ex.targets["revenue_net"].exact_definition is False


def test_l2_wrong_table_unit_is_a_conflict_until_prior_actual_disambiguates(by_ticker) -> None:
    raw = extract_plan_targets(by_ticker["ACB"], 2025)
    assert "pbt" in raw.conflicts and "pbt" not in raw.targets
    checked = extract_plan_targets(by_ticker["ACB"], 2025, prior_actuals={"pbt": 21.0e12})
    assert checked.targets["pbt"].value == pytest.approx(23_000e9)
    assert any(r["reason"] == "IMPLAUSIBLE_VS_PRIOR_FY" for r in checked.rejected)


def test_l2_year_guard_and_parent_only_exclusion(by_ticker) -> None:
    # GAS "Kế hoạch" without a year (prior-year review table) must not become a FY2025 target
    assert extract_plan_targets(by_ticker["GAS"], 2025).targets == {}
    pvt = extract_plan_targets(by_ticker["PVT"], 2025)
    assert pvt.targets["revenue_net"].value == pytest.approx(10_300e9), "consolidated, not parent-only 3,000"
    assert extract_plan_targets(by_ticker["FRT"], 2024).targets == {}


def test_l2_garbled_bilingual_numbers_rejected_by_magnitude_guard(by_ticker) -> None:
    ex = extract_plan_targets(
        by_ticker["DXG"], 2026, prior_actuals={"revenue_net": 4_192e9, "npat_parent": 231e9}
    )
    assert "revenue_net" not in ex.targets and "npat_parent" not in ex.targets
    assert {r["reason"] for r in ex.rejected} == {"IMPLAUSIBLE_VS_PRIOR_FY"}
