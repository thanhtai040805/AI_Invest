"""Deterministic extraction of AGM business-plan targets from `sag_extracted_tables` (slot AGM_TARGETS).

Targets are never accepted from the LLM. A target is emitted only when, in one PIT-valid table:
- a column header names a plan ("kế hoạch"/"KH") for exactly the requested fiscal year (header year, or
  the table title/section year when the header carries none), and is not a ratio/variance/parent-only column;
- a row label maps to a canonical item (revenue_net | pbt | npat | npat_parent);
- a monetary unit is resolvable (row unit cell > column header > row label > first header/title > table unit);
- the value is positive and, when a prior-FY actual exists, within a plausible band of it.
Candidates are then clustered across tables; adjusted plans ("điều chỉnh") supersede originals, the
cluster backed by the most distinct tables wins, and an unresolved tie is reported as CONFLICT.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

PLAUSIBLE_RATIO = (0.25, 4.0)
CLUSTER_TOL = 0.005

_UNIT_WORDS: tuple[tuple[str, float], ...] = (
    ("ty", 1e9),
    ("trieu", 1e6),
    ("(?:nghin|ngan)", 1e3),
)
_UNIT_CELL_RE = re.compile(r"^(ty|trieu|nghin|ngan)?\s*(dong|vnd|d)$")
_YEAR_RE = re.compile(r"(?<!\d)(20\d\d)(?!\d)")
_PLAN_RE = re.compile(r"ke ?hoach|\bkh\b")
_BAD_COL_RE = re.compile(r"%|ty le|so voi|tang truong|thuc hien|cong ty me|rieng le|\bth\b|chenh lech|\+/-")
_BAD_LABEL_RE = re.compile(r"ty suat|/|%|tang truong|chua phan phoi|phan phoi|con lai|rieng le|nang suat|\bbien\b")


def _norm(text: Any) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "d")
    s = s.encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", s).strip()


def _unit_from_text(text: str) -> float | None:
    """Monetary scale stated in free text, e.g. '(tỷ đồng)', 'ĐVT: Tỷ đồng', 'triệu VNĐ', 'Tỷ Đ'."""
    t = _norm(text)
    for word, mult in _UNIT_WORDS:
        if re.search(rf"\b{word}\s*(dong|vnd|d)\b", t) or re.search(rf"dvt\W*{word}\b", t):
            return mult
    return None


@dataclass(frozen=True, slots=True)
class TargetCandidate:
    item: str
    value: float  # VND
    exact_definition: bool
    adjusted: bool
    table_id: str
    page: int
    title: str
    raw_text: str


@dataclass
class TargetExtraction:
    fiscal_year: int
    targets: dict[str, TargetCandidate] = field(default_factory=dict)
    conflicts: dict[str, list[TargetCandidate]] = field(default_factory=dict)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    candidates: list[TargetCandidate] = field(default_factory=list)


def classify_label(label: str) -> tuple[str, bool] | None:
    """Map an AGM row label to (canonical item, exact_definition)."""
    t = _norm(label)
    t = re.sub(r"^[\divx.\-) ]+", "", t)
    if not t or _BAD_LABEL_RE.search(t):
        return None
    if "truoc thue" in t or re.search(r"\blntt\b", t):
        return ("pbt", True)
    if "sau thue" in t or re.search(r"\blnst\b", t):
        if "co dong" in t or re.search(r"(thuoc|cua) (ve )?(co dong )?cong ty me", t):
            return ("npat_parent", True)
        return ("npat", True)
    if "doanh thu" in t:
        if re.search(r"tai chinh|khac|phi |hoa don", t):
            return None
        return ("revenue_net", "doanh thu thuan" in t)
    return None


def _plan_columns(headers: Sequence[Any], title_years: set[int], fy: int) -> list[tuple[int, bool]]:
    cols: list[tuple[int, bool]] = []
    for idx, h in enumerate(headers):
        t = _norm(h)
        if not _PLAN_RE.search(t) or _BAD_COL_RE.search(t):
            continue
        years = {int(y) for y in _YEAR_RE.findall(t)}
        if years:
            if years != {fy}:
                continue
        elif title_years != {fy}:
            continue
        cols.append((idx, "dieu chinh" in t))
    return cols


def _row_unit(cells: Sequence[Mapping[str, Any]], header: str, label: str, table: Mapping[str, Any]) -> float | None:
    for c in cells:
        txt = _norm(c.get("raw_text"))
        if txt and _UNIT_CELL_RE.match(txt):
            for word, mult in _UNIT_WORDS:
                if re.match(word, txt):
                    return mult
            return 1.0
    for src in (header, label, str((table.get("headers_json") or [""])[0]), str(table.get("table_title") or "")):
        mult = _unit_from_text(src)
        if mult:
            return mult
    raw = _norm(table.get("unit_raw"))
    if raw.startswith(("ty", "trieu", "nghin", "ngan")):
        return float(table.get("unit_multiplier") or 0.0) or None
    return None


def _has_pct_cell(cells: Sequence[Mapping[str, Any]]) -> bool:
    return any(_norm(c.get("raw_text")) == "%" for c in cells)


def extract_plan_targets(
    tables: Sequence[Mapping[str, Any]],
    fiscal_year: int,
    *,
    prior_actuals: Mapping[str, float] | None = None,
) -> TargetExtraction:
    out = TargetExtraction(fiscal_year=fiscal_year)
    prior = prior_actuals or {}
    for tbl in tables:
        headers = list(tbl.get("headers_json") or [])
        title = str(tbl.get("table_title") or "")
        title_n = _norm(title)
        if "cong ty me" in title_n and "hop nhat" not in title_n:
            continue
        title_years = {int(y) for y in _YEAR_RE.findall(_norm(f"{title} {tbl.get('section_path') or ''}"))}
        cols = _plan_columns(headers, title_years, fiscal_year)
        if not cols:
            continue
        seen_items: set[str] = set()
        for cells in tbl.get("rows_json") or ():
            if not isinstance(cells, list) or _has_pct_cell(cells):
                continue
            label_cell = next(
                (c for c in cells if isinstance(c, Mapping) and classify_label(str(c.get("raw_text") or ""))), None
            )
            if label_cell is None:
                continue
            item, exact = classify_label(str(label_cell.get("raw_text") or ""))  # type: ignore[misc]
            if item in seen_items:
                continue  # first occurrence = consolidated block in mixed consolidated/parent tables
            for col_idx, adjusted in cols:
                if col_idx >= len(cells):
                    continue
                cell = cells[col_idx]
                num = cell.get("numeric_value") if isinstance(cell, Mapping) else None
                base = {"item": item, "table_id": tbl.get("table_id"), "page": tbl.get("page_from"),
                        "raw_text": cell.get("raw_text") if isinstance(cell, Mapping) else None}
                if not isinstance(num, int | float) or isinstance(num, bool):
                    out.rejected.append({**base, "reason": "NON_NUMERIC"})
                    continue
                mult = _row_unit(cells, str(headers[col_idx]), str(label_cell.get("raw_text") or ""), tbl)
                if mult is None:
                    out.rejected.append({**base, "reason": "UNIT_UNKNOWN"})
                    continue
                value = float(num) * mult
                if value <= 0:
                    out.rejected.append({**base, "reason": "NON_POSITIVE"})
                    continue
                ref = prior.get(item)
                if ref and ref > 0 and not (PLAUSIBLE_RATIO[0] <= value / ref <= PLAUSIBLE_RATIO[1]):
                    out.rejected.append({**base, "reason": "IMPLAUSIBLE_VS_PRIOR_FY", "value": value, "prior": ref})
                    continue
                out.candidates.append(
                    TargetCandidate(item, value, exact, adjusted, str(tbl.get("table_id") or ""),
                                    int(tbl.get("page_from") or 0), title[:120], str(cell.get("raw_text") or ""))
                )
                seen_items.add(item)
    _resolve(out)
    return out


def _resolve(out: TargetExtraction) -> None:
    by_item: dict[str, list[TargetCandidate]] = {}
    for c in out.candidates:
        by_item.setdefault(c.item, []).append(c)
    for item, cands in by_item.items():
        pool = [c for c in cands if c.adjusted] or cands
        if any(c.exact_definition for c in pool):
            pool = [c for c in pool if c.exact_definition]
        clusters: list[list[TargetCandidate]] = []
        for c in pool:
            for cl in clusters:
                if abs(c.value - cl[0].value) <= CLUSTER_TOL * cl[0].value:
                    cl.append(c)
                    break
            else:
                clusters.append([c])
        clusters.sort(key=lambda cl: len({c.table_id for c in cl}), reverse=True)
        support = [len({c.table_id for c in cl}) for cl in clusters]
        if len(clusters) > 1 and support[0] == support[1]:
            out.conflicts[item] = [cl[0] for cl in clusters]
            continue
        out.targets[item] = clusters[0][0]
