"""Brief-Centric Candidate Locator, Parallel Per-Claim Executor & Verdict Cards (`brief_executor.py`).

Implements Components (3), (4), and (5) of the SAG Brief-Centric Brain:
- Component (3) `locate_candidates`: 0-LLM-token universe & anchor-relative locator over `FactSet` +
  market caps (with offline `screen_universe_metrics` fallback when Core PG is absent).
- Component (4) `execute_brief_claims`: runs `premises_to_check` first, then executes independent
  claims concurrently (`0`-token Python quant engines + bounded-concurrency `doc:*` Mini-Reader LLM calls).
- Component (5) `ClaimVerdictCard` + code-owned 1-hop selective escalation on anomalies/contradictions,
  with hard budget termination (never relying on Flash self-stopping).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from sag_agent.tools import ToolContext
from sag_agent.types import AgentMessage, CancellationToken, ModelProvider, ModelRequest, Usage
from sag_api.research.analytics import (
    KpiStatus,
    KpiValue,
    PeriodRef,
    PlanTarget,
    compute_kpi,
    earnings_quality,
    latest_analysis_period,
    peer_compare,
    plan_vs_actual,
    red_flag_scan,
)
from sag_api.research.analytics.agm_targets import extract_plan_targets
from sag_api.research.analytics.kpis import KPI_BY_ID
from sag_api.research.brief import (
    MAX_DOC_SPECS_PER_CLAIM,
    CandidateCriterion,
    ClaimSpec,
    ResearchBrief,
    TraceSink,
    emit_trace,
    usage_to_dict,
)
from sag_api.research.facts import FactSet, LookupStatus, Template
from sag_api.research.facts.catalog import CATALOG_BY_ID, items_for_template, resolve_template
from sag_api.research.facts.store import FLAG_QUARTERLY_CF_UNRELIABLE
from sag_api.research.financial_metrics_store import screen_universe_metrics
from sag_api.research.tools.analyst_tools import _agm_tables, _fact_set, _register_kpi, fmt_value
from sag_api.research.tools.context import ResearchToolContext
from sag_api.research.tools.research_tools import (
    _execute_disclosure_search,
    hydrate_financial_tables_tool,
    normalize_disclosure_slots,
    query_knowledge_graph_tool,
)

logger = logging.getLogger(__name__)

VerdictStatus = Literal["CONFIRMED", "REFUTED", "PARTIAL", "NO_DATA"]

MAX_CANDIDATE_BATCH = 500
MAX_MINI_READER_CONCURRENCY = 3
MAX_ESCALATION_CLAIMS = 2

_THINK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_PERIOD_Q_PARSE_RE = re.compile(r"Q([1-4])/(\d{4})", re.IGNORECASE)
_PERIOD_FY_PARSE_RE = re.compile(r"FY(\d{4})", re.IGNORECASE)

_NEGATIVE_PREMISE_RE = re.compile(
    r"(?:\bsập\b|giảm\s+mạnh|lao\s+dốc|bốc\s+hơi|suy\s+sụp|đổ\s+vỡ|thua\s+lỗ|\blỗ\b|\bâm\b|cắt\s+cổ\s+tức)",
    re.IGNORECASE,
)
_POSITIVE_PREMISE_RE = re.compile(
    r"(?:tăng\s+bằng\s+lần|tăng\s+đột\s+biến|tăng\s+gấp\s+đôi|kỷ\s+lục|vượt\s+kế\s+hoạch)",
    re.IGNORECASE,
)

MINI_READER_STATIC_SYSTEM_PROMPT = (
    "Bạn là Chuyên viên Đọc Kiểm Toán BCTC & Thuyết Minh (Forensic Disclosure Mini-Reader) của AIInvest.\n"
    "Nhiệm vụ: chỉ dựa trên các đoạn tài liệu `[C#]` được cung cấp, trích xuất dữ kiện chính xác để trả lời "
    "ĐÚNG MỘT Ý nhỏ (`claim`) và xuất ra ĐÚNG MỘT JSON (không viết văn bản ngoài JSON, không tự cộng trừ nhẩm).\n\n"
    "QUY TẮC BẮT BUỘC:\n"
    "1. Mọi con số hoặc sự kiện trích ra trong `findings` PHẢI có sẵn nguyên bản trong đoạn `[C#]` và gắn ngay "
    "mã `[C#]` tương ứng ở cuối câu (ví dụ: `\"Vay ngắn hạn ngân hàng chịu lãi suất 5,5% - 7,2%/năm [C2].\"`).\n"
    "2. TUYỆT ĐỐI KHÔNG tự cộng các khoản mục con trong thuyết minh vào số tổng trên Bảng cân đối kế toán "
    "(tránh lỗi cộng trùng nợ dài hạn đến hạn trả vào tổng vay ngắn hạn).\n"
    "3. Nếu các đoạn `[C#]` không chứa thông tin trả lời cho ý này, đặt `\"status\": \"NO_DATA\"` và ghi rõ "
    "khoảng trống, KHÔNG suy đoán.\n"
    "4. Chỉ điền `anomaly_followup_query` (chuỗi từ khóa VAS ngắn < 10 từ) KHI VÀ CHỈ KHI đoạn `[C#]` nhắc tới "
    "một mã thuyết minh khác đang mâu thuẫn trực tiếp với số liệu và chưa có trong đoạn hiện tại; "
    "còn lại để `null`.\n\n"
    "CẤU TRÚC JSON ĐẦU RA:\n"
    "{\n"
    '  "status": "CONFIRMED | REFUTED | PARTIAL | NO_DATA",\n'
    '  "findings": ["dòng sự kiện 1 kèm [C#]", "dòng sự kiện 2 kèm [C#]"],\n'
    '  "anomaly_followup_query": null\n'
    "}"
)


def _norm_ascii(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "d")
    return s.encode("ascii", "ignore").decode().lower().strip()


def _parse_json_obj(raw: str) -> dict[str, Any] | None:
    cleaned = _THINK_RE.sub("", raw or "").strip()
    close_idx = cleaned.lower().find("</think>")
    if close_idx != -1:
        cleaned = cleaned[close_idx + len("</think>") :].strip()
    cleaned = _CODE_FENCE_RE.sub("", cleaned).strip()
    for candidate in (cleaned, cleaned[cleaned.find("{") : cleaned.rfind("}") + 1] if "{" in cleaned else ""):
        if not candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


async def _collect_model_text(
    model: ModelProvider,
    messages: Sequence[AgentMessage],
    *,
    turn: int = 1,
) -> tuple[str, Usage]:
    req = ModelRequest(messages=tuple(messages), tools=(), turn=turn)
    parts: list[str] = []
    in_tok = out_tok = cached_tok = reas_tok = 0
    async for chunk in model.stream_turn(req, CancellationToken()):
        if chunk.usage is not None:
            in_tok = max(in_tok, chunk.usage.input_tokens)
            out_tok = max(out_tok, chunk.usage.output_tokens)
            cached_tok = max(cached_tok, chunk.usage.cached_tokens)
            reas_tok = max(reas_tok, chunk.usage.reasoning_tokens)
        if chunk.text_delta:
            parts.append(chunk.text_delta)
    return "".join(parts), Usage(
        requests=1,
        input_tokens=in_tok,
        output_tokens=out_tok,
        cached_tokens=cached_tok,
        reasoning_tokens=reas_tok,
    )


@dataclass
class LocatedTargets:
    analysis_tickers: list[str]
    anchor_ticker: str | None = None
    anchor_metrics: dict[str, float | None] = field(default_factory=dict)
    candidate_rows: list[dict[str, Any]] = field(default_factory=list)
    summary_lines: list[str] = field(default_factory=list)
    coverage_note: str | None = None


@dataclass
class ClaimVerdictCard:
    claim_id: str
    is_premise: bool
    statement: str
    period: str | None
    status: VerdictStatus
    summary_lines: list[str] = field(default_factory=list)
    badges: list[str] = field(default_factory=list)
    escalation_query: str | None = None
    escalated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "claim_id": self.claim_id,
            "is_premise": self.is_premise,
            "statement": self.statement,
            "period": self.period,
            "status": self.status,
            "summary_lines": list(self.summary_lines),
            "badges": list(self.badges),
            "escalation_query": self.escalation_query,
            "escalated": self.escalated,
        }

    def render_for_synthesis(self) -> str:
        role_lbl = "TIỀN ĐỀ CẦN KIỂM TRA" if self.is_premise else "Ý PHÂN TÍCH"
        p_lbl = f" ({self.period})" if self.period else ""
        esc_lbl = " [Đã đào sâu thuyết minh vòng 2]" if self.escalated else ""
        head = f"### [{self.claim_id}] {role_lbl}{p_lbl} — Trạng thái: {self.status}{esc_lbl}\nĐề bài: {self.statement}"
        body = "\n".join(f"- {ln}" for ln in self.summary_lines) if self.summary_lines else "- Không có dữ liệu PIT."
        return f"{head}\n{body}"


@dataclass
class BriefExecutionResult:
    located: LocatedTargets
    premise_cards: list[ClaimVerdictCard] = field(default_factory=list)
    claim_cards: list[ClaimVerdictCard] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    tool_calls_count: int = 0
    escalations_run: int = 0


def _resolve_period_ref(fs: FactSet, ticker: str, raw_period: str | None) -> PeriodRef | None:
    if raw_period:
        txt = raw_period.strip().upper()
        qm = _PERIOD_Q_PARSE_RE.search(txt)
        if qm:
            fq, fy = int(qm.group(1)), int(qm.group(2))
            ptype = "TTM" if txt.startswith("TTM") else "Q"
            if fs.get(ticker, "npat", ptype, fy, fq).status is LookupStatus.OK:
                return PeriodRef(ptype, fy, fq)
        fym = _PERIOD_FY_PARSE_RE.search(txt)
        if fym:
            fy = int(fym.group(1))
            if fs.get(ticker, "npat", "FY", fy, 0).status is LookupStatus.OK:
                return PeriodRef("FY", fy, 0)
    return latest_analysis_period(fs, ticker)


def _latest_stock_val(fs: FactSet, ticker: str, item: str) -> tuple[float | None, str]:
    lq = fs.latest_period(ticker, "Q")
    if lq is not None:
        r = fs.get(ticker, item, "Q", lq[0], lq[1])
        if r.status is LookupStatus.OK and r.fact is not None:
            return r.fact.value, r.fact.period_label
    lfy = fs.latest_period(ticker, "FY")
    if lfy is not None:
        r = fs.get(ticker, item, "FY", lfy[0], 0)
        if r.status is LookupStatus.OK and r.fact is not None:
            return r.fact.value, r.fact.period_label
    return None, "-"


_CASH_COMPOSITE_METRICS = frozenset(
    {"cash_to_market_cap", "net_cash_to_market_cap", "net_cash", "total_debt", "net_debt"}
)


def eval_candidate_metric(
    fs: FactSet,
    ticker: str,
    metric_id: str,
    market_caps: Mapping[str, float],
) -> tuple[float | None, str, str]:
    """Deterministically evaluate a `FactSet`, `KPI`, or composite valuation/cash metric (0 tokens).

    Returns `(numeric_value_or_None, unit_code, period_label)` so every number is shown with its period.
    """
    tk = ticker.strip().upper()
    if tk not in fs.templates:
        return None, "", "-"

    mcap = market_caps.get(tk)
    mcap_lbl = "PIT"
    if mcap is None and (date.today() - fs.as_of_date).days <= 3:
        # Never use vendor `vr_market_cap` on historical `as_of_date` (alphastock backfills fetch-date market cap)
        mcap, mcap_lbl = _latest_stock_val(fs, tk, "vr_market_cap")

    if metric_id == "market_cap":
        return mcap, "VND", mcap_lbl

    if metric_id in {"pe", "pb", "ev_ebitda"}:
        vr_val, vr_lbl = _latest_stock_val(fs, tk, f"vr_{metric_id}")
        if vr_val is not None and vr_val > 0:
            return vr_val, "X", vr_lbl
        if metric_id == "pe" and mcap and mcap > 0:
            p = latest_analysis_period(fs, tk)
            if p is not None:
                np_res = fs.get(tk, "npat", p.period_type, p.fiscal_year, p.fiscal_quarter)
                if np_res.status is LookupStatus.OK and np_res.fact and np_res.fact.value > 0:
                    return mcap / np_res.fact.value, "X", p.label
        if metric_id == "pb" and mcap and mcap > 0:
            eq, eq_lbl = _latest_stock_val(fs, tk, "equity")
            if eq and eq > 0:
                return mcap / eq, "X", eq_lbl
        return None, "X", "-"

    if metric_id in _CASH_COMPOSITE_METRICS:
        unit = "VND" if metric_id in {"net_cash", "net_debt", "total_debt"} else "RATIO"
        # Bank/insurer/securities cash & investments back deposits/technical reserves/client money:
        # "net cash vs market cap" is economically meaningless for them, so never rank them on it.
        if fs.templates[tk] is not Template.CORPORATE:
            return None, unit, "N/A (doanh nghiệp tài chính)"
        cash, c_lbl = _latest_stock_val(fs, tk, "cash_and_equivalents")
        if cash is None:
            c1, c_lbl = _latest_stock_val(fs, tk, "cash_only")
            c2, _ = _latest_stock_val(fs, tk, "cash_equivalents_only")
            if c1 is not None or c2 is not None:
                cash = (c1 or 0.0) + (c2 or 0.0)
        st_inv, _ = _latest_stock_val(fs, tk, "st_investments")
        st_d, _ = _latest_stock_val(fs, tk, "st_borrowings")
        lt_d, _ = _latest_stock_val(fs, tk, "lt_borrowings")
        liquid = (cash or 0.0) + (st_inv or 0.0) if (cash is not None or st_inv is not None) else None
        tot_debt = (st_d or 0.0) + (lt_d or 0.0) if (st_d is not None or lt_d is not None) else 0.0
        if metric_id == "total_debt":
            return (tot_debt if (st_d is not None or lt_d is not None) else None), unit, c_lbl
        if liquid is None:
            return None, unit, "-"
        net_cash = liquid - tot_debt
        if metric_id == "net_cash":
            return net_cash, unit, c_lbl
        if metric_id == "net_debt":
            return -net_cash, unit, c_lbl
        if not mcap or mcap <= 0:
            return None, unit, "-"
        lbl = f"{c_lbl}; vốn hóa {mcap_lbl}"
        if metric_id == "cash_to_market_cap":
            return liquid / mcap, unit, lbl
        return net_cash / mcap, unit, lbl

    if metric_id == "fcf_yield":
        p = latest_analysis_period(fs, tk)
        if p is not None and mcap and mcap > 0:
            fcf_kv = compute_kpi(fs, tk, "fcf", p)
            if fcf_kv.status is KpiStatus.OK and fcf_kv.value is not None:
                return fcf_kv.value / mcap, "RATIO", fcf_kv.period
        return None, "RATIO", "-"

    if metric_id == "liabilities_to_equity":
        liab, l_lbl = _latest_stock_val(fs, tk, "total_liabilities")
        eq, _ = _latest_stock_val(fs, tk, "equity")
        if liab is not None and eq and eq > 0:
            return liab / eq, "X", l_lbl
        return None, "X", "-"

    alias_kpi = {
        "revenue_yoy": "top_line_growth_yoy",
        "net_income_yoy": "npat_growth_yoy",
        "cfo_to_net_income": "cfo_to_npat",
    }.get(metric_id, metric_id)

    if alias_kpi in KPI_BY_ID:
        p = latest_analysis_period(fs, tk)
        if p is None:
            return None, KPI_BY_ID[alias_kpi].unit, "-"
        # Prefer TTM; fall back to latest single quarter (label makes the difference explicit)
        kv = compute_kpi(fs, tk, alias_kpi, p)
        if kv.status is KpiStatus.OK and kv.value is not None:
            return kv.value, kv.unit, kv.period
        lq = fs.latest_period(tk, "Q")
        if lq is not None:
            kv_q = compute_kpi(fs, tk, alias_kpi, PeriodRef("Q", lq[0], lq[1]))
            if kv_q.status is KpiStatus.OK and kv_q.value is not None:
                return kv_q.value, kv_q.unit, kv_q.period
        return None, KPI_BY_ID[alias_kpi].unit, "-"

    if metric_id in CATALOG_BY_ID:
        spec = CATALOG_BY_ID[metric_id]
        unit = spec.unit or ("VND" if spec.kind in ("FLOW", "STOCK") else "NUMBER")
        if spec.kind in ("STOCK", "VENDOR_RATIO"):
            val, v_lbl = _latest_stock_val(fs, tk, metric_id)
            return val, unit, v_lbl
        p = latest_analysis_period(fs, tk)
        if p is not None:
            res = fs.get(tk, metric_id, p.period_type, p.fiscal_year, p.fiscal_quarter)
            if res.status is LookupStatus.OK and res.fact is not None:
                return res.fact.value, unit, p.label
        return None, unit, "-"

    return None, "", "-"


def _check_criterion(
    cand_val: float | None,
    crit: CandidateCriterion,
    anchor_val: float | None,
) -> bool:
    if cand_val is None:
        return False
    target = anchor_val if crit.ref == "anchor" else crit.value
    if target is None:
        return False
    tol = crit.tolerance if crit.ref == "anchor" else 0.0
    if crit.op == ">=":
        return cand_val >= target - tol
    if crit.op == "<=":
        return cand_val <= target + tol
    if crit.op == ">":
        return cand_val > target - tol
    if crit.op == "<":
        return cand_val < target + tol
    return abs(cand_val - target) <= max(tol, 1e-6)


async def locate_candidates(
    brief: ResearchBrief,
    ctx: ResearchToolContext,
) -> LocatedTargets:
    """Component (3): Resolve explicit tickers, anchor-benchmark peers, or open-universe candidates (0 tokens)."""
    cand_spec = brief.entities.candidate_search
    anchor = brief.entities.anchor_ticker
    if cand_spec is None:
        base_tks = list(brief.entities.tickers)[:6]
        missing_docs = [t for t in base_tks if ctx.available_tickers and t not in ctx.available_tickers]
        cov_note = (
            f"Lưu ý phạm vi tài liệu: {', '.join(missing_docs)} có số liệu BCTC định lượng Core PG nhưng "
            "chưa index thuyết minh PDF chi tiết trong Qdrant."
            if missing_docs
            else None
        )
        return LocatedTargets(analysis_tickers=base_tks, coverage_note=cov_note)

    # Offline fallback when Core PG is absent (e.g. SQLite-only unit/E2E tests)
    if ctx.core_reader is None:
        async with ctx.session_factory() as session:
            rows, total_matched, desc = await screen_universe_metrics(
                session,
                tenant_id=ctx.tenant_id,
                as_of_date=ctx.as_of_date,
                allowed_project_ids=ctx.principal.allowed_project_ids,
                core_reader=None,
                sectors=cand_spec.sectors or None,
                sort_by=cand_spec.sort_by if cand_spec.sort_by else "roe",
                sort_dir=cand_spec.sort_dir,
                limit=cand_spec.limit,
            )
        await ctx.evidence.register_screening(rows, desc)
        picked = [str(r["ticker"]) for r in rows if r.get("ticker")]
        analysis_tks = list(dict.fromkeys(([anchor] if anchor else []) + picked))[:6]
        summary = [f"Sàng lọc nội bộ PIT ({desc}): khớp {total_matched} mã -> chọn {', '.join(picked) or 'không có'}."]
        return LocatedTargets(
            analysis_tickers=analysis_tks,
            anchor_ticker=anchor,
            candidate_rows=rows,
            summary_lines=summary,
        )

    # Universe = ALL Core PG issuers (not the ~20 filing-indexed org catalog), so "toàn sàn" really means toàn sàn
    try:
        all_profiles = list(await ctx.core_reader.fetch_issuer_profiles())
        profile_source = "CORE_PG_FULL_UNIVERSE"
    except Exception as exc:  # noqa: BLE001
        logger.warning("Core PG issuer universe load failed; falling back to org catalog: %s", exc)
        all_profiles = list(ctx.catalog_profiles)
        profile_source = "ORG_CATALOG_FALLBACK_DEGRADED"
    allowed = ctx.principal.allowed_project_ids
    sec_norms = [_norm_ascii(s) for s in cand_spec.sectors if s.strip()]
    want_tmpl = Template(cand_spec.template) if cand_spec.template in set(Template) else None

    eligible_profiles: list[Mapping[str, Any]] = []
    for p in all_profiles:
        tk = str(p.get("ticker") or "").strip().upper()
        if not tk or ("*" not in allowed and tk not in allowed):
            continue
        if tk == anchor:
            eligible_profiles.append(p)
            continue
        ind_str = str(p.get("industry") or "")
        sec_str = str(p.get("sector") or "")
        p_tmpl = resolve_template(ind_str)
        if want_tmpl is not None and p_tmpl is not want_tmpl:
            continue
        if sec_norms:
            blob = f"{_norm_ascii(ind_str)} {_norm_ascii(sec_str)}"
            if not any(sn in blob for sn in sec_norms):
                continue
        eligible_profiles.append(p)

    if not eligible_profiles:
        return LocatedTargets(
            analysis_tickers=list(brief.entities.tickers)[:6],
            anchor_ticker=anchor,
            summary_lines=["Không có mã cổ phiếu nào trong phạm vi ngành/quyền truy cập để sàng lọc."],
        )

    eligible_n = len(eligible_profiles)
    all_elig_tickers = [str(p["ticker"]).strip().upper() for p in eligible_profiles if p.get("ticker")]

    if eligible_n > MAX_CANDIDATE_BATCH:
        # Pre-load PIT market caps across eligible tickers so cap sorting is strictly by market cap, never alphabetical
        mcaps = await ctx.core_reader.fetch_pit_market_caps(ctx.as_of_date, tickers=all_elig_tickers)
        eligible_profiles.sort(
            key=lambda p: (
                0 if str(p.get("ticker") or "").upper() == anchor else 1,
                0 if str(p.get("ticker") or "").upper() in ctx.available_tickers else 1,
                -float(mcaps.get(str(p.get("ticker") or "").upper()) or p.get("market_cap") or 0.0),
            )
        )
        batch_profiles = eligible_profiles[:MAX_CANDIDATE_BATCH]
        batch_tickers = [str(p["ticker"]).strip().upper() for p in batch_profiles if p.get("ticker")]
        if anchor and anchor not in batch_tickers:
            batch_tickers.insert(0, anchor)
        fs = await _fact_set(ctx, batch_tickers, lookback_years=2 if len(batch_tickers) > 30 else 6)
    else:
        eligible_profiles.sort(
            key=lambda p: (
                0 if str(p.get("ticker") or "").upper() == anchor else 1,
                0 if str(p.get("ticker") or "").upper() in ctx.available_tickers else 1,
                -float(p.get("market_cap") or 0.0),
            )
        )
        batch_profiles = eligible_profiles
        batch_tickers = list(all_elig_tickers)
        if anchor and anchor not in batch_tickers:
            batch_tickers.insert(0, anchor)
        fs, mcaps = await asyncio.gather(
            _fact_set(ctx, batch_tickers, lookback_years=2 if len(batch_tickers) > 30 else 6),
            ctx.core_reader.fetch_pit_market_caps(ctx.as_of_date, tickers=batch_tickers),
        )

    metric_keys = list(
        dict.fromkeys(
            [c.metric for c in cand_spec.criteria]
            + ([cand_spec.sort_by] if cand_spec.sort_by else [])
            + ["roe", "pe", "pb"]
        )
    )

    anchor_vals: dict[str, float | None] = {}
    anchor_units: dict[str, str] = {}
    anchor_periods: dict[str, str] = {}
    summary_lines: list[str] = []
    if eligible_n > MAX_CANDIDATE_BATCH:
        has_mcap = sum(
            1
            for p in eligible_profiles
            if (mcaps.get(str(p.get("ticker") or "").upper()) or float(p.get("market_cap") or 0.0)) > 0
        )
        order_basis = (
            "ưu tiên mã đã index rồi vốn hóa PIT lớn"
            if has_mcap >= eligible_n // 2
            else f"ưu tiên mã đã index rồi THỨ TỰ CHỮ CÁI vì {eligible_n - has_mcap}/{eligible_n} hồ sơ thiếu vốn hóa"
        )
        cap_note = f", giới hạn {MAX_CANDIDATE_BATCH} mã ({order_basis}) — các mã ngoài giới hạn CHƯA được xét"
    else:
        cap_note = ""
    summary_lines.append(
        f"Phạm vi định vị: đã đánh giá {len(batch_tickers)}/{eligible_n} mã đủ điều kiện ngành & quyền truy cập "
        f"(nguồn {profile_source}{cap_note})."
    )
    if anchor and anchor in fs.templates:
        anc_inputs: dict[str, Any] = {}
        anc_parts: list[str] = []
        for mk in metric_keys:
            val, unit, plbl = eval_candidate_metric(fs, anchor, mk, mcaps)
            anchor_vals[mk] = val
            anchor_units[mk] = unit
            anchor_periods[mk] = plbl
            if val is not None:
                anc_inputs[mk] = {"value": val, "unit": unit or "NUMBER"}
                anc_parts.append(f"{mk}={fmt_value(val, unit)} ({plbl})")
        saved_anc = await ctx.evidence.register_calculator_trace(
            {
                "formula": "anchor_benchmark",
                "description": f"Chuẩn đối chiếu từ mã gốc {anchor} (PIT {ctx.as_of_date.isoformat()})",
                "inputs": anc_inputs,
                "result": None,
                "formatted": "; ".join(anc_parts),
                "ticker": anchor,
                "format_style": "number",
                "source": "ANCHOR_BENCHMARK",
            }
        )
        summary_lines.append(
            f"[{saved_anc['calc_id']}] Chuẩn mã gốc {anchor}: " + (", ".join(anc_parts) or "chưa đủ số liệu")
        )

    prof_by_tk = {str(p["ticker"]).strip().upper(): p for p in batch_profiles}
    scored_candidates: list[dict[str, Any]] = []
    for tk in batch_tickers:
        if tk == anchor or tk not in fs.templates:
            continue
        vals: dict[str, float | None] = {}
        units: dict[str, str] = {}
        periods: dict[str, str] = {}
        for mk in metric_keys:
            v, u, plbl = eval_candidate_metric(fs, tk, mk, mcaps)
            vals[mk] = v
            units[mk] = u
            periods[mk] = plbl
        passed = 0
        crit_notes: list[str] = []
        for cr in cand_spec.criteria:
            ok = _check_criterion(vals.get(cr.metric), cr, anchor_vals.get(cr.metric))
            if ok:
                passed += 1
            u_cr = units.get(cr.metric, "")
            if cr.ref == "anchor":
                tol_txt = f" ± dung sai {fmt_value(cr.tolerance, u_cr)}" if cr.tolerance else ""
                ref_txt = f"{anchor} {fmt_value(anchor_vals.get(cr.metric), u_cr)}{tol_txt}"
            else:
                ref_txt = fmt_value(cr.value, u_cr)
            mark = "ĐẠT" if ok else "CHƯA ĐẠT"
            crit_notes.append(
                f"{cr.metric}={fmt_value(vals.get(cr.metric), u_cr)} [{periods.get(cr.metric, '-')}] "
                f"({cr.op} {ref_txt}: {mark})"
            )
        if cand_spec.criteria and passed == 0:
            continue
        p_info = prof_by_tk.get(tk, {})
        scored_candidates.append(
            {
                "ticker": tk,
                "company_name": p_info.get("company_name") or tk,
                "sector": p_info.get("industry") or p_info.get("sector") or "",
                "template": fs.templates[tk].value,
                "passed_count": passed,
                "total_criteria": len(cand_spec.criteria),
                "all_passed": passed == len(cand_spec.criteria),
                "metrics": vals,
                "units": units,
                "criteria_notes": crit_notes,
                "has_indexed_filings": tk in ctx.available_tickers,
            }
        )

    sort_key = cand_spec.sort_by or (cand_spec.criteria[-1].metric if cand_spec.criteria else "roe")
    reverse_sort = cand_spec.sort_dir != "asc"

    def _rank_tuple(row: Mapping[str, Any]) -> tuple[int, int, float]:
        sv = row["metrics"].get(sort_key)
        if sv is None:
            num_score = float("-inf")
        else:
            num_score = float(sv) if reverse_sort else -float(sv)
        return (1 if row["all_passed"] else 0, int(row["passed_count"]), num_score)

    scored_candidates.sort(key=_rank_tuple, reverse=True)
    strict_matches = [r for r in scored_candidates if r["all_passed"]]
    top_rows = (strict_matches if strict_matches else scored_candidates)[: cand_spec.limit]

    # Register screening rows + [CALC-*] traces so Stage 7 verifies every candidate number
    scr_payload: list[dict[str, Any]] = []
    for r in top_rows:
        tk = r["ticker"]
        m_vals = r["metrics"]
        m_units = r["units"]
        saved_c = await ctx.evidence.register_calculator_trace(
            {
                "formula": "candidate_locator_metrics",
                "description": f"Chỉ tiêu định vị ứng viên {tk} (PIT {ctx.as_of_date.isoformat()})",
                "inputs": {
                    k: {"value": v, "unit": m_units.get(k) or "NUMBER"}
                    for k, v in m_vals.items()
                    if v is not None
                },
                "result": m_vals.get(sort_key),
                "formatted": fmt_value(m_vals.get(sort_key), m_units.get(sort_key, "")),
                "ticker": tk,
                "format_style": "percent" if m_units.get(sort_key) == "RATIO" else "number",
                "source": "CANDIDATE_LOCATOR",
            }
        )
        r["calc_id"] = saved_c["calc_id"]
        status_tag = (
            f"Đạt đủ {r['passed_count']}/{r['total_criteria']} tiêu chí"
            if r["all_passed"]
            else f"Đạt {r['passed_count']}/{r['total_criteria']} tiêu chí (gần nhất)"
        )
        summary_lines.append(
            f"[SCR:{tk}] [{saved_c['calc_id']}] {tk} ({r['company_name']} - {r['sector']}): "
            f"{status_tag} | " + "; ".join(r["criteria_notes"])
        )
        scr_payload.append(
            {
                "ticker": tk,
                "company_name": r["company_name"],
                "sector": r["sector"],
                "roe": m_vals.get("roe"),
                "pe": m_vals.get("pe"),
                "pb": m_vals.get("pb"),
                "valid_from": ctx.as_of_date.isoformat(),
                "source_kind": "CORE_FACTSET_LOCATOR",
            }
        )

    await ctx.evidence.register_screening(
        scr_payload,
        f"Định vị ứng viên trên {len(batch_tickers)} mã Core PG (khớp tuyệt đối: {len(strict_matches)} mã)",
    )

    # Optional open-universe qualitative disclosure search across the 24 filing-indexed tickers
    if cand_spec.qualitative_query and ctx.available_tickers:
        disc_res = await _execute_disclosure_search(
            tool_name="search_disclosures",
            ctx=ctx,
            raw_tickers=sorted(ctx.available_tickers),
            dense_queries=[cand_spec.qualitative_query],
            vas_terms=[cand_spec.qualitative_query],
            slots_filter=["BCTN_CORE", "AGM_TARGETS", "QUARTER_NOTES"],
            years_filter=[],
            quarters_filter=[],
            include_cross_ref=False,
            top_k=5,
        )
        for doc in disc_res.details.get("selected_documents") or ():
            dtk = str(doc.get("ticker") or "").upper()
            if dtk and dtk != anchor and all(r["ticker"] != dtk for r in top_rows) and len(top_rows) < cand_spec.limit:
                top_rows.append(
                    {
                        "ticker": dtk,
                        "company_name": dtk,
                        "sector": "",
                        "template": "CORPORATE",
                        "passed_count": 1,
                        "total_criteria": 1,
                        "all_passed": True,
                        "metrics": {},
                        "units": {},
                        "criteria_notes": [f"Khớp thuyết minh [{doc.get('citation_id')}]"],
                        "has_indexed_filings": True,
                    }
                )

    picked_tickers = [r["ticker"] for r in top_rows]
    analysis_tickers = list(dict.fromkeys(([anchor] if anchor else []) + picked_tickers))[:6]
    no_pdf = [t for t in analysis_tickers if ctx.available_tickers and t not in ctx.available_tickers]
    cov_note = (
        f"Các mã {', '.join(no_pdf)} được kiểm chứng trên BCTC định lượng Core PG; "
        f"hiện kho thuyết minh PDF chi tiết mới phủ {len(ctx.available_tickers)} mã đã index."
        if no_pdf
        else None
    )
    if not top_rows:
        summary_lines.append("Không tìm thấy ứng viên nào thỏa tiêu chí định lượng trong phạm vi PIT hiện tại.")

    return LocatedTargets(
        analysis_tickers=analysis_tickers,
        anchor_ticker=anchor,
        anchor_metrics=anchor_vals,
        candidate_rows=top_rows,
        summary_lines=summary_lines,
        coverage_note=cov_note,
    )


async def _run_quant_specs_for_claim(
    *,
    claim: ClaimSpec,
    tickers: Sequence[str],
    fs: FactSet | None,
    located: LocatedTargets,
    ctx: ResearchToolContext,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_stage: Callable[[str, str, str], Awaitable[None]] | None = None,
) -> tuple[list[str], list[str], str | None]:
    """Execute all 0-token deterministic specs (`fact:*`, `kpi:*`, `risk`, `peers`, `agm`, `kg`, `screen`, `table:*`).

    Returns `(summary_lines, badges, anomaly_escalation_query_or_None)`.
    """
    t_quant_start = time.perf_counter()
    lines: list[str] = []
    badges: list[str] = []
    escalation_hint: str | None = None

    fact_items = [ev.split(":", 1)[1] for ev in claim.evidence if ev.startswith("fact:")]
    kpi_items = [ev.split(":", 1)[1] for ev in claim.evidence if ev.startswith("kpi:")]
    wants_risk = "risk" in claim.evidence
    wants_peers = "peers" in claim.evidence
    wants_agm = "agm" in claim.evidence
    wants_screen = "screen" in claim.evidence
    kg_specs = [ev for ev in claim.evidence if ev == "kg" or ev.startswith("kg:")]
    table_specs = [ev for ev in claim.evidence if ev.startswith("table:")]
    wants_mem = "mem" in claim.evidence
    has_fin_quant = bool(fact_items or kpi_items or wants_risk or wants_peers or wants_agm)
    quant_call_id = f"quant-{claim.id}"
    if has_fin_quant:
        primary_tool = (
            "check_plan_vs_actual"
            if wants_agm
            else "compare_peers"
            if wants_peers
            else "scan_financial_risks"
            if wants_risk and not fact_items and not kpi_items
            else "get_sector_kpis"
            if kpi_items and not fact_items
            else "get_financial_statements"
        )
        spec_preview_parts: list[str] = []
        if fact_items:
            spec_preview_parts.append(f"BCTC ({', '.join(fact_items[:4])})")
        if kpi_items:
            spec_preview_parts.append(f"KPI ({', '.join(kpi_items[:4])})")
        if wants_risk:
            spec_preview_parts.append("Quét cờ rủi ro")
        if wants_peers:
            spec_preview_parts.append("So sánh cùng ngành")
        if wants_agm:
            spec_preview_parts.append("KH ĐHĐCĐ vs Thực hiện")
        if on_stage is not None:
            await on_stage("calculator", "running", f"Đang kiểm chứng số liệu định lượng ({claim.id})...")
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": quant_call_id,
                    "call_id": quant_call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": primary_tool,
                    "label": f"Kiểm chứng số liệu BCTC & KPI ({claim.id})",
                    "stage": "calculator",
                    "status": "running",
                    "tickers": list(tickers[:6]),
                    "args_preview": " · ".join(spec_preview_parts),
                }
            )

    if wants_screen and located.summary_lines:
        lines.extend(located.summary_lines[:6])
        for r in located.candidate_rows:
            badges.append(f"[SCR:{r['ticker']}]")
            if r.get("calc_id"):
                badges.append(f"[{r['calc_id']}]")

    if wants_mem:
        for bid, mem in list(ctx.evidence.recalled_memories.items())[:4]:
            lines.append(f"[MEM:{bid}] {mem.get('content_text', '')}")
            badges.append(f"[MEM:{bid}]")
        for sym, pos in list(ctx.evidence.portfolio_snapshots.items())[:4]:
            lines.append(
                f"[PORT:{sym}] Tỷ trọng={pos.get('weight_pct')}% | Giá vốn={pos.get('avg_cost')} | "
                f"Lãi/lỗ={pos.get('unrealized_pnl_pct')}%"
            )
            badges.append(f"[PORT:{sym}]")

    if fs is not None and tickers:
        # 1) Facts + accounting-safe composite totals (prevents mental addition/subtraction errors!)
        if fact_items:
            for tk in tickers:
                if tk not in fs.templates:
                    continue
                tmpl = fs.templates[tk]
                applicable = {it.item_id for it in items_for_template(tmpl)}
                fact_objs = []
                item_summaries: list[str] = []
                for item_id in fact_items:
                    if item_id not in applicable:
                        continue
                    spec = CATALOG_BY_ID[item_id]
                    cf_note = ""
                    if spec.kind in ("STOCK", "VENDOR_RATIO"):
                        q_series = fs.series(tk, item_id, "Q")[-3:]
                        fy_series = fs.series(tk, item_id, "FY")[-2:]
                        chosen = q_series if q_series else fy_series
                    else:
                        raw_q = fs.series(tk, item_id, "Q")[-3:]
                        # Vendor quarterly cash flows are sparse/YTD-cumulative: never show them as single quarters
                        q_series = [f for f in raw_q if FLAG_QUARTERLY_CF_UNRELIABLE not in f.flags]
                        if len(q_series) < len(raw_q):
                            cf_note = (
                                " (Lưu ý: số quý của dòng tiền từ nhà cung cấp thưa/lũy kế nên đã loại; "
                                "chỉ dùng số năm FY)"
                            )
                        fy_series = fs.series(tk, item_id, "FY")[-2:]
                        ttm_series = fs.series(tk, item_id, "TTM")[-1:]
                        chosen = [*q_series, *fy_series[-1:], *ttm_series]
                    if not chosen:
                        item_summaries.append(f"{spec.label_vi}: chưa có dữ liệu PIT")
                        continue
                    fact_objs.extend(chosen)
                    pts = ", ".join(f"{f.period_label}={fmt_value(f.value, f.unit)}" for f in chosen[-3:])
                    note_guard = ""
                    if item_id == "st_borrowings":
                        note_guard = (
                            " (Lưu ý kế toán: chỉ tiêu này ĐÃ là TỔNG vay & nợ thuê tài chính ngắn hạn trên CĐKT, "
                            "đã bao gồm nợ dài hạn đến hạn trả — KHÔNG cộng thêm các khoản mục con từ thuyết minh)"
                        )
                    item_summaries.append(f"{spec.label_vi}: {pts}{note_guard}{cf_note}")

                # Accounting-safe deterministic composites so LLM never mentally adds/subtracts/divides
                lq = fs.latest_period(tk, "Q")
                common_size_inputs: dict[str, dict[str, Any]] = {}
                if lq is not None:
                    fy_q, fq_q = lq
                    ta_r = fs.get(tk, "total_assets", "Q", fy_q, fq_q)
                    if ta_r.status is LookupStatus.OK and ta_r.fact is not None and ta_r.fact.value > 0:
                        ta_val = ta_r.fact.value
                        cs_parts: list[str] = []
                        for bs_item in (
                            "st_receivables",
                            "lt_receivables",
                            "inventory",
                            "cash_and_equivalents",
                            "st_borrowings",
                            "lt_borrowings",
                        ):
                            if bs_item not in fact_items or bs_item not in applicable:
                                continue
                            bs_r = fs.get(tk, bs_item, "Q", fy_q, fq_q)
                            if bs_r.status is LookupStatus.OK and bs_r.fact is not None:
                                share = bs_r.fact.value / ta_val
                                common_size_inputs[f"{bs_item}_to_assets@Q{fq_q}/{fy_q}"] = {
                                    "value": share,
                                    "unit": "RATIO",
                                }
                                bs_lbl = CATALOG_BY_ID[bs_item].label_vi
                                cs_parts.append(f"{bs_lbl}/Tổng TS={fmt_value(share, 'RATIO')}")
                        if cs_parts:
                            item_summaries.append(f"Tỷ trọng trên Tổng tài sản Q{fq_q}/{fy_q}: " + ", ".join(cs_parts))
                    # Total interest-bearing debt = st_borrowings + lt_borrowings
                    if "st_borrowings" in fact_items or "lt_borrowings" in fact_items:
                        st_r = fs.get(tk, "st_borrowings", "Q", fy_q, fq_q)
                        lt_r = fs.get(tk, "lt_borrowings", "Q", fy_q, fq_q)
                        if st_r.status is LookupStatus.OK and st_r.fact is not None:
                            lt_v = lt_r.fact.value if (lt_r.status is LookupStatus.OK and lt_r.fact) else 0.0
                            tot_d = st_r.fact.value + lt_v
                            saved_td = await ctx.evidence.register_calculator_trace(
                                {
                                    "formula": "st_borrowings + lt_borrowings",
                                    "description": f"Tổng nợ vay chịu lãi (ngắn hạn + dài hạn) {tk} Q{fq_q}/{fy_q}",
                                    "inputs": {
                                        f"st_borrowings@Q{fq_q}/{fy_q}": {"value": st_r.fact.value, "unit": "VND"},
                                        f"lt_borrowings@Q{fq_q}/{fy_q}": {"value": lt_v, "unit": "VND"},
                                    },
                                    "result": tot_d,
                                    "formatted": fmt_value(tot_d, "VND"),
                                    "ticker": tk,
                                    "format_style": "number",
                                    "source": "DETERMINISTIC_COMPOSITE",
                                }
                            )
                            badges.append(f"[{saved_td['calc_id']}]")
                            item_summaries.append(
                                f"[{saved_td['calc_id']}] Tổng nợ vay chịu lãi {tk} Q{fq_q}/{fy_q} "
                                f"(vay ngắn hạn {fmt_value(st_r.fact.value, 'VND')} + vay dài hạn "
                                f"{fmt_value(lt_v, 'VND')}): {fmt_value(tot_d, 'VND')}"
                            )
                    # Liquidity context for short-term debt: cash alone hides term deposits (st_investments)
                    if "st_borrowings" in fact_items and "st_investments" in applicable:
                        c_r = fs.get(tk, "cash_and_equivalents", "Q", fy_q, fq_q)
                        i_r = fs.get(tk, "st_investments", "Q", fy_q, fq_q)
                        sd_r = fs.get(tk, "st_borrowings", "Q", fy_q, fq_q)
                        if (
                            c_r.status is LookupStatus.OK
                            and c_r.fact is not None
                            and sd_r.status is LookupStatus.OK
                            and sd_r.fact is not None
                        ):
                            inv_v = i_r.fact.value if (i_r.status is LookupStatus.OK and i_r.fact) else 0.0
                            liquid_v = c_r.fact.value + inv_v
                            cover = liquid_v / sd_r.fact.value if sd_r.fact.value > 0 else None
                            saved_lq = await ctx.evidence.register_calculator_trace(
                                {
                                    "formula": "(cash_and_equivalents + st_investments) / st_borrowings",
                                    "description": (
                                        f"Tiền + đầu tư tài chính ngắn hạn so với vay ngắn hạn {tk} Q{fq_q}/{fy_q}"
                                    ),
                                    "inputs": {
                                        f"cash_and_equivalents@Q{fq_q}/{fy_q}": {
                                            "value": c_r.fact.value,
                                            "unit": "VND",
                                        },
                                        f"st_investments@Q{fq_q}/{fy_q}": {"value": inv_v, "unit": "VND"},
                                        "liquid_assets": {"value": liquid_v, "unit": "VND"},
                                        f"st_borrowings@Q{fq_q}/{fy_q}": {"value": sd_r.fact.value, "unit": "VND"},
                                    },
                                    "result": cover,
                                    "formatted": fmt_value(cover, "X") if cover is not None else "",
                                    "ticker": tk,
                                    "format_style": "ratio",
                                    "source": "DETERMINISTIC_COMPOSITE",
                                }
                            )
                            badges.append(f"[{saved_lq['calc_id']}]")
                            cover_txt = f" = {fmt_value(cover, 'X')} vay ngắn hạn" if cover is not None else ""
                            item_summaries.append(
                                f"[{saved_lq['calc_id']}] Thanh khoản sẵn có {tk} Q{fq_q}/{fy_q}: tiền "
                                f"{fmt_value(c_r.fact.value, 'VND')} + đầu tư tài chính ngắn hạn "
                                f"{fmt_value(inv_v, 'VND')} = {fmt_value(liquid_v, 'VND')}{cover_txt}"
                            )
                    # Securities net FVTPL = fvtpl_gains - fvtpl_losses (prevents gain/loss sign flip!)
                    if tmpl is Template.SECURITIES and ("fvtpl_gains" in fact_items or "fvtpl_losses" in fact_items):
                        g_r = fs.get(tk, "fvtpl_gains", "Q", fy_q, fq_q)
                        l_r = fs.get(tk, "fvtpl_losses", "Q", fy_q, fq_q)
                        if g_r.status is LookupStatus.OK and l_r.status is LookupStatus.OK and g_r.fact and l_r.fact:
                            net_fvtpl = g_r.fact.value - l_r.fact.value
                            sign_lbl = "LÃI RÒNG" if net_fvtpl >= 0 else "LỖ RÒNG"
                            saved_fv = await ctx.evidence.register_calculator_trace(
                                {
                                    "formula": "fvtpl_gains - fvtpl_losses",
                                    "description": f"Kết quả thuần tự doanh FVTPL ({sign_lbl}) {tk} Q{fq_q}/{fy_q}",
                                    "inputs": {
                                        "fvtpl_gains": {"value": g_r.fact.value, "unit": "VND"},
                                        "fvtpl_losses": {"value": l_r.fact.value, "unit": "VND"},
                                    },
                                    "result": net_fvtpl,
                                    "formatted": fmt_value(net_fvtpl, "VND"),
                                    "ticker": tk,
                                    "format_style": "number",
                                    "source": "DETERMINISTIC_COMPOSITE",
                                }
                            )
                            badges.append(f"[{saved_fv['calc_id']}]")
                            item_summaries.append(
                                f"[{saved_fv['calc_id']}] Kết quả thuần FVTPL Q{fq_q}/{fy_q}: {sign_lbl} "
                                f"{fmt_value(net_fvtpl, 'VND')} (Lãi FVTPL {fmt_value(g_r.fact.value, 'VND')} "
                                f"trừ Lỗ FVTPL {fmt_value(l_r.fact.value, 'VND')})"
                            )

                if fact_objs:
                    trace_inputs: dict[str, Any] = {
                        f"{f.item}@{f.period_label}": {"value": f.value, "unit": f.unit}
                        for f in fact_objs
                    }
                    trace_inputs.update(common_size_inputs)
                    saved_fs = await ctx.evidence.register_calculator_trace(
                        {
                            "formula": "fact_sheet_slice",
                            "description": f"Số liệu BCTC {tk} cho ý {claim.id} (PIT {ctx.as_of_date.isoformat()})",
                            "inputs": trace_inputs,
                            "result": None,
                            "formatted": "",
                            "ticker": tk,
                            "format_style": "number",
                            "source": "CORE_PG_FACTS",
                        }
                    )
                    cid = str(saved_fs["calc_id"])
                    badges.append(f"[{cid}]")
                    for s_ln in item_summaries:
                        lines.append(f"[{cid}] {tk} · {s_ln}" if not s_ln.startswith("[CALC-") else s_ln)
                elif item_summaries:
                    for s_ln in item_summaries:
                        lines.append(f"{tk} · {s_ln}")

        # 2) KPIs
        if kpi_items:
            for tk in tickers:
                if tk not in fs.templates:
                    continue
                p_ref = _resolve_period_ref(fs, tk, claim.period)
                if p_ref is None:
                    continue
                for kid in kpi_items:
                    if kid not in KPI_BY_ID:
                        continue
                    kv = compute_kpi(fs, tk, kid, p_ref)
                    if kv.status is KpiStatus.OK and kv.value is not None:
                        cid = await _register_kpi(ctx, kv)
                        badges.append(f"[{cid}]")
                        extra = f" ({kv.detail})" if kv.detail else ""
                        lines.append(
                            f"[{cid}] {tk} · {kv.label_vi} ({kv.period}): "
                            f"{fmt_value(kv.value, kv.unit)}{extra}"
                        )
                    elif kv.status is KpiStatus.INSUFFICIENT_DATA:
                        lines.append(f"{tk} · {kv.label_vi}: thiếu dữ liệu ({kv.detail})")

        # 3) Deterministic Risk & Earnings Quality Scan
        if wants_risk:
            for tk in tickers:
                if tk not in fs.templates:
                    continue
                eq = earnings_quality(fs, tk)
                rf = red_flag_scan(fs, tk)
                seen_codes: set[str] = set()
                tk_flags: list[str] = []
                for fl in [*rf.get("flags", []), *eq.get("signals", [])]:
                    if fl["code"] in seen_codes:
                        continue
                    seen_codes.add(fl["code"])
                    obs = fl.get("observed")
                    cid_tag = ""
                    if isinstance(obs, int | float):
                        saved_fl = await ctx.evidence.register_calculator_trace(
                            {
                                "formula": f"rule:{fl['code']}",
                                "description": f"{fl['message_vi']} ({tk} {fl['period']})",
                                "inputs": {
                                    f"ev_{i}": {"value": e.get("value")}
                                    for i, e in enumerate(fl.get("evidence", []))
                                    if isinstance(e, Mapping) and isinstance(e.get("value"), int | float)
                                },
                                "result": float(obs),
                                "formatted": f"{float(obs):.4g}",
                                "ticker": tk,
                                "format_style": "number",
                                "source": "DETERMINISTIC_RULE_ENGINE",
                            }
                        )
                        cid_tag = f"[{saved_fl['calc_id']}] "
                        badges.append(f"[{saved_fl['calc_id']}]")
                    sev_label = {
                        "HIGH": "Cảnh báo rủi ro cao",
                        "MEDIUM": "Cảnh báo rủi ro trung bình",
                        "LOW": "Lưu ý dữ liệu",
                    }.get(str(fl.get("severity") or "").upper(), "Lưu ý")
                    tk_flags.append(f"{cid_tag}{sev_label}: {fl['message_vi']}")
                    if fl["severity"] in ("HIGH", "MEDIUM") and escalation_hint is None:
                        escalation_hint = _flag_to_vas_followup(fl["code"])
                if tk_flags:
                    lines.extend(f"{tk} · {f_ln}" for f_ln in tk_flags[:4])
                else:
                    lines.append(f"{tk} · Quét rủi ro tất định: không phát hiện cờ cảnh báo trọng yếu.")

        # 4) Peer comparison
        if wants_peers and len(tickers) >= 2:
            pc = peer_compare(fs, tickers, kpi_items or None)
            if pc.get("status") == "OK":
                for kid, row in list(pc["kpis"].items())[:6]:
                    row_parts: list[str] = []
                    lbl = kid
                    for tk in pc["tickers"]:
                        v = row.get(tk)
                        if not v or v.get("status") != "OK":
                            continue
                        lbl = v["label_vi"]
                        kv_obj = KpiValue(
                            kpi_id=v["kpi_id"],
                            label_vi=v["label_vi"],
                            ticker=tk,
                            period=v["period"],
                            value=v["value"],
                            unit=v["unit"],
                            formula=v["formula"],
                            status=KpiStatus.OK,
                            inputs=tuple(v["inputs"]),
                            flags=tuple(v["flags"]),
                        )
                        cid = await _register_kpi(ctx, kv_obj)
                        badges.append(f"[{cid}]")
                        rk = f" (#{v['rank']})" if "rank" in v else ""
                        row_parts.append(f"{tk}={fmt_value(v['value'], v['unit'])}{rk} [{cid}]")
                    if row_parts:
                        lines.append(f"So sánh {lbl}: " + " | ".join(row_parts))

        # 5) AGM Plan vs Actual
        if wants_agm:
            fy_plan = ctx.as_of_date.year
            for tk in tickers[:3]:
                if tk not in fs.templates:
                    continue
                tmpl = fs.templates[tk]
                applicable = {it.item_id for it in items_for_template(tmpl)}
                prior = {}
                for it in ("revenue_net", "pbt", "npat", "npat_parent"):
                    r = fs.get(tk, it, "FY", fy_plan - 1)
                    if r.status is LookupStatus.OK and r.fact is not None:
                        prior[it] = r.fact.value
                ex = extract_plan_targets(await _agm_tables(ctx, tk), fy_plan, prior_actuals=prior)
                targets = [
                    PlanTarget(it, cand.value, f"AGM_TARGETS {cand.table_id} tr.{cand.page}")
                    for it, cand in ex.targets.items()
                    if it in applicable
                ]
                if not targets:
                    lines.append(f"{tk} · Không có bảng kế hoạch ĐHĐCĐ năm {fy_plan} hợp lệ trong PIT.")
                    continue
                pva = plan_vs_actual(fs, tk, fy_plan, targets)
                for row in pva.get("rows", []):
                    if row.get("status") != "OK":
                        continue
                    lbl = CATALOG_BY_ID[row["item"]].label_vi
                    saved_p = await ctx.evidence.register_calculator_trace(
                        {
                            "formula": "ytd_actual / plan_target",
                            "description": f"% hoàn thành KH {lbl} {tk} {fy_plan}",
                            "inputs": {
                                f"plan:{row['item']}": {"value": row["target"], "unit": "VND"},
                                f"ytd:{row['item']}": {"value": row["ytd_actual"], "unit": "VND"},
                            },
                            "result": row["achievement"],
                            "formatted": f"{row['achievement'] * 100:.1f}%",
                            "ticker": tk,
                            "format_style": "percent",
                            "source": "DETERMINISTIC_PLAN_ENGINE",
                        }
                    )
                    badges.append(f"[{saved_p['calc_id']}]")
                    lines.append(
                        f"[{saved_p['calc_id']}] {tk} · KH {lbl} {fy_plan}: mục tiêu "
                        f"{fmt_value(row['target'], 'VND')} ({row['target_source']}), lũy kế {row['ytd_period']} "
                        f"đạt {fmt_value(row['ytd_actual'], 'VND')} ({row['achievement'] * 100:.1f}% KH, "
                        f"tiến độ {row['pace']})."
                    )

    if has_fin_quant and on_tool_result is not None:
        q_dur = int((time.perf_counter() - t_quant_start) * 1000)
        calc_badges = [b.strip("[]") for b in badges if b.startswith("[CALC-")]
        latest_calc_preview: dict[str, Any] | None = None
        if calc_badges and ctx.evidence.calculator_traces:
            first_cid = calc_badges[0]
            matched_tr = next(
                (t for t in ctx.evidence.calculator_traces if str(t.get("calc_id") or "") == first_cid),
                None,
            )
            if matched_tr and matched_tr.get("formatted"):
                latest_calc_preview = {
                    "calc_id": first_cid,
                    "formula": str(matched_tr.get("formula") or ""),
                    "description": str(matched_tr.get("description") or ""),
                    "formatted": str(matched_tr.get("formatted") or ""),
                }
        await on_tool_result(
            {
                "id": quant_call_id,
                "call_id": quant_call_id,
                "type": "tool_call",
                "turn": 1,
                "tool": primary_tool,
                "label": f"Kiểm chứng số liệu BCTC & KPI ({claim.id})",
                "stage": "calculator",
                "status": "done",
                "duration_ms": q_dur,
                "returned_count": len(lines),
                "summary": (
                    f"Trích xuất {len(lines)} dòng số liệu tất định"
                    + (f" ({', '.join(calc_badges[:3])})" if calc_badges else "")
                ),
                "calc_result": latest_calc_preview,
            }
        )
        if on_stage is not None:
            await on_stage("calculator", "done", f"Hoàn tất kiểm chứng định lượng ({claim.id})")

    # 6) Knowledge Graph 3-layer query (`0` LLM tokens)
    if kg_specs and ctx.enable_kg and tickers:
        async def _noop(_p: Any) -> None:
            return None

        kg_call_id = f"kg-{claim.id}"
        tctx = ToolContext(
            run_id="brief-kg",
            tool_call_id=kg_call_id,
            data=ctx,
            cancellation=CancellationToken(),
            _on_progress=_noop,
        )
        macro_hint = kg_specs[0].split(":", 1)[1].strip() if ":" in kg_specs[0] else ""
        kg_args: dict[str, Any] = {"tickers": list(tickers[:4])}
        if macro_hint:
            kg_args["macro_factor"] = macro_hint
        if on_stage is not None:
            await on_stage("graph_hop", "running", f"Đang truy vấn đồ thị 3 tầng ({claim.id})...")
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": kg_call_id,
                    "call_id": kg_call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "query_knowledge_graph",
                    "label": f"Đồ thị tri thức 3 tầng ({claim.id})",
                    "stage": "graph_hop",
                    "status": "running",
                    "tickers": list(tickers[:4]),
                    "args_preview": f"Mã {', '.join(tickers[:4])}" + (f" · vĩ mô `{macro_hint}`" if macro_hint else ""),
                }
            )
        t_kg = time.perf_counter()
        try:
            kg_res = await query_knowledge_graph_tool.execute(kg_args, tctx)
            hops_list = list(kg_res.details.get("kg_hops") or ())[:5]
            for h in hops_list:
                b_tag = f"[KG:{h.get('layer')}|{h.get('verification_tier')}]"
                badges.append(b_tag)
                lines.append(f"{b_tag} {h.get('src_label')} --({h.get('relation_type')})--> {h.get('dst_label')}")
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": kg_call_id,
                        "call_id": kg_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "query_knowledge_graph",
                        "label": f"Đồ thị tri thức 3 tầng ({claim.id})",
                        "stage": "graph_hop",
                        "status": "done",
                        "duration_ms": int((time.perf_counter() - t_kg) * 1000),
                        "returned_count": len(hops_list),
                        "summary": f"Truy xuất {len(hops_list)} liên kết đồ thị PIT",
                        "kg_hops": hops_list,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            lines.append("Đồ thị tri thức 3 tầng: chưa có liên kết PIT phù hợp.")
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": kg_call_id,
                        "call_id": kg_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "query_knowledge_graph",
                        "label": f"Đồ thị tri thức 3 tầng ({claim.id})",
                        "stage": "graph_hop",
                        "status": "error",
                        "duration_ms": int((time.perf_counter() - t_kg) * 1000),
                        "summary": f"Chưa có liên kết đồ thị PIT ({type(exc).__name__})",
                    }
                )
        if on_stage is not None:
            await on_stage("graph_hop", "done", f"Hoàn tất đồ thị ({claim.id})")

    # 7) Hydrated 2D PDF tables (`0` LLM tokens)
    if table_specs and tickers:
        async def _noop_t(_p: Any) -> None:
            return None

        stmt_kind = table_specs[0].split(":", 1)[1].strip() or "ALL"
        tbl_call_id = f"tbl-{claim.id}"
        tctx = ToolContext(
            run_id="brief-tbl",
            tool_call_id=tbl_call_id,
            data=ctx,
            cancellation=CancellationToken(),
            _on_progress=_noop_t,
        )
        if on_stage is not None:
            await on_stage("table_hydration", "running", f"Đang nạp bảng tài chính 2D ({claim.id})...")
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": tbl_call_id,
                    "call_id": tbl_call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "hydrate_financial_tables",
                    "label": f"Nạp bảng tài chính 2D ({claim.id})",
                    "stage": "table_hydration",
                    "status": "running",
                    "tickers": list(tickers[:3]),
                    "args_preview": f"Mã {', '.join(tickers[:3])} · loại `{stmt_kind}`",
                }
            )
        t_tbl = time.perf_counter()
        try:
            tbl_res = await hydrate_financial_tables_tool.execute(
                {"tickers": list(tickers[:3]), "statement_kind": stmt_kind},
                tctx,
            )
            tbl_list = list(tbl_res.details.get("hydrated_tables") or ())[:3]
            for tbl in tbl_list:
                tid = tbl.get("table_id")
                if tid:
                    badges.append(f"[TBL:{tid}]")
                    lines.append(
                        f"[TBL:{tid}] Bảng 2D {tbl.get('project_id')}: {tbl.get('title')} "
                        f"({tbl.get('verification_status')})"
                    )
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": tbl_call_id,
                        "call_id": tbl_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "hydrate_financial_tables",
                        "label": f"Nạp bảng tài chính 2D ({claim.id})",
                        "stage": "table_hydration",
                        "status": "done",
                        "duration_ms": int((time.perf_counter() - t_tbl) * 1000),
                        "returned_count": len(tbl_list),
                        "summary": f"Đã nạp {len(tbl_list)} bảng 2D kiểm toán",
                        "hydrated_tables": tbl_list,
                    }
                )
        except Exception as exc:  # noqa: BLE001
            if on_tool_result is not None:
                await on_tool_result(
                    {
                        "id": tbl_call_id,
                        "call_id": tbl_call_id,
                        "type": "tool_call",
                        "turn": 1,
                        "tool": "hydrate_financial_tables",
                        "label": f"Nạp bảng tài chính 2D ({claim.id})",
                        "stage": "table_hydration",
                        "status": "error",
                        "duration_ms": int((time.perf_counter() - t_tbl) * 1000),
                        "summary": f"Không nạp được bảng 2D ({type(exc).__name__})",
                    }
                )
        if on_stage is not None:
            await on_stage("table_hydration", "done", f"Hoàn tất nạp bảng 2D ({claim.id})")

    return lines, list(dict.fromkeys(badges)), escalation_hint


def _flag_to_vas_followup(flag_code: str) -> str:
    mapping = {
        "LOW_CASH_CONVERSION": "lưu chuyển tiền hoạt động kinh doanh phải thu tồn kho",
        "HIGH_ACCRUALS": "phải thu ngắn hạn giao dịch bên liên quan",
        "SHORT_TERM_REFINANCING_RISK": "vay và nợ thuê tài chính ngắn hạn kỳ hạn lãi suất",
        "HIGH_NET_LEVERAGE": "vay ngắn hạn vay dài hạn trái phiếu đến hạn trả",
        "RECEIVABLES_OUTPACE_REVENUE": "phải thu ngắn hạn khách hàng phải thu khác bên liên quan",
        "INVENTORY_OUTPACE_REVENUE": "hàng tồn kho dự phòng giảm giá hàng tồn kho",
        "NPL_ABOVE_3PCT": "chất lượng nợ cho vay nợ nhóm 3 nhóm 4 nhóm 5",
        "UNREALISED_PROFIT_DEPENDENCE": "tài sản tài chính FVTPL đánh giá lại danh mục tự doanh",
    }
    return mapping.get(flag_code, "thuyết minh báo cáo tài chính khoản mục trọng yếu")


def _evaluate_premise_direction(
    claim: ClaimSpec,
    fs: FactSet | None,
    tickers: Sequence[str],
) -> tuple[VerdictStatus | None, str | None, dict[str, Any] | None]:
    """Deterministically verify directional premises (e.g. 'NIM ACB sập', 'lãi tăng gấp đôi') on FactSet.

    Returns `(status, Vietnamese_summary_or_None, calc_trace_payload_or_None)` so every compared figure
    can be registered in `ctx.evidence` for Stage 7 numeric grounding.
    """
    if fs is None or not tickers:
        return None, None, None
    tk = tickers[0]
    if tk not in fs.templates:
        return None, None, None
    stmt = claim.statement.lower()
    neg_claim = bool(_NEGATIVE_PREMISE_RE.search(stmt))
    pos_claim = bool(_POSITIVE_PREMISE_RE.search(stmt))
    if not neg_claim and not pos_claim:
        return None, None, None

    # Vendor ratios often have a single quarter; fall back to the equivalent computed KPI series
    proxy_kpi = {"vr_nim": "nii_to_avg_assets", "vr_cir": "cir", "vr_ldr": "loan_to_deposit"}
    ev_to_check = list(claim.evidence)
    for ev in claim.evidence:
        if ev.startswith("fact:"):
            item_id = ev.split(":", 1)[1]
            proxy = proxy_kpi.get(item_id)
            if proxy and len(fs.series(tk, item_id, "Q")) < 2 and f"kpi:{proxy}" not in ev_to_check:
                ev_to_check.append(f"kpi:{proxy}")

    # Check first fact or KPI in premise evidence that has >= 2 comparable periods
    for ev in ev_to_check:
        if ev.startswith("fact:"):
            item_id = ev.split(":", 1)[1]
            q_ser = fs.series(tk, item_id, "Q")
            if len(q_ser) < 2:
                continue
            cur, prev = q_ser[-1], q_ser[-2]
            lbl_vi = CATALOG_BY_ID[item_id].label_vi
            if "lỗ" in stmt or "âm" in stmt:
                if cur.value > 0:
                    payload = {
                        "formula": f"{item_id}({cur.period_label})",
                        "description": f"Kiểm chứng tiền đề lỗ/âm cho {lbl_vi} {tk} {cur.period_label}",
                        "inputs": {f"{item_id}@{cur.period_label}": {"value": cur.value, "unit": cur.unit}},
                        "result": cur.value,
                        "formatted": fmt_value(cur.value, cur.unit),
                        "ticker": tk,
                        "format_style": "percent" if cur.unit == "RATIO" else "number",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    return (
                        "REFUTED",
                        f"BÁC BỎ TIỀN ĐỀ: {lbl_vi} {tk} kỳ {cur.period_label} "
                        f"đạt dương {fmt_value(cur.value, cur.unit)} (không lỗ/âm).",
                        payload,
                    )
            if neg_claim and prev.value > 0:
                rel_chg = (cur.value - prev.value) / abs(prev.value)
                abs_chg = cur.value - prev.value
                # For percentage ratios like NIM (~0.035), a 'collapse' implies >= 35 bps or >=10% rel drop
                is_collapse = (
                    (abs_chg <= -0.0035 and rel_chg <= -0.10) if cur.unit == "RATIO" else (rel_chg <= -0.15)
                )
                payload = {
                    "formula": (
                        f"({item_id}@{cur.period_label} - {item_id}@{prev.period_label}) / abs({prev.period_label})"
                    ),
                    "description": (
                        f"So sánh tiền đề giảm mạnh {lbl_vi} {tk} ({prev.period_label} -> {cur.period_label})"
                    ),
                    "inputs": {
                        f"{item_id}@{cur.period_label}": {"value": cur.value, "unit": cur.unit},
                        f"{item_id}@{prev.period_label}": {"value": prev.value, "unit": prev.unit},
                    },
                    "result": rel_chg,
                    "formatted": f"{rel_chg * 100:+.1f}%",
                    "ticker": tk,
                    "format_style": "percent",
                    "source": "PREMISE_DIRECTION_CHECK",
                }
                if not is_collapse:
                    return (
                        "REFUTED",
                        f"BÁC BỎ TIỀN ĐỀ: {lbl_vi} {tk} kỳ {cur.period_label} ở mức "
                        f"{fmt_value(cur.value, cur.unit)} so với kỳ liền trước {prev.period_label} là "
                        f"{fmt_value(prev.value, prev.unit)} (biến động {rel_chg * 100:+.1f}%, "
                        "KHÔNG sụt giảm mạnh / sập như tiền đề câu hỏi).",
                        payload,
                    )
                return ("CONFIRMED", None, payload)
            if pos_claim:
                # Prefer same-quarter prior year for growth claims ("tăng gấp đôi"), else previous quarter
                yoy_matches = [
                    f
                    for f in q_ser[:-1]
                    if f.fiscal_year == cur.fiscal_year - 1 and f.fiscal_quarter == cur.fiscal_quarter
                ]
                base = yoy_matches[-1] if yoy_matches else prev
                if base.value > 0:
                    rel_chg = (cur.value - base.value) / abs(base.value)
                    thresh = 0.80 if ("gấp đôi" in stmt or "bằng lần" in stmt) else 0.30
                    payload = {
                        "formula": (
                            f"({item_id}@{cur.period_label} - {item_id}@{base.period_label}) / abs({base.period_label})"
                        ),
                        "description": (
                            f"Kiểm chứng tiền đề tăng mạnh {lbl_vi} {tk} ({base.period_label} -> {cur.period_label})"
                        ),
                        "inputs": {
                            f"{item_id}@{cur.period_label}": {"value": cur.value, "unit": cur.unit},
                            f"{item_id}@{base.period_label}": {"value": base.value, "unit": base.unit},
                        },
                        "result": rel_chg,
                        "formatted": f"{rel_chg * 100:+.1f}%",
                        "ticker": tk,
                        "format_style": "percent",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    if rel_chg < thresh:
                        return (
                            "REFUTED",
                            f"BÁC BỎ TIỀN ĐỀ: {lbl_vi} {tk} kỳ {cur.period_label} đạt "
                            f"{fmt_value(cur.value, cur.unit)} so với {base.period_label} là "
                            f"{fmt_value(base.value, base.unit)} (biến động {rel_chg * 100:+.1f}%, "
                            "KHÔNG đạt mức tăng mạnh như tiền đề).",
                            payload,
                        )
                    return ("CONFIRMED", None, payload)
        if ev.startswith("kpi:"):
            kid = ev.split(":", 1)[1]
            lq = fs.latest_period(tk, "Q")
            if lq is not None and kid in KPI_BY_ID:
                cur_ref = PeriodRef("Q", lq[0], lq[1])
                cur_kv = compute_kpi(fs, tk, kid, cur_ref)
                if (
                    pos_claim
                    and kid.endswith("_growth_yoy")
                    and cur_kv.status is KpiStatus.OK
                    and cur_kv.value is not None
                ):
                    thresh = 0.80 if ("gấp đôi" in stmt or "bằng lần" in stmt) else 0.30
                    payload = {
                        "formula": f"{kid}({cur_ref.label})",
                        "description": f"Kiểm chứng tiền đề tăng trưởng {cur_kv.label_vi} {tk} {cur_ref.label}",
                        "inputs": {f"{kid}@{cur_ref.label}": {"value": cur_kv.value, "unit": cur_kv.unit}},
                        "result": cur_kv.value,
                        "formatted": fmt_value(cur_kv.value, cur_kv.unit),
                        "ticker": tk,
                        "format_style": "percent",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    if cur_kv.value < thresh:
                        return (
                            "REFUTED",
                            f"BÁC BỎ TIỀN ĐỀ: {cur_kv.label_vi} {tk} {cur_ref.label} đạt "
                            f"{fmt_value(cur_kv.value, cur_kv.unit)} (không đạt mức tăng như tiền đề).",
                            payload,
                        )
                    return ("CONFIRMED", None, payload)
                prev_q = (lq[0] - 1, 4) if lq[1] == 1 else (lq[0], lq[1] - 1)
                prev_ref = PeriodRef("Q", prev_q[0], prev_q[1])
                prev_kv = compute_kpi(fs, tk, kid, prev_ref)
                if (
                    cur_kv.status is KpiStatus.OK
                    and prev_kv.status is KpiStatus.OK
                    and cur_kv.value is not None
                    and prev_kv.value is not None
                    and neg_claim
                    and prev_kv.value > 0
                ):
                    rel_chg = (cur_kv.value - prev_kv.value) / abs(prev_kv.value)
                    payload = {
                        "formula": f"({kid}@{cur_ref.label} - {kid}@{prev_ref.label}) / abs({prev_ref.label})",
                        "description": (
                            f"So sánh tiền đề giảm mạnh {cur_kv.label_vi} {tk} ({prev_ref.label} -> {cur_ref.label})"
                        ),
                        "inputs": {
                            f"{kid}@{cur_ref.label}": {"value": cur_kv.value, "unit": cur_kv.unit},
                            f"{kid}@{prev_ref.label}": {"value": prev_kv.value, "unit": prev_kv.unit},
                        },
                        "result": rel_chg,
                        "formatted": f"{rel_chg * 100:+.1f}%",
                        "ticker": tk,
                        "format_style": "percent",
                        "source": "PREMISE_DIRECTION_CHECK",
                    }
                    if rel_chg > -0.12:
                        return (
                            "REFUTED",
                            f"BÁC BỎ TIỀN ĐỀ: {cur_kv.label_vi} {tk} {cur_ref.label} đạt "
                            f"{fmt_value(cur_kv.value, cur_kv.unit)} so với kỳ trước {prev_ref.label} là "
                            f"{fmt_value(prev_kv.value, prev_kv.unit)} ({rel_chg * 100:+.1f}%, không sập).",
                            payload,
                        )
                    return ("CONFIRMED", None, payload)
    return None, None, None


def _parse_doc_spec(doc_ev: str, default_period: str | None) -> tuple[list[str], list[int], list[int], str]:
    rest = doc_ev.split(":", 1)[1] if ":" in doc_ev else doc_ev
    slot_part, query_part = rest.split("|", 1) if "|" in rest else ("QUARTER_NOTES,BCTC_AUDITED", rest)
    slots = normalize_disclosure_slots([s.strip() for s in slot_part.split(",") if s.strip()])
    years: list[int] = []
    quarters: list[int] = []
    if default_period:
        qm = _PERIOD_Q_PARSE_RE.search(default_period)
        fym = _PERIOD_FY_PARSE_RE.search(default_period)
        if qm and "QUARTER_NOTES" in slots and len(slots) == 1:
            years = [int(qm.group(2))]
            quarters = [int(qm.group(1))]
        elif fym and "QUARTER_NOTES" not in slots:
            years = [int(fym.group(1))]
    return slots, years, quarters, query_part.strip()


async def _run_doc_mini_reader(
    *,
    claim: ClaimSpec,
    doc_specs: Sequence[str],
    tickers: Sequence[str],
    quant_context_lines: Sequence[str],
    ctx: ResearchToolContext,
    model: ModelProvider,
    reader_sem: asyncio.Semaphore,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    trace: TraceSink | None = None,
) -> tuple[VerdictStatus, list[str], list[str], str | None, Usage, int]:
    """Retrieve targeted `[C#]` sections for `claim` and run 1 compact Mini-Reader extraction call."""
    search_log: list[dict[str, Any]] = []

    def _trace_reader(**payload: Any) -> None:
        emit_trace(trace, "mini_reader", claim_id=claim.id, statement=claim.statement, searches=search_log, **payload)

    doc_tickers = [t for t in tickers if not ctx.available_tickers or t in ctx.available_tickers][:4]
    if not doc_tickers:
        no_cov = (
            f"Chưa có tài liệu thuyết minh PDF đã index trong Qdrant cho {', '.join(tickers) or 'phạm vi này'} "
            f"tại PIT {ctx.as_of_date.isoformat()}."
        )
        _trace_reader(outcome="NO_DOC_COVERAGE", status="NO_DATA", findings=[no_cov])
        return ("NO_DATA", [no_cov], [], None, Usage(), 0)

    gathered_cids: list[str] = []
    snippet_blocks: list[str] = []
    tool_calls = 0

    for idx, dspec in enumerate(doc_specs[:MAX_DOC_SPECS_PER_CLAIM], 1):
        slots, years, quarters, q_txt = _parse_doc_spec(dspec, claim.period)
        call_id = f"doc-{claim.id}-{idx}"
        if on_tool_call is not None:
            await on_tool_call(
                {
                    "id": call_id,
                    "call_id": call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "search_disclosures",
                    "label": f"Tra cứu thuyết minh ({claim.id})",
                    "stage": "retrieval",
                    "status": "running",
                    "tickers": doc_tickers,
                    "queries": [q_txt],
                    "sub_doc_slots": slots,
                    "args_preview": f"{', '.join(doc_tickers)} · {','.join(slots)} · {q_txt[:60]}",
                }
            )
        t0 = time.perf_counter()
        res = await _execute_disclosure_search(
            tool_name="search_disclosures",
            ctx=ctx,
            raw_tickers=doc_tickers,
            dense_queries=[f"{claim.statement} {q_txt}"],
            vas_terms=[q_txt],
            slots_filter=slots,
            years_filter=years,
            quarters_filter=quarters,
            include_cross_ref=False,
            top_k=5,
        )
        tool_calls += 1
        widened = False
        # If strict quarter/year filter yielded 0 chunks, widen once without year/quarter lock (0 LLM tokens!)
        if res.details.get("returned_count", 0) == 0 and (years or quarters):
            widened = True
            res = await _execute_disclosure_search(
                tool_name="search_disclosures",
                ctx=ctx,
                raw_tickers=doc_tickers,
                dense_queries=[f"{claim.statement} {q_txt}"],
                vas_terms=[q_txt],
                slots_filter=slots,
                years_filter=[],
                quarters_filter=[],
                include_cross_ref=False,
                top_k=5,
            )
            tool_calls += 1

        dur_ms = int((time.perf_counter() - t0) * 1000)
        if on_tool_result is not None:
            await on_tool_result(
                {
                    "id": call_id,
                    "call_id": call_id,
                    "type": "tool_call",
                    "turn": 1,
                    "tool": "search_disclosures",
                    "label": f"Tra cứu thuyết minh ({claim.id})",
                    "stage": "retrieval",
                    "status": "done",
                    "duration_ms": dur_ms,
                    "summary": f"Chọn lọc {res.details.get('returned_count', 0)} đoạn cho {claim.id}",
                    "crag_grade": res.details.get("crag_grade"),
                    "returned_count": res.details.get("returned_count", 0),
                    "selected_documents": list(res.details.get("selected_documents") or ())[:8],
                }
            )
        if res.details.get("returned_count", 0) > 0:
            snippet_blocks.append(res.content)
            for doc in res.details.get("selected_documents") or ():
                cid = str(doc.get("citation_id") or "")
                if cid and f"[{cid}]" not in gathered_cids:
                    gathered_cids.append(f"[{cid}]")
        search_log.append(
            {
                "doc_spec": dspec,
                "tickers": doc_tickers,
                "slots": slots,
                "fiscal_years": years,
                "fiscal_quarters": quarters,
                "query": q_txt,
                "widened_period_filter": widened,
                "returned_count": res.details.get("returned_count", 0),
                "crag_grade": res.details.get("crag_grade"),
                "selected_documents": list(res.details.get("selected_documents") or ())[:8],
                "duration_ms": dur_ms,
            }
        )

    if not snippet_blocks:
        no_snip = f"Không tìm thấy đoạn thuyết minh phù hợp trong phạm vi PIT {ctx.as_of_date.isoformat()}."
        _trace_reader(outcome="NO_SNIPPETS", status="NO_DATA", findings=[no_snip])
        return ("NO_DATA", [no_snip], [], None, Usage(), tool_calls)

    quant_hint = "\n".join(quant_context_lines[:3]) if quant_context_lines else "Không có số định lượng đi kèm."
    user_msg = (
        f"Ngày khóa sổ PIT: {ctx.as_of_date.isoformat()}\n"
        f"Ý cần kiểm chứng (`{claim.id}`): {claim.statement}\n"
        f"Ngữ cảnh số liệu BCTC chuẩn hóa đã biết:\n{quant_hint}\n\n"
        f"Các đoạn tài liệu gốc vừa truy xuất:\n" + "\n\n".join(snippet_blocks)
    )
    async with reader_sem:
        raw_json, usage = await _collect_model_text(
            model,
            (
                AgentMessage(role="system", content=MINI_READER_STATIC_SYSTEM_PROMPT),
                AgentMessage(role="user", content=user_msg),
            ),
            turn=1,
        )

    parsed = _parse_json_obj(raw_json)
    if not parsed:
        # Clean fallback if Mini-Reader returned plain text instead of JSON
        clean_txt = _THINK_RE.sub("", raw_json).strip()
        _trace_reader(
            outcome="NON_JSON_OUTPUT",
            user_prompt=user_msg,
            raw_output=raw_json,
            usage=usage_to_dict(usage),
            status="PARTIAL" if clean_txt else "NO_DATA",
        )
        if clean_txt:
            return ("PARTIAL", [clean_txt[:500]], gathered_cids, None, usage, tool_calls)
        return ("NO_DATA", ["Không trích xuất được dữ kiện từ đoạn tài liệu."], [], None, usage, tool_calls)

    raw_status = str(parsed.get("status") or "PARTIAL").strip().upper()
    status: VerdictStatus = (
        raw_status if raw_status in ("CONFIRMED", "REFUTED", "PARTIAL", "NO_DATA") else "PARTIAL"  # type: ignore[assignment]
    )
    findings = [
        str(f).strip()
        for f in (parsed.get("findings") or ())
        if isinstance(f, str) and f.strip()
    ]
    followup = str(parsed.get("anomaly_followup_query") or "").strip() or None
    if not findings and status != "NO_DATA":
        status = "NO_DATA"
        findings = ["Tài liệu truy xuất không chứa chi tiết thuyết minh khớp trực tiếp với câu hỏi."]
    _trace_reader(
        outcome="OK",
        user_prompt=user_msg,
        raw_output=raw_json,
        usage=usage_to_dict(usage),
        model_status=raw_status,
        status=status,
        findings=findings[:5],
        anomaly_followup_query=followup,
        gathered_citations=gathered_cids,
    )
    return status, findings[:5], gathered_cids, followup, usage, tool_calls


async def execute_brief_claims(
    *,
    brief: ResearchBrief,
    located: LocatedTargets,
    ctx: ResearchToolContext,
    model: ModelProvider,
    overall_deadline_mono: float | None = None,
    on_stage: Callable[[str, str, str], Awaitable[None]] | None = None,
    on_thought: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_call: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    on_tool_result: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
    trace: TraceSink | None = None,
) -> BriefExecutionResult:
    """Components (4) & (5): Premise-first + Parallel Per-Claim Execution + Code-Owned 1-Hop Escalation."""
    tickers = located.analysis_tickers
    fs: FactSet | None = None
    if ctx.core_reader is not None and tickers:
        try:
            fs = await _fact_set(ctx, tickers)
        except Exception:  # noqa: BLE001
            fs = None

    reader_sem = asyncio.Semaphore(MAX_MINI_READER_CONCURRENCY)
    total_usage = Usage()
    total_tool_calls = 0

    def _card_thought_payload(card: ClaimVerdictCard) -> dict[str, Any]:
        role_title = "Kiểm chứng tiền đề" if card.is_premise else "Thẻ kết luận"
        esc_suffix = " · Đã đào sâu vòng 2" if card.escalated else ""
        return {
            "id": f"card-{card.claim_id}",
            "type": "thought",
            "turn": 1,
            "kind": "verdict_card",
            "verdict_status": card.status,
            "is_premise": card.is_premise,
            "period": card.period,
            "claim_statement": card.statement,
            "badges": list(card.badges[:12]),
            "escalated": card.escalated,
            "status": "done",
            "title": f"{role_title} [{card.claim_id}] · {card.status}{esc_suffix}",
            "text": "\n".join(card.summary_lines[:6])[:1200],
        }

    async def _evaluate_one_claim(
        claim: ClaimSpec,
        *,
        is_premise: bool,
        refuted_premises: Mapping[str, str],
    ) -> tuple[ClaimVerdictCard, Usage, int]:
        quant_lines, badges, risk_followup = await _run_quant_specs_for_claim(
            claim=claim,
            tickers=tickers,
            fs=fs,
            located=located,
            ctx=ctx,
            on_tool_call=on_tool_call,
            on_tool_result=on_tool_result,
            on_stage=on_stage,
        )
        summary_lines = list(quant_lines)
        c_usage = Usage()
        c_tools = 0
        doc_followup: str | None = None

        refuted_deps = [pid for pid in claim.depends_on if pid in refuted_premises]
        if refuted_deps:
            summary_lines.insert(
                0,
                f"Lưu ý: Tiền đề {', '.join(refuted_deps)} đã bị bác bỏ bởi số liệu PIT; "
                "dưới đây là số liệu thực tế của doanh nghiệp để giải thích đúng bản chất.",
            )

        prem_status: VerdictStatus | None = None
        if is_premise:
            prem_status, prem_msg, prem_calc = _evaluate_premise_direction(claim, fs, tickers)
            if prem_calc is not None:
                saved_p = await ctx.evidence.register_calculator_trace(prem_calc)
                p_badge = f"[{saved_p['calc_id']}]"
                if p_badge not in badges:
                    badges.append(p_badge)
                if prem_msg:
                    prem_msg = f"{p_badge} {prem_msg}"
            if prem_msg:
                summary_lines.insert(0, prem_msg)

        doc_specs = [ev for ev in claim.evidence if ev.startswith("doc:")]
        doc_status: VerdictStatus | None = None
        # Skip speculative causal doc searches when a premise this claim depends on was already REFUTED
        if doc_specs and not refuted_deps:
            d_st, d_lines, d_badges, d_follow, d_usage, d_tools = await _run_doc_mini_reader(
                claim=claim,
                doc_specs=doc_specs,
                tickers=tickers,
                quant_context_lines=quant_lines,
                ctx=ctx,
                model=model,
                reader_sem=reader_sem,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                trace=trace,
            )
            doc_status = d_st
            summary_lines.extend(d_lines)
            for b in d_badges:
                if b not in badges:
                    badges.append(b)
            doc_followup = d_follow
            c_usage = c_usage.plus(d_usage)
            c_tools += d_tools

        if located.coverage_note and doc_specs:
            summary_lines.append(located.coverage_note)

        # Resolve final deterministic card status
        is_pure_screen_premise = (
            is_premise
            and "screen" in claim.evidence
            and not any(ev.startswith(("fact:", "kpi:", "doc:")) for ev in claim.evidence)
        )
        is_directional_premise = is_premise and bool(
            _NEGATIVE_PREMISE_RE.search(claim.statement) or _POSITIVE_PREMISE_RE.search(claim.statement)
        )
        if is_pure_screen_premise and prem_status is None:
            if any(r.get("all_passed") for r in located.candidate_rows):
                final_status: VerdictStatus = "CONFIRMED"
            else:
                final_status = "REFUTED"
                summary_lines.insert(
                    0,
                    "BÁC BỎ TIỀN ĐỀ: không có ứng viên nào trong phạm vi định vị đạt đủ tiêu chí lọc.",
                )
        elif is_directional_premise and prem_status is None and doc_status not in ("CONFIRMED", "REFUTED"):
            # A directional premise is NEVER "confirmed" just because 1 period exists: it needs a 2-period check
            final_status = "NO_DATA"
            summary_lines.insert(
                0,
                "CHƯA KIỂM CHỨNG ĐƯỢC TIỀN ĐỀ: không đủ số liệu so sánh 2 kỳ liên tiếp trong phạm vi PIT; "
                "không được coi tiền đề là đúng.",
            )
        elif prem_status == "REFUTED":
            final_status = "REFUTED"
        elif doc_status == "REFUTED":
            final_status = "REFUTED"
        elif prem_status == "CONFIRMED":
            final_status = "CONFIRMED"
        elif quant_lines and (not doc_specs or doc_status in ("CONFIRMED", "PARTIAL")):
            final_status = "CONFIRMED" if doc_status != "PARTIAL" else "PARTIAL"
        elif quant_lines and doc_status == "NO_DATA":
            final_status = "PARTIAL"
        elif doc_status in ("CONFIRMED", "PARTIAL"):
            final_status = doc_status
        else:
            final_status = "NO_DATA"

        esc_q = doc_followup or (risk_followup if not doc_specs else None)
        card = ClaimVerdictCard(
            claim_id=claim.id,
            is_premise=is_premise,
            statement=claim.statement,
            period=claim.period,
            status=final_status,
            summary_lines=summary_lines,
            badges=badges,
            escalation_query=esc_q,
        )
        if on_thought is not None:
            await on_thought(_card_thought_payload(card))
        return card, c_usage, c_tools

    # 1) Execute `premises_to_check` FIRST
    premise_cards: list[ClaimVerdictCard] = []
    refuted_map: dict[str, str] = {}
    if brief.premises_to_check:
        prem_outcomes = await asyncio.gather(
            *(
                _evaluate_one_claim(p, is_premise=True, refuted_premises={})
                for p in brief.premises_to_check
            )
        )
        for p_card, p_usage, p_tools in prem_outcomes:
            premise_cards.append(p_card)
            total_usage = total_usage.plus(p_usage)
            total_tool_calls += p_tools
            if p_card.status == "REFUTED":
                refuted_map[p_card.claim_id] = p_card.summary_lines[0] if p_card.summary_lines else "Bác bỏ"

    # 2) Execute all `claims` IN PARALLEL
    claim_cards: list[ClaimVerdictCard] = []
    if brief.claims:
        claim_outcomes = await asyncio.gather(
            *(
                _evaluate_one_claim(c, is_premise=False, refuted_premises=refuted_map)
                for c in brief.claims
            )
        )
        for c_card, c_usage, c_tools in claim_outcomes:
            claim_cards.append(c_card)
            total_usage = total_usage.plus(c_usage)
            total_tool_calls += c_tools

    # 3) Code-Controlled Selective 1-Hop Escalation (HARD CAP: max 1 round, max 2 claims, >=35s remaining)
    escalations_run = 0
    remaining_s = (
        (overall_deadline_mono - asyncio.get_running_loop().time())
        if overall_deadline_mono is not None
        else 60.0
    )
    escalation_candidates = [
        c
        for c in claim_cards
        if c.escalation_query
        and c.status != "REFUTED"
        and any(not ctx.available_tickers or t in ctx.available_tickers for t in tickers)
    ][:MAX_ESCALATION_CLAIMS]
    will_escalate = bool(escalation_candidates) and remaining_s >= 35.0
    emit_trace(
        trace,
        "escalation.decision",
        requested=[{"claim_id": c.claim_id, "query": c.escalation_query} for c in claim_cards if c.escalation_query],
        selected=[c.claim_id for c in escalation_candidates],
        remaining_seconds=round(remaining_s, 1),
        ran=will_escalate,
    )

    if will_escalate:
        if on_thought is not None:
            for c_esc in escalation_candidates:
                await on_thought(
                    {
                        "id": f"escalate-{c_esc.claim_id}",
                        "type": "thought",
                        "turn": 2,
                        "kind": "escalation",
                        "status": "done",
                        "title": f"Đào sâu bất thường vòng 2 [{c_esc.claim_id}]",
                        "text": (
                            f"Phát hiện chi tiết cần đối chiếu thêm (`{c_esc.escalation_query}`) "
                            "— tra cứu thuyết minh bổ sung."
                        ),
                    }
                )

        async def _run_followup(card: ClaimVerdictCard) -> tuple[list[str], list[str], Usage, int]:
            assert card.escalation_query is not None
            probe_claim = ClaimSpec(
                id=f"{card.claim_id}-hop2",
                statement=f"Đào sâu thuyết minh kiểm chứng dấu hiệu bất thường cho {card.claim_id}: {card.statement}",
                evidence=[f"doc:QUARTER_NOTES,BCTC_AUDITED|{card.escalation_query}"],
                period=card.period,
            )
            _, f_lines, f_badges, _, f_usage, f_tools = await _run_doc_mini_reader(
                claim=probe_claim,
                doc_specs=probe_claim.evidence,
                tickers=tickers,
                quant_context_lines=card.summary_lines[:2],
                ctx=ctx,
                model=model,
                reader_sem=reader_sem,
                on_tool_call=on_tool_call,
                on_tool_result=on_tool_result,
                trace=trace,
            )
            return f_lines, f_badges, f_usage, f_tools

        hop2_outcomes = await asyncio.gather(*(_run_followup(c) for c in escalation_candidates))
        for card, (f_lines, f_badges, f_usage, f_tools) in zip(escalation_candidates, hop2_outcomes, strict=True):
            total_usage = total_usage.plus(f_usage)
            total_tool_calls += f_tools
            if f_lines and f_badges:
                escalations_run += 1
                card.escalated = True
                card.summary_lines.extend(f"[Đào sâu thuyết minh] {ln}" for ln in f_lines[:3])
                for b in f_badges:
                    if b not in card.badges:
                        card.badges.append(b)
                if card.status == "NO_DATA":
                    card.status = "PARTIAL"
                if on_thought is not None:
                    await on_thought(_card_thought_payload(card))

    # Final full verdict cards = exact synthesis input (never truncated in the trace)
    spec_by_id = {s.id: s for s in [*brief.premises_to_check, *brief.claims]}
    claim_ids = {c.id for c in brief.claims}
    for card in [*premise_cards, *claim_cards]:
        spec = spec_by_id.get(card.claim_id)
        deps = list(spec.depends_on) if spec is not None else []
        emit_trace(
            trace,
            "claim.card",
            claim_id=card.claim_id,
            is_premise=card.is_premise,
            statement=card.statement,
            evidence_specs=list(spec.evidence) if spec is not None else [],
            depends_on=deps,
            # Claims run in parallel: only premise dependencies are enforced, claim->claim ones are not
            unenforced_claim_dependencies=[d for d in deps if d in claim_ids],
            status=card.status,
            escalated=card.escalated,
            escalation_query=card.escalation_query,
            badges=list(card.badges),
            summary_lines=list(card.summary_lines),
            rendered_for_synthesis=card.render_for_synthesis(),
        )

    return BriefExecutionResult(
        located=located,
        premise_cards=premise_cards,
        claim_cards=claim_cards,
        usage=total_usage,
        tool_calls_count=total_tool_calls,
        escalations_run=escalations_run,
    )
