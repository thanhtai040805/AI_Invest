"""Deterministic analytical engines over the financial fact layer (zero LLM calls).

- `kpis`: sector KPI packs (corporate / bank / securities / insurance) with formula + input provenance.
- `engines`: earnings quality, red-flag scan, peer comparison, plan-vs-actual.
"""

from sag_api.research.analytics.business_drivers import (
    compute_multi_quarter_trajectory,
    extract_balance_sheet_note_breakdowns,
    extract_segment_drivers,
)
from sag_api.research.analytics.engines import (
    PlanTarget,
    RedFlag,
    earnings_quality,
    peer_compare,
    plan_vs_actual,
    red_flag_scan,
)
from sag_api.research.analytics.kpis import (
    KPI_SPECS,
    KpiStatus,
    KpiValue,
    PeriodRef,
    compute_kpi,
    compute_kpi_pack,
    latest_analysis_period,
    vendor_snapshot,
)

__all__ = [
    "KPI_SPECS",
    "KpiStatus",
    "KpiValue",
    "PeriodRef",
    "PlanTarget",
    "RedFlag",
    "compute_kpi",
    "compute_kpi_pack",
    "compute_multi_quarter_trajectory",
    "earnings_quality",
    "extract_balance_sheet_note_breakdowns",
    "extract_segment_drivers",
    "latest_analysis_period",
    "peer_compare",
    "plan_vs_actual",
    "red_flag_scan",
    "vendor_snapshot",
]
