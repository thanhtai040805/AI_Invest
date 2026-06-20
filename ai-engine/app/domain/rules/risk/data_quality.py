"""Data Quality Check Engine — IOS v5.1

Module kiểm tra chất lượng dữ liệu, chạy trước mỗi pipeline.
Định nghĩa 8 checks theo DRY_RUN_CHECKLIST.md Phase 0.
"""

from __future__ import annotations
import logging
from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


class CheckSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARNING = "WARNING"
    SKIP = "SKIP"


@dataclass
class DataQualityCheck:
    check_id: str
    name: str
    severity: CheckSeverity
    status: CheckStatus = CheckStatus.PASS
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == CheckStatus.PASS


class DataQualityReport:
    def __init__(self):
        self.checks: List[DataQualityCheck] = []

    def add_check(self, check: DataQualityCheck):
        self.checks.append(check)

    @property
    def overall(self) -> str:
        if not self.checks:
            return "SKIP"
        if any(c.severity == CheckSeverity.CRITICAL and c.status == CheckStatus.FAIL for c in self.checks):
            return "FAIL"
        if any(c.status == CheckStatus.FAIL for c in self.checks):
            return "WARNING"
        return "PASS"

    @property
    def summary(self) -> Dict[str, Any]:
        return {
            "total": len(self.checks),
            "passed": sum(1 for c in self.checks if c.status == CheckStatus.PASS),
            "failed": sum(1 for c in self.checks if c.status == CheckStatus.FAIL),
            "skipped": sum(1 for c in self.checks if c.status == CheckStatus.SKIP),
            "overall": self.overall,
        }


def check_ohlcv_completeness(tickers: List[str], data: List[Dict], target_date: date) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-01", "OHLCV Completeness", CheckSeverity.CRITICAL)
    if not tickers:
        chk.status = CheckStatus.FAIL
        chk.reason = "Empty ticker list"
        return chk
        
    found_tickers = {r["ticker"] for r in data if r.get("date") == target_date}
    missing = [t for t in tickers if t not in found_tickers]
    if missing:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Missing tickers: {', '.join(missing)}"
        chk.details = {"missing_tickers": missing}
    return chk


def check_price_limit(data: List[Dict], target_date: date, max_change_pct: float = 7.5) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-02", "Price Limits", CheckSeverity.CRITICAL)
    violations = []
    for r in data:
        if r.get("date") != target_date: continue
        close = r.get("close_adj", 0)
        prev = r.get("prev_close_adj", 0)
        if prev and prev > 0:
            change = abs(close / prev - 1) * 100
            if change > max_change_pct:
                violations.append(f"{r['ticker']} ({change:.2f}%)")
    
    if violations:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Price limits violated: {', '.join(violations)}"
        chk.details = {"violations": violations}
    return chk


def check_volume_non_negative(data: List[Dict], target_date: date) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-03", "Volume Non-Negative", CheckSeverity.CRITICAL)
    negative_fields = []
    for r in data:
        if r.get("date") != target_date: continue
        for f in ["volume_total", "volume_continuous", "volume_atc", "volume_ato", "foreign_buy_vol", "foreign_sell_vol"]:
            val = r.get(f)
            if val is not None and val < 0:
                negative_fields.append(f"{r['ticker']}.{f}")
    
    if negative_fields:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Negative volume fields: {', '.join(negative_fields)}"
        chk.details = {"negative_fields": negative_fields}
    return chk


def check_volume_separation(data: List[Dict], target_date: date) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-04", "Volume Separation", CheckSeverity.WARNING)
    mismatches = []
    for r in data:
        if r.get("date") != target_date: continue
        total = r.get("volume_total")
        cont = r.get("volume_continuous", 0)
        atc = r.get("volume_atc", 0)
        ato = r.get("volume_ato", 0)
        if total is not None and abs(total - (cont + atc + ato)) > 1:
            mismatches.append(r["ticker"])
            
    if mismatches:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Volume mismatch: {', '.join(mismatches)}"
    return chk


def check_financial_freshness(financials: List[Dict], reference_date: date) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-05", "Financial Freshness", CheckSeverity.WARNING)
    stale = []
    for r in financials:
        ann_date = r.get("announcement_date")
        if ann_date is None:
            stale.append(r["ticker"])
            continue
        if (reference_date - ann_date).days > 180:
            stale.append(r["ticker"])
            
    if stale:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Stale financials for: {', '.join(stale)}"
    return chk


def check_corporate_action_applied(corp_actions: List[Dict]) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-06", "Corporate Action Applied", CheckSeverity.CRITICAL)
    unapplied = [a["ticker"] for a in corp_actions if not a.get("applied", False)]
    if unapplied:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Unapplied corp actions: {', '.join(unapplied)}"
        chk.details = {"unapplied": unapplied}
    return chk


def check_announcement_date_exists(financials: List[Dict], max_null_pct: float = 5.0) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-07", "Announcement Date Presence", CheckSeverity.CRITICAL)
    if not financials:
        chk.status = CheckStatus.SKIP
        return chk
        
    null_count = sum(1 for r in financials if r.get("announcement_date") is None)
    null_pct = (null_count / len(financials)) * 100
    if null_pct > max_null_pct:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Missing announcement dates: {null_count}/{len(financials)} ({null_pct:.2f}%)"
    return chk


def check_point_in_time_integrity(data: List[Dict], target_date: date) -> DataQualityCheck:
    chk = DataQualityCheck("CHECK-08", "PIT Integrity", CheckSeverity.CRITICAL)
    future = [r["ticker"] for r in data if r.get("date") and r["date"] > target_date]
    if future:
        chk.status = CheckStatus.FAIL
        chk.reason = f"Future data detected: {', '.join(future)}"
    return chk


def run_all_checks(
    tickers: List[str] = None,
    market_data: List[Dict] = None,
    financials: List[Dict] = None,
    corp_actions: List[Dict] = None,
    target_date: date = None
) -> DataQualityReport:
    if tickers is None: tickers = []
    if market_data is None: market_data = []
    if financials is None: financials = []
    if corp_actions is None: corp_actions = []
    if target_date is None: target_date = date.today()
    
    report = DataQualityReport()
    report.add_check(check_ohlcv_completeness(tickers, market_data, target_date))
    report.add_check(check_price_limit(market_data, target_date))
    report.add_check(check_volume_non_negative(market_data, target_date))
    report.add_check(check_volume_separation(market_data, target_date))
    report.add_check(check_financial_freshness(financials, target_date))
    report.add_check(check_corporate_action_applied(corp_actions))
    report.add_check(check_announcement_date_exists(financials))
    report.add_check(check_point_in_time_integrity(market_data, target_date))
    
    return report
