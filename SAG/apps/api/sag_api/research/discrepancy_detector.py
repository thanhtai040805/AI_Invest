"""Deterministic Quant-vs-Narrative Discrepancy Detector (0 LLM).

Compares narrative claims (e.g. BCTN CEO/Chair letters, MD&A S01/S02/S03) against
`VERIFIED` audited financial tables (S06/S07/S08/S10) and Core DB metrics without any LLM calls.
Emits structured `DiscrepancyAlert` payloads matching `PROJECT.md` Contract 4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from sag_api.research.input_inspector import normalize_ws
from sag_api.research.table_engine import detect_unit_and_currency, parse_vn_number

Severity = Literal["HIGH", "MEDIUM"]


@dataclass(frozen=True, slots=True)
class NarrativeClaim:
    metric_key: str
    metric_label: str
    claimed_value: float | None = None
    unit_multiplier: float = 1.0
    is_percentage: bool = False
    stance: Literal["POSITIVE", "NEGATIVE", "NEUTRAL"] = "NEUTRAL"
    claim_text: str = ""
    citation: str = ""


@dataclass(frozen=True, slots=True)
class AuditedBaseline:
    metric_key: str
    metric_label: str
    value: float
    unit_multiplier: float = 1.0
    is_percentage: bool = False
    citation: str = ""
    formatted_value: str = ""


@dataclass(frozen=True, slots=True)
class FinancialTrendSnapshot:
    """Optional quantitative context from Core DB / audited tables for trend & quality checks."""

    fiscal_year: int
    gpm_yoy_bps: float | None = None
    rev_yoy_pct: float | None = None
    npat_yoy_pct: float | None = None
    cfo_value: float | None = None
    npat_value: float | None = None
    plan_target_pbt: float | None = None
    actual_pbt: float | None = None
    operating_lease_yoy_pct: float | None = None
    citations: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class DiscrepancyAlert:
    alert_id: str
    severity: Severity
    metric: str
    narrative_claim: str
    audited_value: str
    delta_pct: float
    citations: list[str]
    alert_type: str = "QUANT_VS_NARRATIVE"

    def to_dict(self) -> dict[str, Any]:
        return {
            "alert_id": self.alert_id,
            "severity": self.severity,
            "metric": self.metric,
            "narrative_claim": self.narrative_claim,
            "audited_value": self.audited_value,
            "delta_pct": self.delta_pct,
            "citations": list(self.citations),
        }


_METRIC_ALIASES: dict[str, tuple[str, tuple[str, ...], bool]] = {
    "net_revenue": (
        "Doanh thu thuần",
        ("doanh thu thuần", "doanh thu bán hàng", "tổng doanh thu", "net revenue"),
        False,
    ),
    "gross_margin": (
        "Biên lợi nhuận gộp",
        ("biên lợi nhuận gộp", "biên gộp", "tỷ suất lợi nhuận gộp", "gross margin"),
        True,
    ),
    "npat": (
        "Lợi nhuận sau thuế",
        ("lợi nhuận sau thuế", "lnst", "lãi ròng", "net profit after tax"),
        False,
    ),
    "pbt": (
        "Lợi nhuận trước thuế",
        ("lợi nhuận trước thuế", "lntt", "tổng lợi nhuận kế toán trước thuế", "profit before tax"),
        False,
    ),
    "total_assets": (
        "Tổng tài sản",
        ("tổng cộng tài sản", "tổng tài sản", "total assets"),
        False,
    ),
    "capex": (
        "Chi đầu tư CAPEX",
        ("chi đầu tư", "capex", "ngân sách đầu tư", "tổng mức đầu tư"),
        False,
    ),
    "total_debt": (
        "Tổng nợ vay",
        ("tổng nợ vay", "vay và nợ thuê tài chính", "dư nợ vay"),
        False,
    ),
}

_POSITIVE_MARGIN_RE = re.compile(
    r"(?:biên\s+(?:lợi\s+nhuận(?:\s+gộp)?|gộp)\s+(?:cải\s+thiện|tăng(?:\s+trưởng)?|khởi\s+sắc|tích\s+cực)|"
    r"hiệu\s+quả\s+sinh\s+lời\s+(?:tăng|cải\s+thiện|vượt\s+trội))",
    re.IGNORECASE,
)
_POSITIVE_GROWTH_RE = re.compile(
    r"(?:tăng\s+trưởng\s+(?:mạnh|bứt\s+phá|vượt\s+bậc|vượt\s+trội|ấn\s+tượng|tích\s+cực)|"
    r"kết\s+quả\s+(?:kinh\s+doanh\s+)?(?:khả\s+quan|vượt\s+trội|tăng\s+trưởng\s+tốt)|"
    r"lợi\s+nhuận\s+(?:tăng\s+mạnh|bứt\s+phá|lập\s+kỷ\s+lục))",
    re.IGNORECASE,
)
_PAGE_TAG_RE = re.compile(r"<!--\s*PAGE\s+(\d+)\s*-->", re.IGNORECASE)
_CLAIM_NUM_RE = re.compile(
    r"(?:đạt|lên|ở\s+mức|khoảng|gần|trên|là|:)?\s*"
    r"(\(?-?(?:\d{1,3}(?:[.,]\d{3})+|\d+)(?:[.,]\d{1,2})?\)?)"
    r"\s*(%|tỷ\s*(?:đồng|vnđ|vnd|usd)|tỉ\s*(?:đồng|vnđ|vnd)|triệu\s*(?:đồng|vnđ|vnd|usd)|nghìn\s*(?:đồng|vnđ|vnd))?",
    re.IGNORECASE,
)


def _canonical_metric_for_label(label: str) -> tuple[str, str, bool] | None:
    low = normalize_ws(label).lower()
    for m_key, (display_name, aliases, is_pct) in _METRIC_ALIASES.items():
        if any(alias in low for alias in aliases):
            return m_key, display_name, is_pct
    return None


def _split_segments_with_pages(narrative_text: str, default_slot: str) -> list[tuple[str, str]]:
    """Split narrative text into (citation_tag, sentence) pairs respecting `<!-- PAGE N -->` anchors."""
    cur_page = 1
    out: list[tuple[str, str]] = []
    for raw_line in (narrative_text or "").splitlines():
        m_pg = _PAGE_TAG_RE.search(raw_line)
        if m_pg:
            cur_page = int(m_pg.group(1))
            continue
        clean = normalize_ws(raw_line)
        if not clean:
            continue
        for sent in re.split(r"(?<=[.!?;])\s+", clean):
            s = sent.strip()
            if s:
                out.append((f"{default_slot}·P{cur_page}", s))
    return out


def extract_narrative_claims(
    narrative_text: str,
    *,
    sub_doc_slot: str = "S01",
) -> list[NarrativeClaim]:
    """Deterministically extract quantitative and directional claims from narrative text (0 LLM)."""
    claims: list[NarrativeClaim] = []
    for cit, sent in _split_segments_with_pages(narrative_text, sub_doc_slot):
        low = sent.lower()
        matched_metric = _canonical_metric_for_label(low)
        if matched_metric is not None:
            m_key, m_label, is_pct = matched_metric
            # Look for number right after the matched alias
            aliases = _METRIC_ALIASES[m_key][1]
            alias_end = max(
                (low.find(a) + len(a) for a in aliases if a in low),
                default=0,
            )
            tail = sent[alias_end:]
            m_num = _CLAIM_NUM_RE.search(tail)
            if m_num and m_num.group(1):
                num_str = m_num.group(1)
                unit_str = (m_num.group(2) or "").strip()
                parsed_val = parse_vn_number(num_str, locale="VN")
                if parsed_val is not None:
                    if unit_str == "%" or is_pct:
                        claims.append(
                            NarrativeClaim(
                                metric_key=m_key,
                                metric_label=m_label,
                                claimed_value=parsed_val,
                                unit_multiplier=1.0,
                                is_percentage=True,
                                stance="POSITIVE" if _POSITIVE_MARGIN_RE.search(low) else "NEUTRAL",
                                claim_text=sent,
                                citation=cit,
                            )
                        )
                        continue
                    _, mult, _ = detect_unit_and_currency(unit_str or sent)
                    claims.append(
                        NarrativeClaim(
                            metric_key=m_key,
                            metric_label=m_label,
                            claimed_value=parsed_val,
                            unit_multiplier=mult,
                            is_percentage=False,
                            stance="POSITIVE" if _POSITIVE_GROWTH_RE.search(low) else "NEUTRAL",
                            claim_text=sent,
                            citation=cit,
                        )
                    )
                    continue

        if _POSITIVE_MARGIN_RE.search(low):
            claims.append(
                NarrativeClaim(
                    metric_key="gross_margin",
                    metric_label="Biên lợi nhuận gộp",
                    stance="POSITIVE",
                    is_percentage=True,
                    claim_text=sent,
                    citation=cit,
                )
            )
        elif _POSITIVE_GROWTH_RE.search(low):
            claims.append(
                NarrativeClaim(
                    metric_key="profit_growth",
                    metric_label="Tăng trưởng KQKD",
                    stance="POSITIVE",
                    claim_text=sent,
                    citation=cit,
                )
            )
    return claims


def extract_audited_baselines_from_tables(
    hydrated_tables: list[dict[str, Any]],
    *,
    target_col_index: int = 0,
) -> dict[str, AuditedBaseline]:
    """Extract audited numeric baselines ONLY from `VERIFIED` tables (ignoring `TABLE_UNVERIFIED`)."""
    baselines: dict[str, AuditedBaseline] = {}
    for tbl in hydrated_tables:
        if tbl.get("verification_status") != "VERIFIED":
            continue
        slot = str(tbl.get("sub_doc_slot") or "S06")[:3]
        page_from = int(tbl.get("page_from") or 1)
        cit = f"{slot}·P{page_from}"
        unit_mult = float(tbl.get("unit_multiplier") or 1.0)
        unit_raw = str(tbl.get("unit_raw") or "")
        rows = tbl.get("rows") or []

        for row in rows:
            if isinstance(row, dict):
                label = str(row.get("label") or "")
                vals = row.get("values") or []
                val = vals[target_col_index] if target_col_index < len(vals) else None
            elif isinstance(row, list) and len(row) > 1 + target_col_index:
                label = str(row[0].get("raw_text") or "")
                val = row[1 + target_col_index].get("numeric_value")
            else:
                continue

            if val is None:
                continue
            matched = _canonical_metric_for_label(label)
            if matched is None:
                continue
            m_key, m_label, is_pct = matched
            if m_key not in baselines:
                formatted = (
                    f"{val:.2f}%"
                    if is_pct
                    else f"{val:,.2f} {unit_raw}".strip()
                )
                baselines[m_key] = AuditedBaseline(
                    metric_key=m_key,
                    metric_label=m_label,
                    value=float(val),
                    unit_multiplier=1.0 if is_pct else unit_mult,
                    is_percentage=is_pct,
                    citation=cit,
                    formatted_value=formatted,
                )
    return baselines


def detect_discrepancies(
    *,
    narrative_text: str = "",
    narrative_slot: str = "S01",
    explicit_claims: list[NarrativeClaim] | None = None,
    hydrated_tables: list[dict[str, Any]] | None = None,
    explicit_baselines: dict[str, AuditedBaseline] | None = None,
    trend_snapshot: FinancialTrendSnapshot | None = None,
    numeric_tolerance_pct: float = 1.5,
    high_severity_pct: float = 5.0,
) -> list[dict[str, Any]]:
    """Run deterministic Quant-vs-Narrative Discrepancy Detection (0 LLM).

    Returns list of `DiscrepancyAlert` dicts matching `PROJECT.md` Contract 4.
    Strictly ignores `TABLE_UNVERIFIED` tables as audited ground truth.
    """
    claims: list[NarrativeClaim] = list(explicit_claims or [])
    if narrative_text:
        claims.extend(extract_narrative_claims(narrative_text, sub_doc_slot=narrative_slot))

    baselines: dict[str, AuditedBaseline] = {}
    if hydrated_tables:
        baselines.update(extract_audited_baselines_from_tables(hydrated_tables))
    if explicit_baselines:
        baselines.update(explicit_baselines)

    raw_alerts: list[DiscrepancyAlert] = []
    seen_keys: set[tuple[str, str, str]] = set()

    # 1. Quantitative claim vs verified audited table / baseline
    for claim in claims:
        if claim.claimed_value is None:
            continue
        base = baselines.get(claim.metric_key)
        if base is None:
            continue

        if claim.is_percentage or base.is_percentage:
            claimed_norm = claim.claimed_value
            audited_norm = base.value
        else:
            claimed_norm = claim.claimed_value * claim.unit_multiplier
            audited_norm = base.value * base.unit_multiplier

        denom = max(abs(audited_norm), 1e-9)
        delta_pct = round(abs(claimed_norm - audited_norm) / denom * 100.0, 2)
        sign_flip = (claimed_norm * audited_norm) < 0

        if delta_pct >= numeric_tolerance_pct or sign_flip:
            sev: Severity = "HIGH" if (delta_pct >= high_severity_pct or sign_flip) else "MEDIUM"
            dedup_k = ("NUMERIC", claim.metric_key, claim.citation)
            if dedup_k in seen_keys:
                continue
            seen_keys.add(dedup_k)
            cits = [c for c in (claim.citation, base.citation) if c]
            raw_alerts.append(
                DiscrepancyAlert(
                    alert_id="",
                    severity=sev,
                    metric=claim.metric_label,
                    narrative_claim=claim.claim_text or f"{claim.metric_label}: {claim.claimed_value}",
                    audited_value=base.formatted_value or str(base.value),
                    delta_pct=delta_pct,
                    citations=cits,
                    alert_type="NUMERIC_CLAIM_MISMATCH",
                )
            )

    # 2. Directional / Margin / Plan / Earnings-Quality divergence against FinancialTrendSnapshot
    if trend_snapshot is not None:
        pos_margin_claim = next(
            (c for c in claims if c.metric_key == "gross_margin" and c.stance == "POSITIVE"),
            None,
        )
        if (
            pos_margin_claim is not None
            and trend_snapshot.gpm_yoy_bps is not None
            and trend_snapshot.gpm_yoy_bps <= -150.0
        ):
            cits = [pos_margin_claim.citation] + list(trend_snapshot.citations)
            raw_alerts.append(
                DiscrepancyAlert(
                    alert_id="",
                    severity="HIGH",
                    metric="Biên lợi nhuận gộp",
                    narrative_claim=pos_margin_claim.claim_text,
                    audited_value=f"Biên gộp thực tế giảm {trend_snapshot.gpm_yoy_bps:+.0f} bps YoY",
                    delta_pct=round(abs(trend_snapshot.gpm_yoy_bps) / 100.0, 2),
                    citations=[c for c in cits if c],
                    alert_type="MARGIN_DIVERGENCE",
                )
            )

        pos_growth_claim = next(
            (c for c in claims if c.metric_key in ("profit_growth", "npat", "net_revenue") and c.stance == "POSITIVE"),
            None,
        )
        if pos_growth_claim is not None:
            if trend_snapshot.npat_yoy_pct is not None and trend_snapshot.npat_yoy_pct <= -5.0:
                cits = [pos_growth_claim.citation] + list(trend_snapshot.citations)
                raw_alerts.append(
                    DiscrepancyAlert(
                        alert_id="",
                        severity="HIGH",
                        metric="Lợi nhuận sau thuế",
                        narrative_claim=pos_growth_claim.claim_text,
                        audited_value=f"LNST kiểm toán/Core DB giảm {trend_snapshot.npat_yoy_pct:+.1f}% YoY",
                        delta_pct=round(abs(trend_snapshot.npat_yoy_pct), 2),
                        citations=[c for c in cits if c],
                        alert_type="GROWTH_DIVERGENCE",
                    )
                )
            if (
                trend_snapshot.cfo_value is not None
                and trend_snapshot.npat_value is not None
                and trend_snapshot.cfo_value < 0
                and trend_snapshot.npat_value > 0
            ):
                cits = [pos_growth_claim.citation] + list(trend_snapshot.citations)
                raw_alerts.append(
                    DiscrepancyAlert(
                        alert_id="",
                        severity="HIGH",
                        metric="Chất lượng dòng tiền (CFO vs LNST)",
                        narrative_claim=pos_growth_claim.claim_text,
                        audited_value=(
                            f"LNST dương ({trend_snapshot.npat_value:,.0f}) "
                            f"nhưng CFO âm ({trend_snapshot.cfo_value:,.0f})"
                        ),
                        delta_pct=100.0,
                        citations=[c for c in cits if c],
                        alert_type="EARNINGS_QUALITY_DIVERGENCE",
                    )
                )

        if (
            trend_snapshot.plan_target_pbt is not None
            and trend_snapshot.actual_pbt is not None
            and trend_snapshot.plan_target_pbt > 0
        ):
            ratio_pct = (trend_snapshot.actual_pbt / trend_snapshot.plan_target_pbt) * 100.0
            diff_pct = round(abs(ratio_pct - 100.0), 2)
            if diff_pct >= 20.0:
                sev_plan: Severity = "HIGH" if trend_snapshot.actual_pbt < trend_snapshot.plan_target_pbt else "MEDIUM"
                raw_alerts.append(
                    DiscrepancyAlert(
                        alert_id="",
                        severity=sev_plan,
                        metric="Thực hiện vs Kế hoạch LNTT",
                        narrative_claim=f"Kế hoạch LNTT năm: {trend_snapshot.plan_target_pbt:,.0f}",
                        audited_value=f"Thực tế đạt {ratio_pct:.1f}% kế hoạch ({trend_snapshot.actual_pbt:,.0f})",
                        delta_pct=diff_pct,
                        citations=list(trend_snapshot.citations) or ["S03·P1"],
                        alert_type="PLAN_VS_ACTUAL_DIVERGENCE",
                    )
                )

        if (
            trend_snapshot.operating_lease_yoy_pct is not None
            and trend_snapshot.operating_lease_yoy_pct >= 50.0
        ):
            raw_alerts.append(
                DiscrepancyAlert(
                    alert_id="",
                    severity="MEDIUM",
                    metric="Cam kết thuê hoạt động ngoại bảng",
                    narrative_claim="Tăng trưởng quy mô vận hành",
                    audited_value=(
                        f"Cam kết thuê hoạt động ngoại bảng tăng +{trend_snapshot.operating_lease_yoy_pct:.1f}% YoY"
                    ),
                    delta_pct=round(trend_snapshot.operating_lease_yoy_pct, 2),
                    citations=list(trend_snapshot.citations) or ["S10·P1"],
                    alert_type="OFF_BALANCE_SHEET_LEASE_JUMP",
                )
            )

    raw_alerts.sort(key=lambda a: (0 if a.severity == "HIGH" else 1, -a.delta_pct))
    finalized: list[dict[str, Any]] = []
    for idx, alert in enumerate(raw_alerts, start=1):
        finalized.append(
            DiscrepancyAlert(
                alert_id=f"DISC-{idx}",
                severity=alert.severity,
                metric=alert.metric,
                narrative_claim=alert.narrative_claim,
                audited_value=alert.audited_value,
                delta_pct=alert.delta_pct,
                citations=alert.citations,
                alert_type=alert.alert_type,
            ).to_dict()
        )
    return finalized
