"""Pure, deterministic fact builder over Core PG `financial_statements` rows.

Guarantees (each is covered by tests):
- Exact-key extraction via `catalog.CATALOG` (no substring matching).
- One vendor row per (ticker, period_end, statement, frequency): `alphastock` preferred,
  `vnstock_vci` fallback; `cafef` code-keyed rows are ignored and counted.
- Calendar period typing: `FY` (fiscal_quarter=0) and single-quarter `Q` (1..4);
  `TTM` facts derived only from 4 consecutive quarters of a FLOW item.
- Quality flags instead of silent fixes: accounting-identity failures, FY vs sum-of-quarters
  mismatch, derived values, non-calendar fiscal year.
- Explicit lookup statuses (`OK`, `NOT_FOUND`, `NOT_APPLICABLE`, `UNKNOWN_TICKER`).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Protocol

from sag_api.research.facts.catalog import (
    CATALOG_BY_ID,
    CanonicalItem,
    ItemKind,
    Template,
    canonical_sign,
    items_for_template,
    resolve_template,
)

SOURCE_PRIORITY: dict[str, int] = {"alphastock": 0, "vnstock_vci": 1}
STATEMENT_TYPES: tuple[str, ...] = ("IS", "BS", "CF", "RATIOS")
IDENTITY_TOLERANCE = 0.005
FY_QSUM_TOLERANCE = 0.02

FLAG_DERIVED = "DERIVED"
FLAG_IDENTITY_FAIL = "IDENTITY_FAIL"
FLAG_FY_QSUM_MISMATCH = "FY_QSUM_MISMATCH"
FLAG_NON_CALENDAR_FY = "NON_CALENDAR_FY"
FLAG_QUARTERLY_CF_UNRELIABLE = "QUARTERLY_CF_UNRELIABLE"  # vendor quarterly CF: sparse and often YTD


class LookupStatus(StrEnum):
    OK = "OK"
    NOT_FOUND = "NOT_FOUND"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN_TICKER = "UNKNOWN_TICKER"
    UNKNOWN_ITEM = "UNKNOWN_ITEM"


@dataclass(frozen=True, slots=True)
class FinancialFact:
    ticker: str
    item: str
    template: str
    statement: str
    kind: str
    period_type: str  # FY | Q | TTM
    fiscal_year: int
    fiscal_quarter: int  # 0 for FY
    period_end: date
    value: float
    unit: str
    source: str
    source_key: str | None
    effective_published_date: date | None
    derivation: str | None = None
    flags: tuple[str, ...] = ()

    @property
    def period_label(self) -> str:
        if self.period_type == "FY":
            return f"FY{self.fiscal_year}"
        if self.period_type == "TTM":
            return f"TTM Q{self.fiscal_quarter}/{self.fiscal_year}"
        return f"Q{self.fiscal_quarter}/{self.fiscal_year}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "item": self.item,
            "template": self.template,
            "period": self.period_label,
            "period_type": self.period_type,
            "fiscal_year": self.fiscal_year,
            "fiscal_quarter": self.fiscal_quarter,
            "period_end": self.period_end.isoformat(),
            "value": self.value,
            "unit": self.unit,
            "source": self.source,
            "source_key": self.source_key,
            "effective_published_date": (
                self.effective_published_date.isoformat() if self.effective_published_date else None
            ),
            "derivation": self.derivation,
            "flags": list(self.flags),
        }


@dataclass(frozen=True, slots=True)
class FactLookup:
    status: LookupStatus
    fact: FinancialFact | None = None
    detail: str = ""


_FactKey = tuple[str, str, str, int, int]  # ticker, item, period_type, fiscal_year, fiscal_quarter


@dataclass
class FactSet:
    as_of_date: date
    templates: dict[str, Template]
    facts: dict[_FactKey, FinancialFact] = field(default_factory=dict)
    ignored_rows: dict[str, int] = field(default_factory=dict)
    identity_checks: list[dict[str, Any]] = field(default_factory=list)
    _indexed_len: int = field(default=-1, init=False, repr=False, compare=False)
    _series_idx: dict[tuple[str, str, str], list[FinancialFact]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _latest_period_idx: dict[tuple[str, str], tuple[int, int]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )
    _facts_by_tk: dict[str, list[FinancialFact]] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        self._rebuild_indexes()

    def _rebuild_indexes(self) -> None:
        series_idx: dict[tuple[str, str, str], list[FinancialFact]] = {}
        latest_idx: dict[tuple[str, str], tuple[int, int]] = {}
        by_tk: dict[str, list[FinancialFact]] = {}
        for (tk, item, ptype, fy, fq), fact in self.facts.items():
            series_idx.setdefault((tk, item, ptype), []).append(fact)
            by_tk.setdefault(tk, []).append(fact)
            lp_key = (tk, ptype)
            cur = (fy, fq)
            prev = latest_idx.get(lp_key)
            if prev is None or cur > prev:
                latest_idx[lp_key] = cur
        for lst in series_idx.values():
            lst.sort(key=lambda f: (f.fiscal_year, f.fiscal_quarter))
        self._series_idx = series_idx
        self._latest_period_idx = latest_idx
        self._facts_by_tk = by_tk
        self._indexed_len = len(self.facts)

    def _ensure_indexes(self) -> None:
        if self._indexed_len != len(self.facts):
            self._rebuild_indexes()

    def get(
        self,
        ticker: str,
        item: str,
        period_type: str,
        fiscal_year: int,
        fiscal_quarter: int = 0,
    ) -> FactLookup:
        tk = ticker.strip().upper()
        if tk not in self.templates:
            return FactLookup(LookupStatus.UNKNOWN_TICKER, detail=f"{tk} has no statements as of {self.as_of_date}")
        spec = CATALOG_BY_ID.get(item)
        if spec is None:
            return FactLookup(LookupStatus.UNKNOWN_ITEM, detail=f"'{item}' is not a canonical item")
        if self.templates[tk] not in spec.templates:
            return FactLookup(
                LookupStatus.NOT_APPLICABLE,
                detail=f"'{item}' does not apply to {self.templates[tk].value} issuers",
            )
        fact = self.facts.get((tk, item, period_type, fiscal_year, fiscal_quarter))
        if fact is None:
            return FactLookup(
                LookupStatus.NOT_FOUND,
                detail=f"{tk} {item} {period_type} {fiscal_year}Q{fiscal_quarter} not reported as of {self.as_of_date}",
            )
        return FactLookup(LookupStatus.OK, fact=fact)

    def series(self, ticker: str, item: str, period_type: str) -> list[FinancialFact]:
        self._ensure_indexes()
        tk = ticker.strip().upper()
        return list(self._series_idx.get((tk, item, period_type), ()))

    def latest_period(self, ticker: str, period_type: str) -> tuple[int, int] | None:
        self._ensure_indexes()
        tk = ticker.strip().upper()
        return self._latest_period_idx.get((tk, period_type))

    def facts_for(self, ticker: str) -> list[FinancialFact]:
        self._ensure_indexes()
        tk = ticker.strip().upper()
        return list(self._facts_by_tk.get(tk, ()))


class StatementReader(Protocol):
    async def fetch_issuer_profiles(self, tickers: Sequence[str] | None = None) -> list[dict[str, Any]]: ...

    async def fetch_pit_financial_statements(
        self,
        as_of_date: date,
        *,
        tickers: Sequence[str] | None = None,
        lookback_years: int = 5,
        prefer_annual: bool = True,
        statement_types: Sequence[str] = ("BS", "IS", "CF"),
    ) -> list[dict[str, Any]]: ...


# ── Row normalization ────────────────────────────────────────────────────────


def _coerce_date(val: Any) -> date | None:
    if isinstance(val, date):
        return val
    if val is None:
        return None
    try:
        return date.fromisoformat(str(val)[:10])
    except ValueError:
        return None


def _parse_data(raw: Any) -> Mapping[str, Any]:
    if isinstance(raw, Mapping):
        return raw
    if isinstance(raw, str | bytes):
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return parsed if isinstance(parsed, Mapping) else {}
    return {}


def _finite(val: Any) -> float | None:
    if isinstance(val, bool) or val is None:
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def effective_publication_date(period_end: date, published_date: Any, frequency: str) -> date:
    """PIT availability date for a vendor statement row.

    Vendor `published_date` is often the fetch date (years after the period); accept it only
    inside a realistic 10..180-day disclosure window, otherwise apply the statutory lag
    (90 days for annual, 45 days for quarterly). Mirrors the Core PG reader SQL.
    """
    pub = _coerce_date(published_date)
    if pub is not None and period_end + timedelta(days=10) <= pub <= period_end + timedelta(days=180):
        return pub
    return period_end + timedelta(days=90 if frequency == "yearly" else 45)


def _select_rows(rows: Iterable[Mapping[str, Any]]) -> tuple[list[Mapping[str, Any]], dict[str, int]]:
    """Keep one row per (symbol, period_end, statement, frequency) by source priority."""
    best: dict[tuple[str, date, str, str], tuple[int, Mapping[str, Any]]] = {}
    ignored: dict[str, int] = defaultdict(int)
    for row in rows:
        src = str(row.get("source") or "unknown").strip().lower()
        prio = SOURCE_PRIORITY.get(src)
        if prio is None:
            ignored[src] += 1
            continue
        sym = str(row.get("symbol") or "").strip().upper()
        st = str(row.get("statement_type") or "").strip().upper()
        freq = str(row.get("frequency") or "").strip().lower()
        pe = _coerce_date(row.get("period_end"))
        if not sym or st not in STATEMENT_TYPES or freq not in {"yearly", "quarterly"} or pe is None:
            ignored["malformed"] += 1
            continue
        key = (sym, pe, st, freq)
        cur = best.get(key)
        if cur is None or prio < cur[0]:
            if cur is not None:
                ignored[f"superseded_{src}"] += 1
            best[key] = (prio, row)
        else:
            ignored[f"superseded_{src}"] += 1
    return [r for _, r in best.values()], dict(ignored)


def _extract_value(spec: CanonicalItem, template: Template, data: Mapping[str, Any]) -> tuple[float, str] | None:
    zero_hit: str | None = None
    for key in spec.keys.get(template, ()):
        val = _finite(data.get(key))
        if val is None:
            continue
        if val == 0.0:
            if zero_hit is None:
                zero_hit = key
            continue
        return val * canonical_sign(spec.sign, key), key
    if zero_hit is not None and not spec.zero_is_missing:
        return 0.0, zero_hit
    return None


def _period_of(pe: date, freq: str) -> tuple[str, int, int, tuple[str, ...]]:
    if freq == "yearly":
        flags = () if pe.month == 12 else (FLAG_NON_CALENDAR_FY,)
        return "FY", pe.year, 0, flags
    return "Q", pe.year, (pe.month - 1) // 3 + 1, ()


def _unit_for(kind: ItemKind) -> str:
    return "VND_PER_SHARE" if kind is ItemKind.PER_SHARE else "VND"


# ── Derivations & checks ─────────────────────────────────────────────────────


def _add_flag(fact: FinancialFact, flag: str) -> FinancialFact:
    return fact if flag in fact.flags else replace(fact, flags=(*fact.flags, flag))


def _derive_missing(facts: dict[_FactKey, FinancialFact], templates: Mapping[str, Template]) -> None:
    base_by_period: dict[tuple[str, str, int, int], FinancialFact] = {}
    for (t, _, p, fy, fq), f in facts.items():
        base_by_period.setdefault((t, p, fy, fq), f)
    for (tk, ptype, fy, fq), base in base_by_period.items():
        tmpl = templates.get(tk)

        def val(item: str, *, _tk: str = tk, _p: str = ptype, _fy: int = fy, _fq: int = fq) -> float | None:
            f = facts.get((_tk, item, _p, _fy, _fq))
            return f.value if f is not None else None

        def put(item: str, value: float, derivation: str, *, _base: FinancialFact = base) -> None:
            spec = CATALOG_BY_ID[item]
            facts[(_base.ticker, item, _base.period_type, _base.fiscal_year, _base.fiscal_quarter)] = FinancialFact(
                ticker=_base.ticker,
                item=item,
                template=_base.template,
                statement=spec.statement,
                kind=spec.kind.value,
                period_type=_base.period_type,
                fiscal_year=_base.fiscal_year,
                fiscal_quarter=_base.fiscal_quarter,
                period_end=_base.period_end,
                value=value,
                unit=spec.unit or _unit_for(spec.kind),
                source=_base.source,
                source_key=None,
                effective_published_date=_base.effective_published_date,
                derivation=derivation,
                flags=(FLAG_DERIVED,),
            )

        if val("total_liabilities") is None and val("total_assets") is not None and val("equity") is not None:
            put("total_liabilities", val("total_assets") - val("equity"), "total_assets - equity")  # type: ignore[operator]
        if val("cash_and_equivalents") is None and val("cash_only") is not None and tmpl is not Template.BANK:
            put(
                "cash_and_equivalents",
                val("cash_only") + (val("cash_equivalents_only") or 0.0),  # type: ignore[operator]
                "cash_only + cash_equivalents_only",
            )
        if val("income_tax") is None and val("pbt") is not None and val("npat") is not None:
            put("income_tax", val("pbt") - val("npat"), "pbt - npat")  # type: ignore[operator]
        if tmpl is Template.CORPORATE:
            if val("gross_profit") is None and val("revenue_net") is not None and val("cogs") is not None:
                put("gross_profit", val("revenue_net") - val("cogs"), "revenue_net - cogs")  # type: ignore[operator]
        if tmpl is Template.BANK:
            comps = (
                "nii",
                "net_fee_income",
                "fx_gold_income",
                "trading_securities_income",
                "investment_securities_income",
                "other_income_net",
                "equity_investment_income",
            )
            comp_vals = [val(c) for c in comps]
            if val("toi") is None and val("nii") is not None:
                put("toi", sum(v for v in comp_vals if v is not None), "sum(7 TT49 operating income lines)")
            if val("opex") is None and val("toi") is not None and val("ppop") is not None:
                put("opex", val("toi") - val("ppop"), "toi - ppop")  # type: ignore[operator]


def _run_identity_checks(
    facts: dict[_FactKey, FinancialFact],
    templates: Mapping[str, Template],
) -> list[dict[str, Any]]:
    """Accounting identities; failing facts are flagged, never altered."""
    rules: list[tuple[str, str, tuple[tuple[str, float], ...], frozenset[Template] | None]] = [
        ("BS: total_assets = total_liabilities + equity", "total_assets",
         (("total_liabilities", 1.0), ("equity", 1.0)), None),
        ("IS: npat = pbt - income_tax", "npat", (("pbt", 1.0), ("income_tax", -1.0)), None),
        ("IS: gross_profit = revenue_net - cogs", "gross_profit",
         (("revenue_net", 1.0), ("cogs", -1.0)), frozenset({Template.CORPORATE})),
        ("IS: nii = interest_income - interest_expense", "nii",
         (("interest_income", 1.0), ("interest_expense", -1.0)), frozenset({Template.BANK})),
    ]
    results: list[dict[str, Any]] = []
    periods = {(t, p, fy, fq) for (t, _, p, fy, fq) in facts if p in ("FY", "Q")}
    for tk, ptype, fy, fq in sorted(periods):
        tmpl = templates.get(tk)
        for name, lhs_item, rhs_terms, only in rules:
            if only is not None and tmpl not in only:
                continue
            lhs = facts.get((tk, lhs_item, ptype, fy, fq))
            rhs_facts = [facts.get((tk, it, ptype, fy, fq)) for it, _ in rhs_terms]
            if lhs is None or any(f is None for f in rhs_facts):
                continue
            involved = [lhs, *rhs_facts]
            if sum(1 for f in involved if f is not None and FLAG_DERIVED in f.flags) > 0:
                continue  # an identity proven by construction is not evidence
            rhs = sum(coef * f.value for (_, coef), f in zip(rhs_terms, rhs_facts, strict=True) if f is not None)
            denom = max(abs(lhs.value), abs(rhs), 1.0)
            err = abs(lhs.value - rhs) / denom
            passed = err <= IDENTITY_TOLERANCE
            results.append(
                {
                    "ticker": tk,
                    "period": f"{ptype}{fy}Q{fq}",
                    "identity": name,
                    "lhs": lhs.value,
                    "rhs": rhs,
                    "error_ratio": round(err, 6),
                    "passed": passed,
                }
            )
            if not passed:
                for f in involved:
                    if f is not None:
                        k = (f.ticker, f.item, f.period_type, f.fiscal_year, f.fiscal_quarter)
                        facts[k] = _add_flag(facts[k], FLAG_IDENTITY_FAIL)
    return results


def _reconcile_fy_with_quarters(facts: dict[_FactKey, FinancialFact]) -> None:
    for (tk, item, ptype, fy, _fq), fy_fact in list(facts.items()):
        if ptype != "FY" or fy_fact.kind != ItemKind.FLOW.value or fy_fact.statement == "CF":
            continue
        qs = [facts.get((tk, item, "Q", fy, q)) for q in (1, 2, 3, 4)]
        if any(q is None for q in qs):
            continue
        qsum = sum(q.value for q in qs if q is not None)
        denom = max(abs(fy_fact.value), abs(qsum), 1.0)
        if abs(fy_fact.value - qsum) / denom > FY_QSUM_TOLERANCE:
            facts[(tk, item, ptype, fy, 0)] = _add_flag(fy_fact, FLAG_FY_QSUM_MISMATCH)


def _build_ttm(facts: dict[_FactKey, FinancialFact]) -> None:
    quarterly: dict[tuple[str, str], dict[tuple[int, int], FinancialFact]] = defaultdict(dict)
    for (tk, item, ptype, fy, fq), f in facts.items():
        # Vendor quarterly cash flows are sparse and often year-to-date cumulative: never sum them.
        if ptype == "Q" and f.kind == ItemKind.FLOW.value and f.statement != "CF":
            quarterly[(tk, item)][(fy, fq)] = f
    for (tk, item), by_q in quarterly.items():
        for fy, fq in by_q:
            window: list[FinancialFact] = []
            y, q = fy, fq
            for _ in range(4):
                f = by_q.get((y, q))
                if f is None:
                    break
                window.append(f)
                y, q = (y - 1, 4) if q == 1 else (y, q - 1)
            if len(window) != 4:
                continue
            head = window[0]
            flags = tuple(sorted({fl for w in window for fl in w.flags}))
            pubs = [w.effective_published_date for w in window if w.effective_published_date is not None]
            facts[(tk, item, "TTM", fy, fq)] = replace(
                head,
                period_type="TTM",
                value=sum(w.value for w in window),
                source_key=None,
                effective_published_date=max(pubs) if pubs else None,
                derivation="sum of 4 consecutive quarters",
                flags=flags,
            )


# ── Public API ───────────────────────────────────────────────────────────────


def build_fact_set(
    rows: Iterable[Mapping[str, Any]],
    *,
    templates_by_ticker: Mapping[str, Template],
    as_of_date: date,
) -> FactSet:
    """Normalize PIT-filtered Core PG statement rows into a `FactSet`.

    `rows` must already be PIT-filtered by the reader (`effective_published_date <= as_of_date`);
    rows whose `effective_published_date` is later than `as_of_date` are dropped defensively.
    """
    selected, ignored = _select_rows(rows)
    templates: dict[str, Template] = {}
    facts: dict[_FactKey, FinancialFact] = {}
    future_dropped = 0

    for row in selected:
        sym = str(row["symbol"]).strip().upper()
        st = str(row["statement_type"]).strip().upper()
        freq = str(row["frequency"]).strip().lower()
        pe = _coerce_date(row["period_end"])
        assert pe is not None
        pub = _coerce_date(row.get("effective_published_date")) or effective_publication_date(
            pe, row.get("published_date"), freq
        )
        if pub > as_of_date:
            future_dropped += 1
            continue
        tmpl = templates_by_ticker.get(sym, Template.CORPORATE)
        templates[sym] = tmpl
        ptype, fy, fq, pflags = _period_of(pe, freq)
        if ptype == "Q" and st == "CF":
            pflags = (*pflags, FLAG_QUARTERLY_CF_UNRELIABLE)
        data = _parse_data(row.get("data"))
        src = str(row.get("source") or "unknown").strip().lower()
        for spec in items_for_template(tmpl):
            if spec.statement != st:
                continue
            hit = _extract_value(spec, tmpl, data)
            if hit is None:
                continue
            value, key = hit
            facts[(sym, spec.item_id, ptype, fy, fq)] = FinancialFact(
                ticker=sym,
                item=spec.item_id,
                template=tmpl.value,
                statement=st,
                kind=spec.kind.value,
                period_type=ptype,
                fiscal_year=fy,
                fiscal_quarter=fq,
                period_end=pe,
                value=value,
                unit=spec.unit or _unit_for(spec.kind),
                source=src,
                source_key=key,
                effective_published_date=pub,
                flags=pflags,
            )

    if future_dropped:
        ignored["future_published"] = future_dropped
    _derive_missing(facts, templates)
    checks = _run_identity_checks(facts, templates)
    _reconcile_fy_with_quarters(facts)
    _build_ttm(facts)
    return FactSet(
        as_of_date=as_of_date,
        templates=templates,
        facts=facts,
        ignored_rows=ignored,
        identity_checks=checks,
    )


async def load_fact_set(
    reader: StatementReader,
    *,
    tickers: Sequence[str],
    as_of_date: date,
    lookback_years: int = 6,
) -> FactSet:
    """Load PIT statements + issuer industries from Core PG (read-only) and build facts."""
    norm = sorted({t.strip().upper() for t in tickers if t and t.strip()})
    profiles = await reader.fetch_issuer_profiles(norm)
    templates = {
        str(p.get("ticker") or "").strip().upper(): resolve_template(p.get("industry"))
        for p in profiles
        if p.get("ticker")
    }
    rows = await reader.fetch_pit_financial_statements(
        as_of_date,
        tickers=norm,
        lookback_years=lookback_years,
        prefer_annual=False,
        statement_types=STATEMENT_TYPES,
    )
    return build_fact_set(rows, templates_by_ticker=templates, as_of_date=as_of_date)
