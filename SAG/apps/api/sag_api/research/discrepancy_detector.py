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
from sag_api.research.table_engine import (
    classify_table_columns,
    detect_unit_and_currency,
    parse_vn_number,
)

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


_RETAINED_EARNINGS_EXCLUDE_RE = re.compile(
    r"(?:chưa\s+phân\s+phối|lũy\s+kế|đã\s+thực\s+hiện|chưa\s+thực\s+hiện|"
    r"retained\s+earnings|accumulated|undistributed)",
    re.IGNORECASE,
)
_PRIMARY_STMT_PRIORITY_RE = re.compile(
    r"(?:kết\s+quả\s+hoạt\s+động|bảng\s+cân\s+đối\s+kế\s+toán|tình\s+hình\s+tài\s+chính|"
    r"lưu\s+chuyển\s+tiền\s+tệ|\bb0[1-3]\b|kqkd|cđkt|lctt|income\s+statement|balance\s+sheet|cash\s+flow)",
    re.IGNORECASE,
)
_SUB_NOTE_PATH_RE = re.compile(
    r"(?:thuyết\s+minh\s+(?:các\s+)?báo\s+cáo\s+tài\s+chính|thuyết\s+minh\s+bctc|"
    r"notes\s+to\s+the\s+financial\s+statements)",
    re.IGNORECASE,
)
_BALANCE_SHEET_TBL_RE = re.compile(
    r"(?:bảng\s+cân\s+đối\s+kế\s+toán|tình\s+hình\s+tài\s+chính|\bb01\b|balance\s+sheet)",
    re.IGNORECASE,
)
_FLOW_ONLY_METRICS = frozenset({"net_revenue", "npat", "pbt", "gross_margin"})
_PRIMARY_ONLY_METRICS = frozenset({"net_revenue", "npat", "pbt", "total_assets"})


def _canonical_metric_for_label(label: str) -> tuple[str, str, bool] | None:
    low = normalize_ws(label).lower()
    for m_key, (display_name, aliases, is_pct) in _METRIC_ALIASES.items():
        if m_key == "npat" and _RETAINED_EARNINGS_EXCLUDE_RE.search(low):
            continue
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


def _resolve_baseline_col_index(
    tbl: dict[str, Any],
    *,
    requested_col_index: int = 0,
) -> int | None:
    """Resolve the 0-based index into `row['values']` representing the primary amount column.

    Skips `code` (`Mã số`) and `note_ref` (`Thuyết minh`) columns automatically when
    `requested_col_index == 0` (the default). Returns `None` if no monetary column exists.
    """
    if requested_col_index > 0:
        return requested_col_index
    col_roles = tbl.get("column_roles")
    amt_indices = tbl.get("amount_col_indices")
    if not col_roles or amt_indices is None:
        headers = tbl.get("headers") or tbl.get("headers_json") or []
        rows = tbl.get("rows") or tbl.get("rows_json") or []
        col_roles, amt_indices = classify_table_columns(headers, rows)
    if col_roles and amt_indices:
        # Prefer first `period_amount` column if present
        for idx in amt_indices:
            if (idx + 1) < len(col_roles) and col_roles[idx + 1] == "period_amount":
                return int(idx)
        return int(amt_indices[0])
    if col_roles and not amt_indices:
        return None
    return 0


def extract_audited_baselines_from_tables(
    hydrated_tables: list[dict[str, Any]],
    *,
    target_col_index: int = 0,
) -> dict[str, AuditedBaseline]:
    """Extract audited numeric baselines ONLY from `VERIFIED` tables (ignoring `TABLE_UNVERIFIED`).

    Prioritizes primary financial statements (CĐKT/KQKD/LCTT) before sub-note tables,
    blocks Note sub-tables from masquerading as consolidated revenue/NPAT/assets, blocks
    Balance Sheet tables from supplying P&L flow metrics, and skips `Mã số`/`Thuyết minh`.
    """
    baselines: dict[str, AuditedBaseline] = {}
    exact_net_rev_locked = False

    def _is_sub_note_table(tbl: dict[str, Any]) -> bool:
        sec_path = str(tbl.get("section_path") or "")
        title = str(tbl.get("title") or tbl.get("table_title") or "")
        blob = f"{sec_path} {title}"
        return bool(_SUB_NOTE_PATH_RE.search(blob))

    def _is_balance_sheet_table(tbl: dict[str, Any]) -> bool:
        sec_path = str(tbl.get("section_path") or "")
        title = str(tbl.get("title") or tbl.get("table_title") or "")
        return bool(_BALANCE_SHEET_TBL_RE.search(f"{sec_path} {title}"))

    def _table_sort_key(tbl: dict[str, Any]) -> tuple[int, int]:
        title_blob = f"{tbl.get('title') or tbl.get('table_title') or ''} {tbl.get('section_path') or ''}"
        slot_u = str(tbl.get("sub_doc_slot") or "").upper()
        is_note = _is_sub_note_table(tbl)
        is_primary = 0 if (not is_note and _PRIMARY_STMT_PRIORITY_RE.search(title_blob)) else 1
        is_audited = 0 if slot_u in ("BCTC_AUDITED", "S06", "S07", "S08") else 1
        return (is_audited, is_primary)

    ordered_tables = sorted(hydrated_tables, key=_table_sort_key)
    for tbl in ordered_tables:
        if tbl.get("verification_status") != "VERIFIED":
            continue
        is_sub_note = _is_sub_note_table(tbl)
        is_bs = _is_balance_sheet_table(tbl)
        raw_slot = str(tbl.get("sub_doc_slot") or "BCTC_AUDITED").strip()
        # Preserve legacy S01..S13 short codes while NEVER truncating Natural 4-Class names
        slot = raw_slot[:3] if re.match(r"^S\d{2}(?:_|$)", raw_slot) else raw_slot
        page_from = int(tbl.get("page_from") or 1)
        cit = f"{slot}·P{page_from}"
        unit_mult = float(tbl.get("unit_multiplier") or 1.0)
        unit_raw = str(tbl.get("unit_raw") or "")
        rows = tbl.get("rows") or []
        eff_col_idx = _resolve_baseline_col_index(tbl, requested_col_index=target_col_index)
        if eff_col_idx is None:
            continue

        for row in rows:
            if isinstance(row, dict):
                label = str(row.get("label") or "")
                vals = row.get("values") or []
                val = vals[eff_col_idx] if eff_col_idx < len(vals) else None
            elif isinstance(row, list) and len(row) > 1 + eff_col_idx:
                label = str(row[0].get("raw_text") or "")
                val = row[1 + eff_col_idx].get("numeric_value")
            else:
                continue

            if val is None:
                continue
            matched = _canonical_metric_for_label(label)
            if matched is None:
                continue
            m_key, m_label, is_pct = matched
            if is_sub_note and m_key in _PRIMARY_ONLY_METRICS:
                continue
            if is_bs and m_key in _FLOW_ONLY_METRICS:
                continue
            is_exact_net_rev = m_key == "net_revenue" and "doanh thu thuần" in label.lower()
            can_upgrade_net_rev = (
                is_exact_net_rev
                and not exact_net_rev_locked
                and m_key in baselines
                and baselines[m_key].citation == cit
            )
            if m_key not in baselines or can_upgrade_net_rev:
                if is_exact_net_rev:
                    exact_net_rev_locked = True
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
