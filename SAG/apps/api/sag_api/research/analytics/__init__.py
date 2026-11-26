"""Deterministic analytical engines over the financial fact layer (zero LLM calls).

- `kpis`: sector KPI packs (corporate / bank / securities / insurance) with formula + input provenance.
- `engines`: earnings quality, red-flag scan, peer comparison, plan-vs-actual.
"""

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
    "earnings_quality",
    "latest_analysis_period",
    "peer_compare",
    "plan_vs_actual",
    "red_flag_scan",
    "vendor_snapshot",
]
