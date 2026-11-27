"""Sector KPI packs computed deterministically from `FactSet`.

Every KPI carries its formula, the exact input facts (item, period, value, source key), the
propagated vendor quality flags, and an explicit status. Missing inputs never produce a number.

Period conventions:
- Flow inputs use the requested period (`TTM`, `FY`, or single `Q`).
- Stock inputs use the balance at period end (`Q` balance for TTM/Q, `FY` balance for FY, with
  the Q4 balance as fallback) and averages use (end + same point one year earlier) / 2.
- Bank/insurer/securities KPIs only run for their template (explicit `NOT_APPLICABLE` otherwise).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from sag_api.research.facts import FactSet, LookupStatus, Template

FLAG_AVG_END_ONLY = "AVG_END_ONLY"


class KpiStatus(StrEnum):
    OK = "OK"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class PeriodRef:
    period_type: str  # TTM | FY | Q
    fiscal_year: int
    fiscal_quarter: int = 0

    @property
    def label(self) -> str:
        if self.period_type == "FY":
            return f"FY{self.fiscal_year}"
        if self.period_type == "TTM":
            return f"TTM Q{self.fiscal_quarter}/{self.fiscal_year}"
        return f"Q{self.fiscal_quarter}/{self.fiscal_year}"

    def prior_year(self) -> PeriodRef:
        return PeriodRef(self.period_type, self.fiscal_year - 1, self.fiscal_quarter)

    @property
    def days(self) -> float:
        return 91.25 if self.period_type == "Q" else 365.0


@dataclass(frozen=True, slots=True)
class KpiValue:
    kpi_id: str
    label_vi: str
    ticker: str
    period: str
    value: float | None
    unit: str
    formula: str
    status: KpiStatus
    inputs: tuple[dict[str, Any], ...] = ()
    flags: tuple[str, ...] = ()
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kpi_id": self.kpi_id,
            "label_vi": self.label_vi,
            "ticker": self.ticker,
            "period": self.period,
            "value": self.value,
            "unit": self.unit,
            "formula": self.formula,
            "status": self.status.value,
            "inputs": list(self.inputs),
            "flags": list(self.flags),
            "detail": self.detail,
        }


class _Missing(Exception):
    def __init__(self, what: str) -> None:
        super().__init__(what)
        self.what = what


@dataclass
class _Ctx:
    fs: FactSet
    ticker: str
    period: PeriodRef
    inputs: list[dict[str, Any]] = field(default_factory=list)
    flags: set[str] = field(default_factory=set)

    def _record(self, fact: Any, role: str) -> float:
        self.inputs.append(
            {
                "role": role,
                "item": fact.item,
                "period": fact.period_label,
                "value": fact.value,
                "unit": fact.unit,
                "source_key": fact.source_key,
                "derivation": fact.derivation,
            }
        )
        self.flags.update(fact.flags)
        return float(fact.value)

    def _get(self, item: str, ptype: str, fy: int, fq: int):
        res = self.fs.get(self.ticker, item, ptype, fy, fq)
        return res.fact if res.status is LookupStatus.OK else None

    def flow(self, item: str, period: PeriodRef | None = None) -> float:
        p = period or self.period
        fact = self._get(item, p.period_type, p.fiscal_year, p.fiscal_quarter)
        if fact is None:
            raise _Missing(f"{item}@{p.label}")
        return self._record(fact, "flow")

    def _stock_fact(self, item: str, p: PeriodRef):
        if p.period_type == "FY":
            return self._get(item, "FY", p.fiscal_year, 0) or self._get(item, "Q", p.fiscal_year, 4)
        return self._get(item, "Q", p.fiscal_year, p.fiscal_quarter)

    def stock(self, item: str, period: PeriodRef | None = None) -> float:
        p = period or self.period
        fact = self._stock_fact(item, p)
        if fact is None:
            raise _Missing(f"{item}@end {p.label}")
        return self._record(fact, "stock_end")

    def stock_opt(self, item: str) -> float:
        """Stock at period end, treating a missing line as zero (only for optional components)."""
        fact = self._stock_fact(item, self.period)
        return self._record(fact, "stock_end") if fact is not None else 0.0

    def stock_avg(self, item: str) -> float:
        end = self._stock_fact(item, self.period)
        if end is None:
            raise _Missing(f"{item}@end {self.period.label}")
        begin = self._stock_fact(item, self.period.prior_year())
        v_end = self._record(end, "stock_end")
        if begin is None:
            self.flags.add(FLAG_AVG_END_ONLY)
            return v_end
        return (v_end + self._record(begin, "stock_begin")) / 2.0

    def template(self) -> Template:
        return self.fs.templates[self.ticker]


def _div(num: float, den: float, what: str) -> float:
    if abs(den) < 1e-9:
        raise _Missing(f"zero denominator ({what})")
    return num / den


def _top_line_item(t: Template) -> str:
    return {
        Template.CORPORATE: "revenue_net",
        Template.SECURITIES: "revenue_net",
        Template.BANK: "toi",
        Template.INSURANCE: "gross_written_premium",
    }[t]


def _growth(c: _Ctx, item: str, *, stock: bool = False) -> float:
    if stock:
        cur, prev = c.stock(item), c.stock(item, c.period.prior_year())
    else:
        cur, prev = c.flow(item), c.flow(item, c.period.prior_year())
    if prev <= 0:
        raise _Missing(f"{item} prior value <= 0 (growth undefined)")
    return (cur - prev) / prev


@dataclass(frozen=True, slots=True)
class KpiSpec:
    kpi_id: str
    label_vi: str
    unit: str
    formula: str
    fn: Callable[[_Ctx], float]
    templates: frozenset[Template]
    better: str | None = None  # higher | lower | None
    annual_only: bool = False  # cash-flow KPIs: vendor quarterly CF is sparse/YTD, use audited FY


_ALL_T = frozenset(Template)
_CORP = frozenset({Template.CORPORATE})
_BANK = frozenset({Template.BANK})
_SEC = frozenset({Template.SECURITIES})
_INS = frozenset({Template.INSURANCE})
_NONBANK = frozenset({Template.CORPORATE, Template.SECURITIES, Template.INSURANCE})


def _npat(c: _Ctx) -> float:
    return c.flow("npat")


KPI_SPECS: tuple[KpiSpec, ...] = (
    # ── Common ────────────────────────────────────────────────────────────────
    KpiSpec("top_line_growth_yoy", "Tăng trưởng doanh thu/TOI cùng kỳ", "RATIO",
            "top_line / top_line(prior year) - 1",
            lambda c: _growth(c, _top_line_item(c.template())), _ALL_T, "higher"),
    KpiSpec("npat_growth_yoy", "Tăng trưởng LNST cùng kỳ", "RATIO", "npat / npat(prior year) - 1",
            lambda c: _growth(c, "npat"), _ALL_T, "higher"),
    KpiSpec("roe", "ROE", "RATIO", "npat / avg(equity) (annualized for Q)",
            lambda c: _div(_npat(c), c.stock_avg("equity"), "avg equity")
            * (4.0 if c.period.period_type == "Q" else 1.0),
            _ALL_T, "higher"),
    KpiSpec("roa", "ROA", "RATIO", "npat / avg(total_assets) (annualized for Q)",
            lambda c: _div(_npat(c), c.stock_avg("total_assets"), "avg assets")
            * (4.0 if c.period.period_type == "Q" else 1.0),
            _ALL_T, "higher"),
    KpiSpec("net_margin", "Biên lợi nhuận ròng", "RATIO", "npat / top_line",
            lambda c: _div(_npat(c), c.flow(_top_line_item(c.template())), "top line"), _ALL_T, "higher"),
    KpiSpec("equity_to_assets", "Vốn chủ / Tổng tài sản", "RATIO", "equity / total_assets",
            lambda c: _div(c.stock("equity"), c.stock("total_assets"), "assets"), _ALL_T, "higher"),
    KpiSpec("cfo_to_npat", "Dòng tiền HĐKD / LNST", "X", "cfo / npat",
            lambda c: _div(c.flow("cfo"), _npat(c), "npat"), _NONBANK, "higher", annual_only=True),
    KpiSpec("fcf", "Dòng tiền tự do", "VND", "cfo - capex",
            lambda c: c.flow("cfo") - c.flow("capex"), _CORP, "higher", annual_only=True),
    KpiSpec("dividend_payout", "Tỷ lệ chi trả cổ tức tiền mặt", "RATIO", "dividends_paid / npat",
            lambda c: _div(c.flow("dividends_paid"), _npat(c), "npat"), _ALL_T, None, annual_only=True),
    KpiSpec("accruals_ratio", "Tỷ lệ dồn tích (LNST - CFO) / TS bình quân", "RATIO",
            "(npat - cfo) / avg(total_assets)",
            lambda c: _div(_npat(c) - c.flow("cfo"), c.stock_avg("total_assets"), "avg assets"),
            _NONBANK, "lower", annual_only=True),
    # ── Corporate ─────────────────────────────────────────────────────────────
    KpiSpec("gross_margin", "Biên lợi nhuận gộp", "RATIO", "gross_profit / revenue_net",
            lambda c: _div(c.flow("gross_profit"), c.flow("revenue_net"), "revenue"), _CORP, "higher"),
    KpiSpec("sga_to_revenue", "Chi phí bán hàng + QLDN / Doanh thu", "RATIO",
            "(selling_expense + ga_expense) / revenue_net",
            lambda c: _div(c.flow("selling_expense") + c.flow("ga_expense"), c.flow("revenue_net"), "revenue"),
            _CORP, "lower"),
    KpiSpec("interest_coverage", "Khả năng trả lãi (EBIT / chi phí lãi vay)", "X",
            "(pbt + interest_expense) / interest_expense",
            lambda c: _div(c.flow("pbt") + c.flow("interest_expense"), c.flow("interest_expense"), "interest"),
            _CORP, "higher"),
    KpiSpec("debt_to_equity", "Nợ vay / Vốn chủ", "X", "(st_borrowings + lt_borrowings) / equity",
            lambda c: _div(c.stock("st_borrowings") + c.stock_opt("lt_borrowings"), c.stock("equity"), "equity"),
            _NONBANK, "lower"),
    KpiSpec("net_debt_to_equity", "Nợ vay ròng / Vốn chủ", "X",
            "(st_borrowings + lt_borrowings - cash_and_equivalents - st_investments) / equity",
            lambda c: _div(
                c.stock("st_borrowings") + c.stock_opt("lt_borrowings") - c.stock("cash_and_equivalents")
                - c.stock_opt("st_investments"),
                c.stock("equity"), "equity",
            ), _CORP, "lower"),
    KpiSpec("current_ratio", "Hệ số thanh toán hiện hành", "X", "current_assets / current_liabilities",
            lambda c: _div(c.stock("current_assets"), c.stock("current_liabilities"), "current liabilities"),
            _CORP, "higher"),
    KpiSpec("st_debt_to_liquid_assets", "Vay ngắn hạn / (Tiền + Đầu tư tài chính ngắn hạn)", "X",
            "st_borrowings / (cash_and_equivalents + st_investments)",
            lambda c: _div(c.stock("st_borrowings"), c.stock("cash_and_equivalents") + c.stock_opt("st_investments"),
                           "liquid assets"), _CORP, "lower"),
    KpiSpec("inventory_days", "Số ngày tồn kho", "DAYS", "avg(inventory) / cogs * days",
            lambda c: _div(c.stock_avg("inventory"), c.flow("cogs"), "cogs") * c.period.days, _CORP, "lower"),
    KpiSpec("receivable_days", "Số ngày phải thu ngắn hạn", "DAYS", "avg(st_receivables) / revenue_net * days",
            lambda c: _div(c.stock_avg("st_receivables"), c.flow("revenue_net"), "revenue") * c.period.days,
            _CORP, "lower"),
    KpiSpec("customer_advances_to_revenue", "Người mua trả trước / Doanh thu", "X",
            "advances_from_customers / revenue_net",
            lambda c: _div(c.stock("advances_from_customers"), c.flow("revenue_net"), "revenue"), _CORP, None),
    KpiSpec("receivables_growth_yoy", "Tăng trưởng phải thu ngắn hạn", "RATIO",
            "st_receivables / st_receivables(prior year) - 1",
            lambda c: _growth(c, "st_receivables", stock=True), _NONBANK, None),
    KpiSpec("inventory_growth_yoy", "Tăng trưởng hàng tồn kho", "RATIO", "inventory / inventory(prior year) - 1",
            lambda c: _growth(c, "inventory", stock=True), _CORP, None),
    # ── Bank (TT49) ───────────────────────────────────────────────────────────
    KpiSpec("nii_to_avg_assets", "NII / Tổng tài sản bình quân (proxy NIM)", "RATIO",
            "nii / avg(total_assets) (annualized for Q)",
            lambda c: _div(c.flow("nii"), c.stock_avg("total_assets"), "avg assets")
            * (4.0 if c.period.period_type == "Q" else 1.0),
            _BANK, "higher"),
    KpiSpec("cir", "Chi phí hoạt động / TOI (CIR)", "RATIO", "opex / toi",
            lambda c: _div(c.flow("opex"), c.flow("toi"), "toi"), _BANK, "lower"),
    KpiSpec("non_interest_income_share", "Tỷ trọng thu nhập ngoài lãi", "RATIO", "(toi - nii) / toi",
            lambda c: _div(c.flow("toi") - c.flow("nii"), c.flow("toi"), "toi"), _BANK, "higher"),
    KpiSpec("credit_cost", "Chi phí tín dụng (dự phòng / dư nợ bình quân)", "RATIO",
            "credit_provision_expense / avg(customer_loans_gross) (annualized for Q)",
            lambda c: _div(c.flow("credit_provision_expense"), c.stock_avg("customer_loans_gross"), "avg loans")
            * (4.0 if c.period.period_type == "Q" else 1.0),
            _BANK, "lower"),
    KpiSpec("provision_to_ppop", "Dự phòng / Lợi nhuận trước dự phòng", "RATIO",
            "credit_provision_expense / ppop",
            lambda c: _div(c.flow("credit_provision_expense"), c.flow("ppop"), "ppop"), _BANK, "lower"),
    KpiSpec("loan_to_deposit", "Cho vay KH / Tiền gửi KH (gộp)", "RATIO",
            "customer_loans_gross / customer_deposits",
            lambda c: _div(c.stock("customer_loans_gross"), c.stock("customer_deposits"), "deposits"), _BANK, None),
    KpiSpec("reserve_to_loans", "Dự phòng rủi ro / Dư nợ cho vay", "RATIO",
            "loan_loss_reserve / customer_loans_gross",
            lambda c: _div(c.stock("loan_loss_reserve"), c.stock("customer_loans_gross"), "loans"), _BANK, "higher"),
    KpiSpec("loan_growth_yoy", "Tăng trưởng cho vay KH cùng kỳ", "RATIO",
            "customer_loans_gross / customer_loans_gross(prior year) - 1",
            lambda c: _growth(c, "customer_loans_gross", stock=True), _BANK, None),
    KpiSpec("deposit_growth_yoy", "Tăng trưởng tiền gửi KH cùng kỳ", "RATIO",
            "customer_deposits / customer_deposits(prior year) - 1",
            lambda c: _growth(c, "customer_deposits", stock=True), _BANK, None),
    # ── Securities (TT210/TT334) ──────────────────────────────────────────────
    KpiSpec("margin_to_equity", "Dư nợ cho vay (margin) / Vốn chủ", "X", "margin_loans / equity",
            lambda c: _div(c.stock("margin_loans"), c.stock("equity"), "equity"), _SEC, None),
    KpiSpec("fvtpl_to_equity", "Tài sản FVTPL / Vốn chủ", "X", "fvtpl_assets / equity",
            lambda c: _div(c.stock("fvtpl_assets"), c.stock("equity"), "equity"), _SEC, None),
    KpiSpec("financial_assets_to_equity", "(FVTPL + AFS + HTM) / Vốn chủ", "X",
            "(fvtpl_assets + afs_assets + htm_assets) / equity",
            lambda c: _div(
                c.stock("fvtpl_assets") + c.stock_opt("afs_assets") + c.stock_opt("htm_assets"),
                c.stock("equity"), "equity",
            ), _SEC, None),
    KpiSpec("brokerage_revenue_share", "Tỷ trọng doanh thu môi giới", "RATIO", "brokerage_revenue / revenue_net",
            lambda c: _div(c.flow("brokerage_revenue"), c.flow("revenue_net"), "revenue"), _SEC, None),
    KpiSpec("margin_income_share", "Tỷ trọng lãi cho vay margin", "RATIO", "margin_lending_income / revenue_net",
            lambda c: _div(c.flow("margin_lending_income"), c.flow("revenue_net"), "revenue"), _SEC, None),
    KpiSpec("unrealised_profit_share", "Tỷ trọng lợi nhuận chưa thực hiện / LNTT", "RATIO",
            "unrealised_profit / pbt",
            lambda c: _div(c.flow("unrealised_profit"), c.flow("pbt"), "pbt"), _SEC, "lower"),
    # ── Insurance (TT232) ─────────────────────────────────────────────────────
    KpiSpec("claims_ratio", "Tỷ lệ bồi thường / Phí bảo hiểm thuần", "RATIO", "insurance_claims / net_premium",
            lambda c: _div(c.flow("insurance_claims"), c.flow("net_premium"), "net premium"), _INS, "lower"),
    KpiSpec("financial_profit_share", "Tỷ trọng lợi nhuận hoạt động tài chính / LNTT", "RATIO",
            "financial_activity_profit / pbt",
            lambda c: _div(c.flow("financial_activity_profit"), c.flow("pbt"), "pbt"), _INS, None),
)

KPI_BY_ID: dict[str, KpiSpec] = {k.kpi_id: k for k in KPI_SPECS}


def latest_analysis_period(fs: FactSet, ticker: str) -> PeriodRef | None:
    """Most recent period with a TTM net profit; falls back to the latest FY."""
    tk = ticker.strip().upper()
    ttm = [f for f in fs.series(tk, "npat", "TTM")]
    if ttm:
        last = ttm[-1]
        return PeriodRef("TTM", last.fiscal_year, last.fiscal_quarter)
    fy = fs.series(tk, "npat", "FY")
    if fy:
        return PeriodRef("FY", fy[-1].fiscal_year, 0)
    return None


def _annual_period_for(fs: FactSet, ticker: str, period: PeriodRef) -> PeriodRef | None:
    """Latest audited FY at or before `period` that has a reported FY cash flow statement."""
    if period.period_type == "FY":
        return period
    last_full = period.fiscal_year if period.fiscal_quarter == 4 else period.fiscal_year - 1
    for fy in (last_full, last_full - 1):
        if fs.get(ticker, "cfo", "FY", fy, 0).status is LookupStatus.OK:
            return PeriodRef("FY", fy, 0)
    return None


def compute_kpi(fs: FactSet, ticker: str, kpi_id: str, period: PeriodRef) -> KpiValue:
    tk = ticker.strip().upper()
    spec = KPI_BY_ID.get(kpi_id)
    if spec is None:
        return KpiValue(kpi_id, kpi_id, tk, period.label, None, "", "", KpiStatus.NOT_APPLICABLE,
                        detail=f"không nhận diện chỉ số '{kpi_id}'")
    tmpl = fs.templates.get(tk)
    if tmpl is None:
        return KpiValue(kpi_id, spec.label_vi, tk, period.label, None, spec.unit, spec.formula,
                        KpiStatus.INSUFFICIENT_DATA, detail=f"{tk} chưa có báo cáo tài chính trong kỳ PIT")
    if tmpl not in spec.templates:
        return KpiValue(kpi_id, spec.label_vi, tk, period.label, None, spec.unit, spec.formula,
                        KpiStatus.NOT_APPLICABLE, detail=f"không áp dụng cho mẫu BCTC {tmpl.value}")
    detail = ""
    if spec.annual_only and period.period_type != "FY":
        annual = _annual_period_for(fs, tk, period)
        if annual is None:
            return KpiValue(kpi_id, spec.label_vi, tk, period.label, None, spec.unit, spec.formula,
                            KpiStatus.INSUFFICIENT_DATA, detail="chưa có báo cáo lưu chuyển tiền tệ năm kiểm toán")
        detail = f"đánh giá trên báo cáo năm {annual.label} do số dòng tiền quý từ nhà cung cấp thưa/lũy kế"
        period = annual
    ctx = _Ctx(fs, tk, period)
    try:
        value = spec.fn(ctx)
    except _Missing as miss:
        return KpiValue(kpi_id, spec.label_vi, tk, period.label, None, spec.unit, spec.formula,
                        KpiStatus.INSUFFICIENT_DATA, tuple(ctx.inputs), tuple(sorted(ctx.flags)),
                        detail=f"thiếu chỉ tiêu {miss.what}")
    return KpiValue(kpi_id, spec.label_vi, tk, period.label, value, spec.unit, spec.formula, KpiStatus.OK,
                    tuple(ctx.inputs), tuple(sorted(ctx.flags)), detail=detail)


def compute_kpi_pack(
    fs: FactSet,
    ticker: str,
    period: PeriodRef | None = None,
    *,
    kpi_ids: Sequence[str] | None = None,
) -> list[KpiValue]:
    tk = ticker.strip().upper()
    p = period or latest_analysis_period(fs, tk)
    if p is None:
        return []
    tmpl = fs.templates.get(tk)
    specs = [KPI_BY_ID[k] for k in kpi_ids if k in KPI_BY_ID] if kpi_ids else [
        s for s in KPI_SPECS if tmpl in s.templates
    ]
    return [compute_kpi(fs, tk, s.kpi_id, p) for s in specs]


def vendor_snapshot(fs: FactSet, ticker: str) -> list[dict[str, Any]]:
    """Latest vendor ratio/valuation snapshot (`vr_*` items), labeled as vendor-computed."""
    tk = ticker.strip().upper()
    latest = fs.latest_period(tk, "Q")
    out: list[dict[str, Any]] = []
    for f in fs.facts_for(tk):
        if not f.item.startswith("vr_") or f.period_type != "Q" or (f.fiscal_year, f.fiscal_quarter) != latest:
            continue
        d = f.to_dict()
        d["provenance"] = "VENDOR_RATIO_ALPHASTOCK"
        out.append(d)
    return sorted(out, key=lambda d: d["item"])
