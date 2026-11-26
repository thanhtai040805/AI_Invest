"""Deterministic analytical engines: earnings quality, red flags, peer comparison, plan vs actual.

Thresholds are explicit constants with the rationale next to them, so every signal is
reproducible and auditable. Engines never invent values: a signal only fires when all of its
KPI inputs are `OK`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sag_api.research.analytics.kpis import (
    KPI_BY_ID,
    KpiStatus,
    KpiValue,
    PeriodRef,
    compute_kpi,
    latest_analysis_period,
    vendor_snapshot,
)
from sag_api.research.facts import FactSet, LookupStatus, Template
from sag_api.research.facts.store import FLAG_FY_QSUM_MISMATCH, FLAG_IDENTITY_FAIL


@dataclass(frozen=True, slots=True)
class RedFlag:
    code: str
    severity: str  # HIGH | MEDIUM | LOW
    message_vi: str
    period: str
    kpi_id: str | None = None
    observed: float | None = None
    threshold: float | None = None
    evidence: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "message_vi": self.message_vi,
            "period": self.period,
            "kpi_id": self.kpi_id,
            "observed": self.observed,
            "threshold": self.threshold,
            "evidence": list(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class _Rule:
    code: str
    kpi_id: str
    op: str  # "<" | ">"
    threshold: float
    severity: str
    message_vi: str
    templates: frozenset[Template]
    require_positive_npat: bool = False


_T = Template
_RULES: tuple[_Rule, ...] = (
    # Earnings quality (non-bank: bank CFO mirrors deposit/loan flows, not earnings quality)
    _Rule("LOW_CASH_CONVERSION", "cfo_to_npat", "<", 0.5, "HIGH",
          "Dòng tiền HĐKD dưới 50% LNST: lợi nhuận chưa chuyển hóa thành tiền.",
          frozenset({_T.CORPORATE, _T.INSURANCE}), require_positive_npat=True),
    _Rule("HIGH_ACCRUALS", "accruals_ratio", ">", 0.10, "MEDIUM",
          "Tỷ lệ dồn tích > 10% tổng tài sản bình quân: chất lượng lợi nhuận thấp.",
          frozenset({_T.CORPORATE})),
    # Corporate leverage & liquidity
    _Rule("HIGH_NET_LEVERAGE", "net_debt_to_equity", ">", 1.5, "HIGH",
          "Nợ vay ròng vượt 1,5 lần vốn chủ sở hữu.", frozenset({_T.CORPORATE})),
    _Rule("WEAK_INTEREST_COVERAGE", "interest_coverage", "<", 1.5, "HIGH",
          "EBIT chưa tới 1,5 lần chi phí lãi vay.", frozenset({_T.CORPORATE})),
    _Rule("SHORT_TERM_REFINANCING_RISK", "st_debt_to_liquid_assets", ">", 2.0, "MEDIUM",
          "Vay ngắn hạn gấp hơn 2 lần tiền và đầu tư tài chính ngắn hạn.", frozenset({_T.CORPORATE})),
    _Rule("CURRENT_RATIO_BELOW_ONE", "current_ratio", "<", 1.0, "MEDIUM",
          "Tài sản ngắn hạn không đủ bù nợ ngắn hạn.", frozenset({_T.CORPORATE})),
    # Banks
    _Rule("HIGH_CREDIT_COST", "credit_cost", ">", 0.015, "MEDIUM",
          "Chi phí dự phòng vượt 1,5% dư nợ bình quân.", frozenset({_T.BANK})),
    _Rule("PROVISIONS_EAT_PPOP", "provision_to_ppop", ">", 0.5, "MEDIUM",
          "Dự phòng chiếm hơn 50% lợi nhuận trước dự phòng.", frozenset({_T.BANK})),
    _Rule("AGGRESSIVE_LOAN_GROWTH", "loan_growth_yoy", ">", 0.30, "MEDIUM",
          "Tăng trưởng cho vay trên 30%/năm, rủi ro chất lượng tài sản có độ trễ.", frozenset({_T.BANK})),
    _Rule("THIN_RESERVES", "reserve_to_loans", "<", 0.01, "MEDIUM",
          "Dự phòng rủi ro dưới 1% dư nợ cho vay.", frozenset({_T.BANK})),
    _Rule("THIN_EQUITY_BUFFER", "equity_to_assets", "<", 0.07, "MEDIUM",
          "Vốn chủ dưới 7% tổng tài sản.", frozenset({_T.BANK})),
    # Securities (TT121: margin lending cap 2.0x equity)
    _Rule("MARGIN_NEAR_LEGAL_CAP", "margin_to_equity", ">", 1.5, "HIGH",
          "Dư nợ cho vay vượt 1,5 lần vốn chủ (trần pháp lý 2,0 lần).", frozenset({_T.SECURITIES})),
    _Rule("HEAVY_PROPRIETARY_BOOK", "fvtpl_to_equity", ">", 1.0, "MEDIUM",
          "Danh mục FVTPL lớn hơn vốn chủ sở hữu: lợi nhuận nhạy với biến động giá tài sản.",
          frozenset({_T.SECURITIES})),
    _Rule("UNREALISED_PROFIT_DEPENDENCE", "unrealised_profit_share", ">", 0.4, "MEDIUM",
          "Hơn 40% LNTT đến từ lợi nhuận chưa thực hiện.", frozenset({_T.SECURITIES})),
    # Insurance
    _Rule("HIGH_CLAIMS_RATIO", "claims_ratio", ">", 0.7, "MEDIUM",
          "Tỷ lệ bồi thường vượt 70% phí bảo hiểm thuần.", frozenset({_T.INSURANCE})),
)

# Vendor ratio thresholds (bank asset quality; SBV considers NPL > 3% weak)
_VENDOR_RULES: tuple[tuple[str, str, str, float, str, str], ...] = (
    ("NPL_ABOVE_3PCT", "vr_npl_ratio", ">", 0.03, "HIGH", "Tỷ lệ nợ xấu (nhà cung cấp dữ liệu) vượt 3%."),
    ("NPL_ABOVE_2PCT", "vr_npl_ratio", ">", 0.02, "MEDIUM", "Tỷ lệ nợ xấu (nhà cung cấp dữ liệu) vượt 2%."),
    ("LOW_NPL_COVERAGE", "vr_npl_coverage", "<", 0.8, "MEDIUM",
     "Bao phủ nợ xấu (nhà cung cấp dữ liệu) dưới 80%."),
)

EARNINGS_COLLAPSE_YOY = -0.5


def _breach(op: str, value: float, threshold: float) -> bool:
    return value < threshold if op == "<" else value > threshold


def _kpi_flag(rule: _Rule, kv: KpiValue) -> RedFlag:
    return RedFlag(
        code=rule.code,
        severity=rule.severity,
        message_vi=rule.message_vi,
        period=kv.period,
        kpi_id=kv.kpi_id,
        observed=kv.value,
        threshold=rule.threshold,
        evidence=kv.inputs,
    )


def earnings_quality(fs: FactSet, ticker: str, period: PeriodRef | None = None) -> dict[str, Any]:
    tk = ticker.strip().upper()
    p = period or latest_analysis_period(fs, tk)
    tmpl = fs.templates.get(tk)
    if p is None or tmpl is None:
        return {"ticker": tk, "status": "INSUFFICIENT_DATA", "detail": "no statements in PIT window"}
    ids = ["cfo_to_npat", "accruals_ratio", "top_line_growth_yoy", "receivables_growth_yoy", "inventory_growth_yoy"]
    kpis = {k: compute_kpi(fs, tk, k, p) for k in ids}
    signals: list[RedFlag] = []
    for rule in _RULES:
        if rule.kpi_id in ("cfo_to_npat", "accruals_ratio") and tmpl in rule.templates:
            kv = kpis[rule.kpi_id]
            if kv.status is KpiStatus.OK and kv.value is not None and _breach(rule.op, kv.value, rule.threshold):
                signals.append(_kpi_flag(rule, kv))
    rev, rec, inv = kpis["top_line_growth_yoy"], kpis["receivables_growth_yoy"], kpis["inventory_growth_yoy"]
    for kv, code, gap, label in (
        (rec, "RECEIVABLES_OUTPACE_REVENUE", 0.20, "phải thu"),
        (inv, "INVENTORY_OUTPACE_REVENUE", 0.25, "hàng tồn kho"),
    ):
        if kv.status is KpiStatus.OK and rev.status is KpiStatus.OK and kv.value is not None and rev.value is not None:
            if kv.value - rev.value > gap:
                signals.append(
                    RedFlag(
                        code=code,
                        severity="MEDIUM",
                        message_vi=(
                            f"Tăng trưởng {label} ({kv.value:.1%}) vượt tăng trưởng doanh thu ({rev.value:.1%}) "
                            f"hơn {gap:.0%}."
                        ),
                        period=p.label,
                        kpi_id=kv.kpi_id,
                        observed=kv.value - rev.value,
                        threshold=gap,
                        evidence=(*kv.inputs, *rev.inputs),
                    )
                )
    note = (
        "CFO của ngân hàng phản ánh biến động tiền gửi/cho vay, không dùng làm thước đo chất lượng lợi nhuận."
        if tmpl is Template.BANK
        else ""
    )
    return {
        "ticker": tk,
        "template": tmpl.value,
        "period": p.label,
        "kpis": [kv.to_dict() for kv in kpis.values()],
        "signals": [s.to_dict() for s in signals],
        "note": note,
    }


def red_flag_scan(fs: FactSet, ticker: str, period: PeriodRef | None = None) -> dict[str, Any]:
    tk = ticker.strip().upper()
    p = period or latest_analysis_period(fs, tk)
    tmpl = fs.templates.get(tk)
    if p is None or tmpl is None:
        return {"ticker": tk, "status": "INSUFFICIENT_DATA", "flags": []}
    flags: list[RedFlag] = []
    checked: list[str] = []
    for rule in _RULES:
        if tmpl not in rule.templates:
            continue
        kv = compute_kpi(fs, tk, rule.kpi_id, p)
        checked.append(rule.code)
        if kv.status is not KpiStatus.OK or kv.value is None:
            continue
        if rule.require_positive_npat and not any(
            i["item"] == "npat" and i["value"] > 0 for i in kv.inputs
        ):
            continue
        if _breach(rule.op, kv.value, rule.threshold):
            flags.append(_kpi_flag(rule, kv))

    # Latest single quarter: loss or YoY earnings collapse
    last_q = fs.latest_period(tk, "Q")
    if last_q is not None:
        fy, fq = last_q
        cur = fs.get(tk, "npat", "Q", fy, fq)
        prev = fs.get(tk, "npat", "Q", fy - 1, fq)
        q_label = f"Q{fq}/{fy}"
        if cur.status is LookupStatus.OK and cur.fact is not None:
            ev = [cur.fact.to_dict()]
            if cur.fact.value < 0:
                flags.append(RedFlag("QUARTERLY_LOSS", "HIGH", "Quý gần nhất lỗ sau thuế.", q_label,
                                     observed=cur.fact.value, threshold=0.0, evidence=tuple(ev)))
            elif prev.status is LookupStatus.OK and prev.fact is not None and prev.fact.value > 0:
                yoy = cur.fact.value / prev.fact.value - 1.0
                if yoy < EARNINGS_COLLAPSE_YOY:
                    flags.append(RedFlag("EARNINGS_COLLAPSE_YOY", "HIGH",
                                         f"LNST quý gần nhất giảm {yoy:.0%} so với cùng kỳ.", q_label,
                                         observed=yoy, threshold=EARNINGS_COLLAPSE_YOY,
                                         evidence=(*ev, prev.fact.to_dict())))
        checked.extend(["QUARTERLY_LOSS", "EARNINGS_COLLAPSE_YOY"])

    # Vendor asset-quality ratios (bank), keep the most severe NPL flag only
    snap = {d["item"]: d for d in vendor_snapshot(fs, tk)}
    npl_flagged = False
    for code, item, op, thr, sev, msg in _VENDOR_RULES:
        if tmpl is not Template.BANK or item not in snap:
            continue
        checked.append(code)
        val = float(snap[item]["value"])
        if item == "vr_npl_ratio" and npl_flagged:
            continue
        if _breach(op, val, thr):
            flags.append(RedFlag(code, sev, msg, snap[item]["period"], kpi_id=item, observed=val, threshold=thr,
                                 evidence=(snap[item],)))
            npl_flagged = npl_flagged or item == "vr_npl_ratio"

    # Vendor data-consistency flags on facts used in the analysis period (transparency, LOW)
    inconsistent = sorted(
        {
            f"{f.item}@{f.period_label}"
            for f in fs.facts_for(tk)
            if (FLAG_IDENTITY_FAIL in f.flags or FLAG_FY_QSUM_MISMATCH in f.flags)
            and f.fiscal_year >= p.fiscal_year - 1
        }
    )
    if inconsistent:
        flags.append(RedFlag("VENDOR_DATA_INCONSISTENCY", "LOW",
                             "Một số số liệu nhà cung cấp không khớp đẳng thức kế toán hoặc tổng 4 quý; "
                             "cần đối chiếu BCTC gốc trước khi kết luận.",
                             p.label, evidence=tuple({"fact": x} for x in inconsistent[:20])))

    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    flags.sort(key=lambda f: order.get(f.severity, 3))
    return {
        "ticker": tk,
        "template": tmpl.value,
        "period": p.label,
        "rules_checked": checked,
        "flags": [f.to_dict() for f in flags],
    }


def peer_compare(
    fs: FactSet,
    tickers: Sequence[str],
    kpi_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    tks = [t.strip().upper() for t in tickers if t.strip()]
    known = [t for t in tks if t in fs.templates]
    unknown = [t for t in tks if t not in fs.templates]
    templates = {fs.templates[t] for t in known}
    if len(templates) > 1:
        return {
            "status": "MIXED_TEMPLATES",
            "detail": "So sánh chỉ hợp lệ giữa các doanh nghiệp cùng chuẩn mực BCTC.",
            "templates": {t: fs.templates[t].value for t in known},
            "unknown_tickers": unknown,
        }
    if not known:
        return {"status": "INSUFFICIENT_DATA", "unknown_tickers": unknown}
    tmpl = templates.pop()
    periods = [latest_analysis_period(fs, t) for t in known]
    ttm = [p for p in periods if p is not None and p.period_type == "TTM"]
    aligned = len(ttm) == len(known)
    common = min(ttm, key=lambda p: (p.fiscal_year, p.fiscal_quarter)) if aligned else None
    ids = list(kpi_ids) if kpi_ids else [s.kpi_id for s in KPI_BY_ID.values() if tmpl in s.templates]
    matrix: dict[str, dict[str, Any]] = {}
    for kid in ids:
        row: dict[str, Any] = {}
        for t, p in zip(known, periods, strict=True):
            use = common or p
            if use is None:
                continue
            row[t] = compute_kpi(fs, t, kid, use).to_dict()
        spec = KPI_BY_ID.get(kid)
        ok = {t: v["value"] for t, v in row.items() if v["status"] == "OK" and v["value"] is not None}
        if spec is not None and spec.better and len(ok) >= 2:
            ranked = sorted(ok, key=lambda t: ok[t], reverse=(spec.better == "higher"))
            for rank, t in enumerate(ranked, start=1):
                row[t]["rank"] = rank
        matrix[kid] = row
    return {
        "status": "OK",
        "template": tmpl.value,
        "period": common.label if common else "per-ticker latest (periods not aligned)",
        "aligned": aligned,
        "tickers": known,
        "unknown_tickers": unknown,
        "kpis": matrix,
    }


@dataclass(frozen=True, slots=True)
class PlanTarget:
    item: str  # canonical item, e.g. revenue_net | pbt | npat
    value: float  # VND
    source: str  # provenance of the target (e.g. AGM table id/page)


@dataclass
class _PlanRow:
    item: str
    target: float
    ytd: float | None
    quarters: list[str] = field(default_factory=list)


def plan_vs_actual(
    fs: FactSet,
    ticker: str,
    fiscal_year: int,
    targets: Sequence[PlanTarget] | Mapping[str, float],
) -> dict[str, Any]:
    tk = ticker.strip().upper()
    if isinstance(targets, Mapping):
        tgt_list = [PlanTarget(k, float(v), "caller") for k, v in targets.items()]
    else:
        tgt_list = list(targets)
    reported = [q for q in (1, 2, 3, 4) if fs.get(tk, "npat", "Q", fiscal_year, q).status is LookupStatus.OK]
    consecutive = 0
    for q in (1, 2, 3, 4):
        if q in reported:
            consecutive = q
        else:
            break
    elapsed = consecutive / 4.0
    rows: list[dict[str, Any]] = []
    for tgt in tgt_list:
        vals = [fs.get(tk, tgt.item, "Q", fiscal_year, q) for q in range(1, consecutive + 1)]
        if not consecutive or any(v.status is not LookupStatus.OK for v in vals):
            rows.append({"item": tgt.item, "target": tgt.value, "target_source": tgt.source,
                         "status": "INSUFFICIENT_DATA", "detail": "missing quarterly actuals"})
            continue
        ytd = sum(v.fact.value for v in vals if v.fact is not None)
        if tgt.value <= 0:
            rows.append({"item": tgt.item, "target": tgt.value, "target_source": tgt.source, "ytd_actual": ytd,
                         "status": "INVALID_TARGET"})
            continue
        achievement = ytd / tgt.value
        if achievement >= elapsed + 0.10:
            pace = "AHEAD"
        elif achievement >= elapsed - 0.05:
            pace = "ON_TRACK"
        else:
            pace = "BEHIND"
        rows.append(
            {
                "item": tgt.item,
                "target": tgt.value,
                "target_source": tgt.source,
                "ytd_actual": ytd,
                "ytd_period": f"{consecutive}Q/{fiscal_year}",
                "achievement": achievement,
                "time_elapsed": elapsed,
                "pace": pace,
                "required_remaining": max(tgt.value - ytd, 0.0),
                "evidence": [v.fact.to_dict() for v in vals if v.fact is not None],
                "status": "OK",
            }
        )
    return {
        "ticker": tk,
        "fiscal_year": fiscal_year,
        "quarters_reported": consecutive,
        "rows": rows,
    }
