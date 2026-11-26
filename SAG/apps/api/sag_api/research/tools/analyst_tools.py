"""Deterministic analyst tools (fact sheets, sector KPIs, earnings quality, red flags, peers).

Every number these tools show is computed in Python from Core PG statements (read-only,
PIT-gated) and registered in the evidence ledger as a `[CALC-n]` trace, so Stage 7 numeric
verification can check the final prose against it. Tools never accept numbers from the LLM.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from typing import Any

from sag_agent.tools import ToolContext, function_tool
from sag_agent.types import ToolExecutionMode, ToolResult, ToolRisk
from sag_api.research.analytics import (
    KPI_SPECS,
    KpiStatus,
    KpiValue,
    PeriodRef,
    PlanTarget,
    compute_kpi_pack,
    earnings_quality,
    latest_analysis_period,
    peer_compare,
    plan_vs_actual,
    red_flag_scan,
    vendor_snapshot,
)
from sag_api.research.analytics.agm_targets import extract_plan_targets
from sag_api.research.facts import FactSet, FinancialFact, LookupStatus, Template, load_fact_set
from sag_api.research.facts.catalog import CATALOG_BY_ID, items_for_template
from sag_api.research.tools.context import ResearchToolContext

MAX_ANALYTICS_TICKERS = 6

DEFAULT_SHEET_ITEMS: dict[Template, tuple[str, ...]] = {
    Template.CORPORATE: (
        "revenue_net", "gross_profit", "operating_profit", "pbt", "npat", "npat_parent", "total_assets",
        "equity", "cash_and_equivalents", "st_investments", "inventory", "st_receivables", "st_borrowings",
        "lt_borrowings", "advances_from_customers", "cfo", "capex",
    ),
    Template.BANK: (
        "nii", "net_fee_income", "toi", "opex", "ppop", "credit_provision_expense", "pbt", "npat",
        "total_assets", "equity", "customer_loans_gross", "loan_loss_reserve", "customer_deposits",
        "valuable_papers_issued",
    ),
    Template.SECURITIES: (
        "revenue_net", "brokerage_revenue", "margin_lending_income", "fvtpl_gains", "fvtpl_losses", "pbt",
        "npat", "total_assets", "equity", "margin_loans", "fvtpl_assets", "htm_assets", "afs_assets",
        "st_borrowings", "cfo",
    ),
    Template.INSURANCE: (
        "gross_written_premium", "net_premium", "insurance_claims", "insurance_gross_profit",
        "financial_activity_profit", "pbt", "npat", "total_assets", "equity", "technical_reserves", "cfo",
    ),
}


def _require_ctx(tool_ctx: ToolContext) -> ResearchToolContext:
    data = tool_ctx.data
    if not isinstance(data, ResearchToolContext):
        raise RuntimeError("Analyst tool invoked without ResearchToolContext")
    return data


def _clamp(raw: Any, ctx: ResearchToolContext) -> tuple[list[str], list[str]]:
    """ACL-only clamp: analytics run on Core PG statements, independent of SAG document coverage."""
    if not isinstance(raw, list | tuple):
        return [], []
    allowed = ctx.principal.allowed_project_ids
    ok: list[str] = []
    denied: list[str] = []
    for item in raw:
        sym = str(item or "").strip().upper()
        if not sym or sym in ok or sym in denied:
            continue
        if "*" not in allowed and sym not in allowed:
            denied.append(sym)
            continue
        ok.append(sym)
        if len(ok) >= MAX_ANALYTICS_TICKERS:
            break
    return ok, denied


async def _fact_set(ctx: ResearchToolContext, tickers: Sequence[str]) -> FactSet:
    key = frozenset(tickers)
    async with ctx.evidence.lock:
        for cached_key, fs in ctx.evidence.fact_sets.items():
            if key <= cached_key:
                return fs
    if ctx.core_reader is None:
        raise RuntimeError("Core PG reader is not configured (SAG_CORE_DATABASE_URL)")
    fs = await load_fact_set(ctx.core_reader, tickers=list(tickers), as_of_date=ctx.as_of_date)
    async with ctx.evidence.lock:
        ctx.evidence.fact_sets[key] = fs
        for tk in tickers:
            if tk in fs.templates:
                ctx.evidence._record_ticker_unlocked(tk)
    return fs


def fmt_value(value: float | None, unit: str) -> str:
    if value is None:
        return "-"
    if unit == "RATIO":
        return f"{value * 100:.2f}%"
    if unit == "X":
        return f"{value:.2f}x"
    if unit == "VND":
        return f"{value / 1e9:,.1f} tỷ đồng"
    if unit == "DAYS":
        return f"{value:.0f} ngày"
    if unit == "VND_PER_SHARE":
        return f"{value:,.0f} đ/cp"
    if unit == "SHARES":
        return f"{value:,.0f} cp"
    return f"{value:,.4f}"


_FORMAT_STYLE = {"RATIO": "percent", "X": "ratio"}


async def _register_kpi(ctx: ResearchToolContext, kv: KpiValue) -> str:
    saved = await ctx.evidence.register_calculator_trace(
        {
            "formula": kv.formula,
            "description": f"{kv.label_vi} {kv.ticker} {kv.period}",
            "inputs": {
                f"{inp['role']}:{inp['item']}@{inp['period']}": {"value": inp["value"], "unit": inp["unit"]}
                for inp in kv.inputs
            },
            "result": kv.value,
            "formatted": fmt_value(kv.value, kv.unit),
            "ticker": kv.ticker,
            "format_style": _FORMAT_STYLE.get(kv.unit, "number"),
            "source": "DETERMINISTIC_KPI_ENGINE",
            "flags": list(kv.flags),
        }
    )
    return str(saved["calc_id"])


async def _register_fact_sheet(ctx: ResearchToolContext, ticker: str, facts: Sequence[FinancialFact]) -> str:
    saved = await ctx.evidence.register_calculator_trace(
        {
            "formula": "fact_sheet",
            "description": f"Bảng số liệu chuẩn hóa {ticker} (Core PG, PIT {ctx.as_of_date.isoformat()})",
            "inputs": {f"{f.item}@{f.period_label}": {"value": f.value, "unit": f.unit} for f in facts},
            "result": None,
            "formatted": "",
            "ticker": ticker,
            "format_style": "number",
            "source": "CORE_PG_FACTS",
        }
    )
    return str(saved["calc_id"])


async def _trace(ctx: ResearchToolContext, tool: str, started: float, **extra: Any) -> int:
    elapsed = int((time.perf_counter() - started) * 1000)
    await ctx.evidence.record_tool_trace({"tool": tool, "duration_ms": elapsed, **extra})
    return elapsed


def _unknown_note(fs: FactSet, tickers: Sequence[str], denied: Sequence[str]) -> str:
    missing = [t for t in tickers if t not in fs.templates]
    parts = []
    if missing:
        parts.append(f"Không có BCTC trong Core PG tại PIT cho: {', '.join(missing)}.")
    if denied:
        parts.append(f"Không được phép truy cập: {', '.join(denied)}.")
    return " ".join(parts)


# ── Tool A: Normalized fact sheet ────────────────────────────────────────────


@function_tool(
    name="get_financial_statements",
    label="Bảng số liệu BCTC chuẩn hóa theo kỳ (Core PG, PIT)",
    description=(
        "Trả về số liệu BCTC đã chuẩn hóa (doanh thu/TOI, lợi nhuận, tài sản, vốn chủ, nợ vay, dòng tiền, "
        "chỉ tiêu đặc thù ngân hàng/chứng khoán/bảo hiểm) theo năm (FY), quý (Q) và 4 quý gần nhất (TTM). "
        "Dùng thay cho việc tìm số liệu trong văn bản. Mỗi bảng có mã [CALC-n] để trích dẫn."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "1..6 mã cổ phiếu."},
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Tùy chọn: mã chỉ tiêu (vd 'npat', 'cfo', 'customer_deposits'). Trống = mặc định.",
            },
            "view": {
                "type": "string",
                "enum": ["recent_quarters", "annual_history", "ttm"],
                "default": "recent_quarters",
            },
            "periods": {"type": "integer", "minimum": 1, "maximum": 8, "default": 5},
        },
        "required": ["tickers"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=15.0,
)
async def get_financial_statements_tool(arguments: Mapping[str, Any], tool_ctx: ToolContext) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()
    tickers, denied = _clamp(arguments.get("tickers"), ctx)
    if not tickers:
        raise ValueError("Cần ít nhất 1 mã cổ phiếu hợp lệ (trong quyền truy cập).")
    view = str(arguments.get("view") or "recent_quarters")
    ptype = {"recent_quarters": "Q", "annual_history": "FY", "ttm": "TTM"}.get(view, "Q")
    n = max(1, min(8, int(arguments.get("periods") or 5)))
    fs = await _fact_set(ctx, tickers)
    blocks: list[str] = []
    sheets: list[dict[str, Any]] = []
    for tk in tickers:
        tmpl = fs.templates.get(tk)
        if tmpl is None:
            continue
        valid_items = {it.item_id for it in items_for_template(tmpl)}
        req = [str(i).strip() for i in (arguments.get("items") or ()) if str(i).strip() in valid_items]
        items = req or list(DEFAULT_SHEET_ITEMS[tmpl])
        periods = sorted({(f.fiscal_year, f.fiscal_quarter) for f in fs.facts_for(tk) if f.period_type == ptype})[-n:]
        facts: list[FinancialFact] = []
        header = ["Chỉ tiêu"] + [PeriodRef(ptype, y, q).label for y, q in periods]
        lines = ["\t".join(header)]
        for item in items:
            row = [CATALOG_BY_ID[item].label_vi]
            for y, q in periods:
                res = fs.get(tk, item, ptype, y, q)
                if res.status is LookupStatus.OK and res.fact is not None:
                    facts.append(res.fact)
                    mark = "*" if res.fact.flags and any(
                        fl in res.fact.flags for fl in ("IDENTITY_FAIL", "FY_QSUM_MISMATCH")
                    ) else ""
                    row.append(fmt_value(res.fact.value, res.fact.unit) + mark)
                else:
                    row.append("-")
            lines.append("\t".join(row))
        calc_id = await _register_fact_sheet(ctx, tk, facts)
        note = (
            "Dòng tiền theo quý của nhà cung cấp thưa/lũy kế: dùng FY cho phân tích dòng tiền."
            if ptype != "FY" else ""
        )
        blocks.append(
            f"[{calc_id}] {tk} ({tmpl.value}) — {view}, PIT {ctx.as_of_date.isoformat()}, đơn vị VND\n"
            + "\n".join(lines)
            + ("\n(*) số liệu nhà cung cấp không khớp đẳng thức/tổng 4 quý — đối chiếu BCTC gốc." if any(
                "*" in ln for ln in lines[1:]) else "")
            + (f"\nGhi chú: {note}" if note else "")
        )
        sheets.append({"ticker": tk, "calc_id": calc_id, "template": tmpl.value, "facts": len(facts)})
    elapsed = await _trace(ctx, "get_financial_statements", started, tickers=tickers, view=view)
    unknown = _unknown_note(fs, tickers, denied)
    body = "\n\n".join(blocks) or "Không có số liệu BCTC hợp lệ."
    return ToolResult(
        content=body + (f"\n\n{unknown}" if unknown else ""),
        details={"sheets": sheets, "duration_ms": elapsed, "returned_count": len(sheets)},
    )


# ── Tool B: Sector KPI pack ──────────────────────────────────────────────────


@function_tool(
    name="get_sector_kpis",
    label="Bộ chỉ số tài chính theo ngành (tính toán tất định)",
    description=(
        "Tính bộ KPI theo đặc thù ngành từ BCTC chuẩn hóa: doanh nghiệp (biên gộp, ROE, CFO/LNST, nợ vay ròng, "
        "số ngày tồn kho/phải thu...), ngân hàng (NII/TS, CIR, chi phí tín dụng, LDR, dự phòng/dư nợ, tăng "
        "trưởng tín dụng; kèm NPL/CASA/bao phủ nợ xấu của nhà cung cấp dữ liệu), chứng khoán (margin/VCSH, "
        "FVTPL/VCSH, cơ cấu doanh thu), bảo hiểm (tỷ lệ bồi thường). Mỗi KPI có mã [CALC-n] và công thức."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "1..6 mã cổ phiếu."},
            "fiscal_year": {
                "type": "integer",
                "description": "Tùy chọn: tính trên năm tài chính đã kiểm toán. Bỏ trống = 4 quý gần nhất (TTM).",
            },
            "kpi_ids": {"type": "array", "items": {"type": "string"}, "description": "Tùy chọn: giới hạn KPI."},
        },
        "required": ["tickers"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=15.0,
)
async def get_sector_kpis_tool(arguments: Mapping[str, Any], tool_ctx: ToolContext) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()
    tickers, denied = _clamp(arguments.get("tickers"), ctx)
    if not tickers:
        raise ValueError("Cần ít nhất 1 mã cổ phiếu hợp lệ (trong quyền truy cập).")
    fs = await _fact_set(ctx, tickers)
    fy = arguments.get("fiscal_year")
    known_ids = {s.kpi_id for s in KPI_SPECS}
    kpi_ids = [k for k in (arguments.get("kpi_ids") or ()) if k in known_ids] or None
    blocks: list[str] = []
    registered = 0
    for tk in tickers:
        if tk not in fs.templates:
            continue
        period = PeriodRef("FY", int(fy), 0) if isinstance(fy, int) else latest_analysis_period(fs, tk)
        pack = compute_kpi_pack(fs, tk, period, kpi_ids=kpi_ids)
        lines = [f"{tk} ({fs.templates[tk].value}) — kỳ phân tích {period.label if period else '-'}"]
        for kv in pack:
            if kv.status is KpiStatus.OK:
                cid = await _register_kpi(ctx, kv)
                registered += 1
                extra = f" ({kv.detail})" if kv.detail else ""
                warn = " ⚠ dữ liệu nguồn có cờ" if kv.flags else ""
                lines.append(f"[{cid}] {kv.label_vi}: {fmt_value(kv.value, kv.unit)} | {kv.formula}{extra}{warn}")
            elif kv.status is KpiStatus.INSUFFICIENT_DATA:
                lines.append(f"- {kv.label_vi}: không đủ dữ liệu ({kv.detail})")
        snap = vendor_snapshot(fs, tk)
        if snap:
            saved = await ctx.evidence.register_calculator_trace(
                {
                    "formula": "vendor_ratio_snapshot",
                    "description": f"Chỉ số do nhà cung cấp dữ liệu tính {tk} {snap[0]['period']}",
                    "inputs": {d["item"]: {"value": d["value"], "unit": d["unit"]} for d in snap},
                    "result": None,
                    "formatted": "",
                    "ticker": tk,
                    "format_style": "number",
                    "source": "VENDOR_RATIO_ALPHASTOCK",
                }
            )
            vend = "; ".join(f"{CATALOG_BY_ID[d['item']].label_vi}={fmt_value(d['value'], d['unit'])}" for d in snap)
            lines.append(f"[{saved['calc_id']}] Chỉ số nhà cung cấp dữ liệu ({snap[0]['period']}, định nghĩa "
                         f"của nhà cung cấp): {vend}")
        blocks.append("\n".join(lines))
    elapsed = await _trace(ctx, "get_sector_kpis", started, tickers=tickers, registered=registered)
    unknown = _unknown_note(fs, tickers, denied)
    return ToolResult(
        content="\n\n".join(blocks) + (f"\n\n{unknown}" if unknown else ""),
        details={"returned_count": registered, "duration_ms": elapsed},
    )


# ── Tool C: Earnings quality + red flags ─────────────────────────────────────


@function_tool(
    name="scan_financial_risks",
    label="Soi chất lượng lợi nhuận & dấu hiệu bất thường (tất định)",
    description=(
        "Chạy bộ quy tắc tất định trên BCTC chuẩn hóa: chất lượng lợi nhuận (CFO/LNST, dồn tích, phải thu/tồn "
        "kho tăng nhanh hơn doanh thu), đòn bẩy và thanh khoản, rủi ro đặc thù ngân hàng/chứng khoán/bảo hiểm, "
        "lỗ hoặc LNST quý giảm mạnh, và cờ không nhất quán số liệu nguồn. Trả về danh sách cờ có ngưỡng và bằng chứng."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "1..6 mã cổ phiếu."},
        },
        "required": ["tickers"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=15.0,
)
async def scan_financial_risks_tool(arguments: Mapping[str, Any], tool_ctx: ToolContext) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()
    tickers, denied = _clamp(arguments.get("tickers"), ctx)
    if not tickers:
        raise ValueError("Cần ít nhất 1 mã cổ phiếu hợp lệ (trong quyền truy cập).")
    fs = await _fact_set(ctx, tickers)
    blocks: list[str] = []
    total_flags = 0
    for tk in tickers:
        if tk not in fs.templates:
            continue
        eq = earnings_quality(fs, tk)
        rf = red_flag_scan(fs, tk)
        n_rules = len(rf.get("rules_checked", []))
        lines = [f"{tk} ({rf.get('template')}) — kỳ {rf.get('period')}; quy tắc đã kiểm: {n_rules}"]
        seen_codes: set[str] = set()
        for flag in [*rf.get("flags", []), *eq.get("signals", [])]:
            if flag["code"] in seen_codes:
                continue
            seen_codes.add(flag["code"])
            total_flags += 1
            cid = ""
            if flag.get("observed") is not None:
                saved = await ctx.evidence.register_calculator_trace(
                    {
                        "formula": f"rule:{flag['code']}",
                        "description": f"{flag['message_vi']} ({tk} {flag['period']})",
                        "inputs": {
                            f"evidence_{i}": {"value": e.get("value")}
                            for i, e in enumerate(flag.get("evidence", []))
                            if isinstance(e, Mapping) and isinstance(e.get("value"), int | float)
                        },
                        "result": flag["observed"],
                        "formatted": f"{flag['observed']:.4g}",
                        "ticker": tk,
                        "format_style": "number",
                        "source": "DETERMINISTIC_RULE_ENGINE",
                    }
                )
                cid = f"[{saved['calc_id']}] "
            thr = f" (ngưỡng {flag['threshold']})" if flag.get("threshold") is not None else ""
            lines.append(f"{cid}{flag['severity']} {flag['code']}: {flag['message_vi']}{thr}")
        if len(lines) == 1:
            lines.append("Không phát hiện cờ rủi ro theo bộ quy tắc hiện tại.")
        if eq.get("note"):
            lines.append(f"Ghi chú: {eq['note']}")
        blocks.append("\n".join(lines))
    elapsed = await _trace(ctx, "scan_financial_risks", started, tickers=tickers, flags=total_flags)
    unknown = _unknown_note(fs, tickers, denied)
    return ToolResult(
        content="\n\n".join(blocks) + (f"\n\n{unknown}" if unknown else ""),
        details={"returned_count": total_flags, "duration_ms": elapsed},
    )


# ── Tool D: Peer comparison ──────────────────────────────────────────────────


@function_tool(
    name="compare_peers",
    label="So sánh đồng ngành theo KPI (cùng chuẩn mực, cùng kỳ)",
    description=(
        "So sánh 2..6 doanh nghiệp CÙNG chuẩn mực BCTC (ngân hàng với ngân hàng...) trên cùng kỳ 4 quý gần nhất, "
        "kèm xếp hạng theo từng KPI. Từ chối so sánh khác chuẩn mực."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "2..6 mã cùng ngành."},
            "kpi_ids": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["tickers"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=15.0,
)
async def compare_peers_tool(arguments: Mapping[str, Any], tool_ctx: ToolContext) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()
    tickers, denied = _clamp(arguments.get("tickers"), ctx)
    if len(tickers) < 2:
        raise ValueError("Cần ít nhất 2 mã cổ phiếu hợp lệ để so sánh.")
    fs = await _fact_set(ctx, tickers)
    known_ids = {s.kpi_id for s in KPI_SPECS}
    kpi_ids = [k for k in (arguments.get("kpi_ids") or ()) if k in known_ids] or None
    out = peer_compare(fs, tickers, kpi_ids)
    if out["status"] != "OK":
        await _trace(ctx, "compare_peers", started, status=out["status"])
        return ToolResult(content=f"{out['status']}: {out.get('detail', '')} {out.get('templates', '')}",
                          details={"status": out["status"]})
    lines = [f"So sánh {out['template']} — kỳ {out['period']} — {', '.join(out['tickers'])}",
             "KPI\t" + "\t".join(out["tickers"])]
    registered = 0
    for kid, row in out["kpis"].items():
        cells = []
        label = kid
        for tk in out["tickers"]:
            v = row.get(tk)
            if not v or v["status"] != "OK":
                cells.append("-")
                continue
            label = v["label_vi"]
            kv = KpiValue(
                kpi_id=v["kpi_id"], label_vi=v["label_vi"], ticker=tk, period=v["period"], value=v["value"],
                unit=v["unit"], formula=v["formula"], status=KpiStatus.OK, inputs=tuple(v["inputs"]),
                flags=tuple(v["flags"]),
            )
            cid = await _register_kpi(ctx, kv)
            registered += 1
            rank = f" #{v['rank']}" if "rank" in v else ""
            cells.append(f"{fmt_value(v['value'], v['unit'])}{rank} [{cid}]")
        lines.append(f"{label}\t" + "\t".join(cells))
    elapsed = await _trace(ctx, "compare_peers", started, tickers=tickers, registered=registered)
    unknown = _unknown_note(fs, tickers, denied)
    return ToolResult(
        content="\n".join(lines) + (f"\n\n{unknown}" if unknown else ""),
        details={"returned_count": registered, "duration_ms": elapsed, "period": out["period"]},
    )


# ── Tool E: AGM plan vs actual ───────────────────────────────────────────────

_PLAN_ITEMS = ("revenue_net", "pbt", "npat", "npat_parent")
_PACE_VI = {"AHEAD": "VƯỢT tiến độ", "ON_TRACK": "ĐÚNG tiến độ", "BEHIND": "CHẬM tiến độ"}


async def _agm_tables(ctx: ResearchToolContext, ticker: str) -> list[dict[str, Any]]:
    from sag_api.research.chat_orchestrator import SQLAlchemyTableStore

    async with ctx.session_factory() as session:
        store = ctx.table_store_override or SQLAlchemyTableStore(session)
        return await store.get_slot_tables(
            ticker=ticker, sub_doc_slot="AGM_TARGETS", as_of_date=ctx.as_of_date, tenant_id=ctx.tenant_id
        )


@function_tool(
    name="check_plan_vs_actual",
    label="Đối chiếu kế hoạch ĐHĐCĐ với thực hiện lũy kế (tất định)",
    description=(
        "Trích kế hoạch doanh thu/LNTT/LNST năm tài chính từ bảng Nghị quyết/Tài liệu ĐHĐCĐ đã index (PIT) bằng "
        "quy tắc tất định, rồi so với số thực hiện lũy kế các quý từ BCTC chuẩn hóa: % hoàn thành, % thời gian đã "
        "trôi qua, tiến độ (vượt/đúng/chậm) và phần còn phải đạt. Không nhận số kế hoạch từ người dùng hay mô hình."
    ),
    parameters={
        "type": "object",
        "properties": {
            "tickers": {"type": "array", "items": {"type": "string"}, "description": "1..6 mã cổ phiếu."},
            "fiscal_year": {"type": "integer", "description": "Năm kế hoạch. Bỏ trống = năm của ngày PIT."},
        },
        "required": ["tickers"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ_ONLY,
    execution_mode=ToolExecutionMode.PARALLEL,
    timeout_seconds=20.0,
)
async def check_plan_vs_actual_tool(arguments: Mapping[str, Any], tool_ctx: ToolContext) -> ToolResult:
    ctx = _require_ctx(tool_ctx)
    started = time.perf_counter()
    tickers, denied = _clamp(arguments.get("tickers"), ctx)
    if not tickers:
        raise ValueError("Cần ít nhất 1 mã cổ phiếu hợp lệ (trong quyền truy cập).")
    fy_arg = arguments.get("fiscal_year")
    fy = int(fy_arg) if isinstance(fy_arg, int) else ctx.as_of_date.year
    fs = await _fact_set(ctx, tickers)
    blocks: list[str] = []
    statuses: dict[str, str] = {}
    for tk in tickers:
        tmpl = fs.templates.get(tk)
        if tmpl is None:
            continue
        applicable = {it.item_id for it in items_for_template(tmpl)}
        prior = {}
        for item in _PLAN_ITEMS:
            res = fs.get(tk, item, "FY", fy - 1)
            if res.status is LookupStatus.OK and res.fact is not None:
                prior[item] = res.fact.value
        ex = extract_plan_targets(await _agm_tables(ctx, tk), fy, prior_actuals=prior)
        lines = [f"{tk} ({tmpl.value}) — kế hoạch năm {fy} (bảng ĐHĐCĐ, PIT {ctx.as_of_date.isoformat()})"]
        targets = [
            PlanTarget(item, cand.value, f"AGM_TARGETS {cand.table_id} tr.{cand.page}")
            for item, cand in ex.targets.items()
            if item in applicable
        ]
        for item, cands in ex.conflicts.items():
            vals = "; ".join(f"{fmt_value(c.value, 'VND')} (tr.{c.page})" for c in cands)
            lbl = CATALOG_BY_ID[item].label_vi
            lines.append(f"- {lbl}: CONFLICT giữa các bảng ĐHĐCĐ ({vals}) — không tính tiến độ.")
        if not targets:
            reasons = sorted({r["reason"] for r in ex.rejected})
            lines.append(
                f"NOT_FOUND: không trích xuất được kế hoạch năm {fy} từ bảng ĐHĐCĐ trong phạm vi PIT"
                + (f" (loại bỏ: {', '.join(reasons)})" if reasons else "")
                + "."
            )
            statuses[tk] = "NOT_FOUND" if not ex.conflicts else "CONFLICT"
            blocks.append("\n".join(lines))
            continue
        out = plan_vs_actual(fs, tk, fy, targets)
        statuses[tk] = "OK"
        lines.append(f"Số quý đã công bố liên tục trong năm {fy}: {out['quarters_reported']}")
        for row in out["rows"]:
            item = row["item"]
            cand = ex.targets[item]
            label = CATALOG_BY_ID[item].label_vi
            caveat = (
                "" if cand.exact_definition
                else f" ⚠ kế hoạch ghi '{cand.raw_text}' theo định nghĩa của DN (có thể là tổng doanh thu), "
                "so với doanh thu thuần — chỉ mang tính tham chiếu"
            )
            adj = " (kế hoạch điều chỉnh)" if cand.adjusted else ""
            if row["status"] != "OK":
                lines.append(f"- {label}: KH {fmt_value(row['target'], 'VND')}{adj} — {row['status']} "
                             f"({row.get('detail', '')})")
                continue
            saved = await ctx.evidence.register_calculator_trace(
                {
                    "formula": "ytd_actual / plan_target",
                    "description": f"% hoàn thành kế hoạch {label} {tk} năm {fy} ({row['ytd_period']})",
                    "inputs": {
                        f"plan_target:{item}@KH{fy}": {"value": row["target"], "unit": "VND"},
                        f"ytd_actual:{item}@{row['ytd_period']}": {"value": row["ytd_actual"], "unit": "VND"},
                        "required_remaining": {"value": row["required_remaining"], "unit": "VND"},
                    },
                    "result": row["achievement"],
                    "formatted": f"{row['achievement'] * 100:.1f}%",
                    "ticker": tk,
                    "format_style": "percent",
                    "source": "DETERMINISTIC_PLAN_ENGINE",
                    "target_source": row["target_source"],
                }
            )
            lines.append(
                f"[{saved['calc_id']}] {label}: KH {fmt_value(row['target'], 'VND')}{adj} ({row['target_source']}) | "
                f"lũy kế {row['ytd_period']} {fmt_value(row['ytd_actual'], 'VND')} | đạt "
                f"{row['achievement'] * 100:.1f}% KH so với {row['time_elapsed'] * 100:.0f}% thời gian → "
                f"{_PACE_VI[row['pace']]}; còn phải đạt {fmt_value(row['required_remaining'], 'VND')}{caveat}"
            )
        skipped = sorted(set(ex.targets) - applicable)
        if skipped:
            lines.append(f"Bỏ qua chỉ tiêu không áp dụng cho chuẩn mực {tmpl.value}: {', '.join(skipped)}.")
        blocks.append("\n".join(lines))
    elapsed = await _trace(ctx, "check_plan_vs_actual", started, tickers=tickers, fiscal_year=fy, statuses=statuses)
    unknown = _unknown_note(fs, tickers, denied)
    return ToolResult(
        content="\n\n".join(blocks) + (f"\n\n{unknown}" if unknown else ""),
        details={"statuses": statuses, "fiscal_year": fy, "duration_ms": elapsed,
                 "returned_count": sum(1 for s in statuses.values() if s == "OK")},
    )


def build_analyst_tools() -> tuple[Any, ...]:
    return (
        get_financial_statements_tool,
        get_sector_kpis_tool,
        scan_financial_risks_tool,
        compare_peers_tool,
        check_plan_vs_actual_tool,
    )
