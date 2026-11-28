"""Deterministic Scope-Gated Market & Sector Pulse Reader (`0` LLM tokens, `100%` PIT-safe).

Resolves the `Zero-Seed Paradox` on broad Sector and Market-wide queries without violating
`adr-remove-e2-llm-enrichment.md` or introducing anchoring bias on single-company queries:
- `COMPANY`: Injects only target-ticker structural disclosure pointers & subsidiary/associate topology.
- `SECTOR`: Computes full-breadth quantitative metrics across all `N` sector issuers in Core PG,
  explicitly separated from `M` SAG-indexed bellwether disclosure pointers.
- `MARKET` / `OPEN_STRATEGY`: Computes PIT-exact VN-Index, 20D liquidity, 5D/20D foreign net flow,
  HMM market regime, macro indicators, and cross-sector earnings/valuation matrix + bellwether pointers.
- Every quantitative aggregate registers `[CALC-*]` / `[SCR:*]` traces so Stage 7 audits 100% of numbers.
"""

from __future__ import annotations

import logging
import re
import statistics
import unicodedata
from collections import OrderedDict, defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from sag_api.db.models import Document, DocumentVersion, KnowledgeUnit
from sag_api.db.models.stock_research import KgEdge, KgNode
from sag_api.research.tools.context import ResearchEvidenceLedger, ResearchToolContext

logger = logging.getLogger(__name__)

ScopeLens = Literal["COMPANY", "SECTOR", "MARKET", "OPEN_STRATEGY"]

SECTOR_DISPLAY_VI: dict[str, str] = {
    "BANKS": "Ngân hàng",
    "FINANCIAL_SERVICES": "Chứng khoán & Dịch vụ Tài chính",
    "REAL_ESTATE": "Bất động sản",
    "RETAIL_TRADE": "Bán lẻ",
    "BASIC_RESOURCES": "Thép & Tài nguyên Cơ bản",
    "TECHNOLOGY": "Công nghệ & Viễn thông",
    "OIL_GAS": "Dầu khí",
    "CHEMICALS": "Hóa chất & Phân bón",
    "UTILITIES": "Điện, Nước & Tiện ích",
    "TRANSPORTATION": "Vận tải, Cảng biển & Logistics",
    "FOOD_BEVERAGE": "Thực phẩm & Đồ uống",
    "CONSTRUCTION": "Xây dựng & Đầu tư công",
    "CONSTRUCTION_MATERIALS": "Vật liệu Xây dựng",
    "INDUSTRIAL_GOODS": "Hàng & Thiết bị Công nghiệp",
    "HEALTHCARE": "Dược phẩm & Y tế",
    "AGRICULTURE": "Nông nghiệp & Thủy sản",
    "CONSUMER_SERVICES": "Dịch vụ Tiêu dùng & Hàng không",
    "OTHER_INDUSTRIALS": "Công nghiệp Đa ngành",
}

_SECTOR_KEYWORD_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("BANKS", ("ngan hang", "nha bang", "co phieu bank", "nhom bank", "nganh bank")),
    (
        "FINANCIAL_SERVICES",
        (
            "nganh chung khoan",
            "nhom chung khoan",
            "co phieu chung khoan",
            "ctck",
            "moi gioi chung khoan",
            "cong ty chung khoan",
        ),
    ),
    ("REAL_ESTATE", ("bat dong san", "dia oc", "bds", "khu cong nghiep", "nha o")),
    ("RETAIL_TRADE", ("ban le", "chuoi ban le", "dien may", "nha thuoc", "trang suc")),
    ("BASIC_RESOURCES", ("nganh thep", "co phieu thep", "nhom thep", "ton ma", "khoang san", "tai nguyen")),
    ("TECHNOLOGY", ("cong nghe", "cntt", "phan mem", "vien thong", "chuyen doi so", "ban dan")),
    ("OIL_GAS", ("dau khi", "nhom dau", "pvn", "loc hoa dau", "khi dot")),
    ("CHEMICALS", ("hoa chat", "phan bon", "ure", "dap", "phot pho", "cao su cong nghiep")),
    ("UTILITIES", ("nganh dien", "nhiet dien", "thuy dien", "dien khi", "nang luong tai tao", "cap nuoc")),
    ("TRANSPORTATION", ("van tai", "cang bien", "logistics", "hang hai", "tau bien")),
    ("FOOD_BEVERAGE", ("thuc pham", "do uong", "nganh sua", "bia", "tieu dung thiet yeu")),
    ("CONSTRUCTION", ("xay dung", "dau tu cong", "ha tang giao thong", "nha thau")),
    ("CONSTRUCTION_MATERIALS", ("vat lieu xay dung", "xi mang", "da xay dung", "nhua duong")),
    ("HEALTHCARE", ("duoc pham", "nganh duoc", "y te", "benh vien")),
    ("AGRICULTURE", ("nong nghiep", "thuy san", "ca tra", "tom", "chan nuoi", "gao", "mia duong")),
)

_TICKER_TOKEN_RE = re.compile(r"\b([A-Z][A-Z0-9]{2,4})\b")
_PIVOT_AWAY_RE = re.compile(
    r"(?:bỏ\s+|quên\s+|đổi\s+sang|chuyển\s+sang|thị\s+trường\s+hiện\s+tại|toàn\s+thị\s+trường|ngành\s+)",
    re.IGNORECASE | re.UNICODE,
)
_QUANT_SCREENER_CUE_RE = re.compile(
    r"(?:rẻ\s+so\s+với\s+tiền|p/e\s*[<>=]|p/b\s*[<>=]|roe\s*[<>=]|biên\s+gộp\s+cao\s+nhất|"
    r"nợ\s+vay\s+thấp\s+nhất|cổ\s+tức\s+cao\s+nhất|lọc\s+cổ\s+phiếu\s+theo|sàng\s+lọc\s+theo)",
    re.IGNORECASE | re.UNICODE,
)
_OPEN_STRATEGY_RE = re.compile(
    r"(?:mua\s+(?:cổ\s+phiếu\s+|mã\s+)?gì\s+(?:tốt|ngon|ổn|hợp\s+lý)|"
    r"(?:cổ\s+phiếu|mã)\s+nào\s+(?:tốt|đáng\s+mua|đáng\s+đầu\s+tư|ngon|hấp\s+dẫn)\s*\??$|"
    r"giờ\s+(?:nên\s+)?(?:mua|đầu\s+tư)\s+(?:cổ\s+phiếu\s+|mã\s+)?gì|"
    r"gợi\s+ý\s+(?:vài\s+|một\s+số\s+)?(?:mã|cổ\s+phiếu))",
    re.IGNORECASE | re.UNICODE,
)
_MARKET_WIDE_RE = re.compile(
    r"(?:thị\s+trường|vn-?index|vnindex|toàn\s+sàn|dòng\s+tiền|khối\s+ngoại|thanh\s+khoản|"
    r"vĩ\s+mô|nhóm\s+ngành\s+nào|xoay\s+trục\s+ngành|bối\s+cảnh\s+hiện\s+tại|tình\s+hình\s+chung)",
    re.IGNORECASE | re.UNICODE,
)

_SIGNAL_LABEL_VI: dict[str, str] = {
    "AGM_QA": "Hỏi–đáp ĐHĐCĐ",
    "CONSOLIDATION_SCOPE": "Thay đổi cấu trúc/hợp nhất",
    "SEGMENT_MDNA": "Giải trình mảng & thị trường",
    "STRATEGIC_CAPEX": "Dự án & đầu tư chiến lược",
    "AUDIT_GOVERNANCE_FLAG": "Lưu ý kiểm toán/quản trị",
    "F1_GROWTH_BACKLOG_MARKET": "Tăng trưởng, đơn hàng & mở rộng thị trường",
    "F2_MARGIN_PRICING_COST": "Biên lợi nhuận, định giá & tối ưu chi phí",
    "F3_FUNDING_CASA_LIQUIDITY": "Huy động vốn rẻ, CASA & thanh khoản",
    "F4_FEE_ECOSYSTEM_IB": "Thu nhập phí, bảo hiểm & hệ sinh thái",
    "F5_ASSET_CREDIT_RISK": "Chất lượng tài sản, vốn lưu động & kiểm soát rủi ro",
    "F6_CONSOLIDATION_MNA_DIVIDEND": "Cấu trúc hợp nhất, công ty liên kết & cổ tức",
    "F7_CAPEX_TECH_NEW_BET": "Đầu tư CAPEX, hạ tầng công nghệ & mũi nhọn mới",
    "F8_GOVERNANCE_HUMAN_CAPITAL": "Quản trị HĐQT & nguồn lực nhân sự",
}

_MAX_CACHE_ENTRIES = 64
_PULSE_CACHE: OrderedDict[tuple[str, str, str, str], ScopeBriefingBundle] = OrderedDict()


def clear_market_pulse_cache() -> None:
    """Clear in-memory PIT briefing cache (used in tests and admin resets)."""
    _PULSE_CACHE.clear()


def _fold_ascii(text: str) -> str:
    s = unicodedata.normalize("NFD", str(text or "")).replace("đ", "d").replace("Đ", "d")
    return re.sub(r"\s+", " ", s.encode("ascii", "ignore").decode("ascii").lower()).strip()


def _fmt_pct(val: float | None, *, signed: bool = True) -> str:
    if val is None:
        return "N/A"
    pct = val * 100.0
    return f"{pct:+.1f}%" if signed else f"{pct:.1f}%"


def _fmt_mult(val: float | None) -> str:
    if val is None:
        return "N/A"
    return f"{val:.2f}x"


def _fmt_t_vnd(val_vnd: float | None, *, signed: bool = True) -> str:
    if val_vnd is None:
        return "N/A"
    ty = val_vnd / 1e9
    if signed:
        return f"{ty:+,.0f} tỷ VND"
    return f"{ty:,.0f} tỷ VND"


@dataclass(frozen=True, slots=True)
class ScopeLensResult:
    lens: ScopeLens
    matched_sectors: list[str] = field(default_factory=list)
    matched_sector_label_vi: str | None = None
    matched_tickers: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class StructuralPointer:
    unit_id: str
    document_version_id: str
    ticker: str
    sub_doc_slot: str
    fiscal_year: int | None
    fiscal_quarter: int | None
    page_from: int
    section_path: str
    signals: tuple[str, ...]
    entities: tuple[str, ...]
    excerpt: str
    block_ids: tuple[str, ...] = ()


@dataclass
class ScopeBriefingBundle:
    lens: ScopeLens
    as_of_date: date
    matched_sectors: list[str] = field(default_factory=list)
    matched_sector_label_vi: str | None = None
    target_tickers: list[str] = field(default_factory=list)
    full_universe_count: int = 0
    bellwether_tickers: list[str] = field(default_factory=list)
    structural_pointers: list[StructuralPointer] = field(default_factory=list)
    briefing_text: str = ""
    calc_traces: list[dict[str, Any]] = field(default_factory=list)
    screening_rows: list[dict[str, Any]] = field(default_factory=list)
    screening_description: str = ""


def resolve_query_scope_lens(
    *,
    query: str,
    explicit_tickers: Sequence[str] = (),
    fallback_tickers: Sequence[str] = (),
    catalog_profiles: Sequence[Mapping[str, Any]] = (),
    available_tickers: Sequence[str] = (),
) -> ScopeLensResult:
    """Deterministically classify query lens (`COMPANY` | `SECTOR` | `MARKET` | `OPEN_STRATEGY`)."""
    known_tks = {
        str(p.get("ticker") or p.get("symbol") or "").strip().upper()
        for p in catalog_profiles
        if str(p.get("ticker") or p.get("symbol") or "").strip()
    } | {str(t).strip().upper() for t in available_tickers if str(t).strip()}

    detected_tks: list[str] = []
    for t in explicit_tickers:
        sym = str(t or "").strip().upper()
        if sym and sym not in detected_tks:
            detected_tks.append(sym)

    q_clean = str(query or "").strip()
    for m in _TICKER_TOKEN_RE.finditer(q_clean.upper()):
        tok = m.group(1)
        if tok in known_tks and tok not in detected_tks:
            detected_tks.append(tok)

    if not detected_tks and fallback_tickers and not _PIVOT_AWAY_RE.search(q_clean):
        for t in fallback_tickers:
            sym = str(t or "").strip().upper()
            if sym and sym not in detected_tks:
                detected_tks.append(sym)

    if detected_tks:
        return ScopeLensResult(lens="COMPANY", matched_tickers=detected_tks[:6])

    if _QUANT_SCREENER_CUE_RE.search(q_clean):
        return ScopeLensResult(lens="COMPANY", matched_tickers=[])

    folded = _fold_ascii(q_clean)
    matched_secs: list[str] = []
    for sec_code, kws in _SECTOR_KEYWORD_RULES:
        if any(kw in folded for kw in kws):
            matched_secs.append(sec_code)

    if matched_secs and not ("toan thi truong" in folded or "vnindex" in folded or "vn-index" in folded):
        lbl = " & ".join(SECTOR_DISPLAY_VI.get(s, s) for s in matched_secs[:2])
        return ScopeLensResult(lens="SECTOR", matched_sectors=matched_secs, matched_sector_label_vi=lbl)

    if _OPEN_STRATEGY_RE.search(q_clean):
        return ScopeLensResult(lens="OPEN_STRATEGY")

    if _MARKET_WIDE_RE.search(q_clean) or len(folded.split()) >= 3:
        return ScopeLensResult(lens="MARKET")

    return ScopeLensResult(lens="OPEN_STRATEGY")


def _format_slot_period_stamp(slot: str, fy: int | None, fq: int | None) -> str:
    s_up = (slot or "").upper()
    if s_up == "AGM_TARGETS":
        return f"ĐHĐCĐ {fy}" if fy else "ĐHĐCĐ"
    if s_up == "BCTN_CORE":
        return f"BCTN thực hiện FY{fy}" if fy else "BCTN"
    if s_up == "BCTC_AUDITED":
        return f"BCTC kiểm toán FY{fy}" if fy else "BCTC kiểm toán"
    if s_up == "QUARTER_NOTES":
        if fy and fq and fq > 0:
            return f"BCTC Q{fq}/{fy}"
        return f"BCTC {fy}" if fy else "BCTC quý"
    return s_up


async def load_pit_structural_pointers(
    session: AsyncSession | None,
    *,
    tenant_id: str,
    as_of_date: date,
    tickers: Sequence[str],
    allowed_project_ids: frozenset[str] | set[str] = frozenset({"*"}),
    signal_filter: Sequence[str] | None = None,
    limit_per_ticker: int = 3,
    max_total: int = 18,
) -> list[StructuralPointer]:
    """Load PIT-valid Intrinsic Company Story Spine peaks (`0` LLM tokens, Discourse-Stitched & Facet-Diversified)."""
    if session is None or not tickers:
        return []
    norm_tks = [
        t.strip().upper()
        for t in tickers
        if t and t.strip() and ("*" in allowed_project_ids or t.strip().upper() in allowed_project_ids)
    ]
    if not norm_tks:
        return []

    from sag_api.research.epistemic_foundation import select_company_story_spine, unit_epistemic_meta

    row_cap = 1400 if len(norm_tks) <= 2 else 600
    cutoff_dt = datetime(as_of_date.year, as_of_date.month, as_of_date.day, 23, 59, 59, tzinfo=UTC)
    stmt = (
        select(KnowledgeUnit, DocumentVersion.metadata_json, DocumentVersion.source_published_at)
        .join(DocumentVersion, DocumentVersion.id == KnowledgeUnit.document_version_id)
        .join(Document, Document.id == DocumentVersion.document_id)
        .where(
            KnowledgeUnit.project_id.in_(norm_tks),
            KnowledgeUnit.is_current.is_(True),
            KnowledgeUnit.valid_from <= cutoff_dt,
            KnowledgeUnit.valid_to > cutoff_dt,
            Document.is_active.is_(True),
            DocumentVersion.status != "REVOKED",
            DocumentVersion.valid_from <= cutoff_dt,
            DocumentVersion.valid_to > cutoff_dt,
        )
        .order_by(DocumentVersion.source_published_at.desc(), KnowledgeUnit.ordinal.asc())
        .limit(row_cap)
    )
    if tenant_id:
        stmt = stmt.where(Document.tenant_id == tenant_id)

    rows = (await session.execute(stmt)).all()
    want_sigs = {s.upper() for s in signal_filter} if signal_filter else None

    units_by_tk: dict[str, list[Any]] = defaultdict(list)
    meta_by_uid: dict[str, tuple[Mapping[str, Any], datetime | None]] = {}
    disc_siblings: dict[tuple[str, str], list[Any]] = defaultdict(list)

    for ku, ver_meta, src_pub in rows:
        tk = str(ku.project_id or "").strip().upper()
        vmeta = ver_meta if isinstance(ver_meta, Mapping) else {}
        meta_by_uid[str(ku.id)] = (vmeta, src_pub if isinstance(src_pub, datetime) else None)
        units_by_tk[tk].append(ku)
        _, _, _, d_id, _ = unit_epistemic_meta(ku)
        if d_id:
            disc_siblings[(str(ku.document_version_id), d_id)].append(ku)

    for d_list in disc_siblings.values():
        d_list.sort(key=lambda u: int(getattr(u, "ordinal", 0) or 0))

    def _to_pointer(ku: Any, tk: str) -> StructuralPointer:
        feats = ku.features_json if isinstance(ku.features_json, Mapping) else {}
        prov = ku.provenance_json if isinstance(ku.provenance_json, Mapping) else {}
        vmeta, _ = meta_by_uid.get(str(ku.id), ({}, None))
        slot = str(prov.get("sub_doc_slot") or vmeta.get("sub_doc_slot") or "BCTN_CORE").upper()
        fy = prov.get("fiscal_year") if prov.get("fiscal_year") is not None else vmeta.get("fiscal_year")
        fq = prov.get("fiscal_quarter") if prov.get("fiscal_quarter") is not None else vmeta.get("fiscal_quarter")
        raw_sigs = tuple(str(x) for x in (feats.get("structural_signals") or ()) if x)
        _, _, facs, d_id, _ = unit_epistemic_meta(ku)
        if not raw_sigs and facs:
            raw_sigs = tuple(facs[:2])

        # Stitch Question + Executive Answer inside the same `discourse_id` so coreference is never lost!
        sibs = disc_siblings.get((str(ku.document_version_id), d_id), [ku]) if d_id else [ku]
        merged_bids: list[str] = []
        merged_ents: list[str] = []
        txt_parts: list[str] = []
        min_pg = max(1, int(prov.get("page_from") or 1))
        for s_ku in sibs[:4]:
            s_tier, _, _, _, _ = unit_epistemic_meta(s_ku)
            if s_tier == 0 and len(sibs) > 1:
                continue
            s_prov = s_ku.provenance_json if isinstance(s_ku.provenance_json, Mapping) else {}
            s_feats = s_ku.features_json if isinstance(s_ku.features_json, Mapping) else {}
            s_pg = int(s_prov.get("page_from") or 0)
            if s_pg > 0:
                min_pg = min(min_pg, s_pg)
            for bid in s_prov.get("evidence_block_ids") or ():
                if bid and str(bid) not in merged_bids:
                    merged_bids.append(str(bid))
            for ent in s_feats.get("entities") or ():
                if ent and str(ent) not in merged_ents:
                    merged_ents.append(str(ent))
            clean_part = re.sub(r"\s+", " ", str(s_ku.text or "")).strip()
            if clean_part:
                txt_parts.append(clean_part)

        if not merged_bids:
            merged_bids = [str(b) for b in (prov.get("evidence_block_ids") or ()) if b]
        if not txt_parts:
            txt_parts = [re.sub(r"\s+", " ", str(ku.text or "")).strip()]

        fy_int = int(fy) if isinstance(fy, int) or (isinstance(fy, str) and fy.isdigit()) else None
        fq_int = int(fq) if isinstance(fq, int) or (isinstance(fq, str) and fq.isdigit()) else None
        stamp = _format_slot_period_stamp(slot, fy_int, fq_int)
        combined_excerpt = f"[{stamp}] " + " | ".join(txt_parts)
        sec_path = str(prov.get("section_path") or "").strip()

        return StructuralPointer(
            unit_id=str(ku.id),
            document_version_id=str(ku.document_version_id),
            ticker=tk,
            sub_doc_slot=slot,
            fiscal_year=fy_int,
            fiscal_quarter=fq_int,
            page_from=min_pg,
            section_path=sec_path,
            signals=raw_sigs or ("SEGMENT_MDNA",),
            entities=tuple(merged_ents[:8]),
            excerpt=combined_excerpt[:360],
            block_ids=tuple(merged_bids[:8]),
        )

    out: list[StructuralPointer] = []
    for tk in norm_tks:
        tk_pool = units_by_tk.get(tk, [])
        if want_sigs:
            tk_pool = [
                u
                for u in tk_pool
                if set((u.features_json or {}).get("structural_signals") or ()) & want_sigs
            ]
        spine_units = select_company_story_spine(tk_pool, max_peaks=limit_per_ticker)
        if len(spine_units) < limit_per_ticker:
            seen_uids = {str(u.id) for u in spine_units}
            for u in tk_pool:
                if str(u.id) in seen_uids:
                    continue
                sigs_u = (u.features_json or {}).get("structural_signals") or ()
                if sigs_u:
                    spine_units.append(u)
                    seen_uids.add(str(u.id))
                if len(spine_units) >= limit_per_ticker:
                    break
        for win_u in spine_units[:limit_per_ticker]:
            out.append(_to_pointer(win_u, tk))
            if len(out) >= max_total:
                return out
    return out


def _canonical_corp_entity_key(label: str) -> str:
    """Normalize corporate entity labels so punctuation/MTV/acronym variants collapse cleanly."""
    s = re.sub(r"\([^)]*\)", " ", str(label or ""))
    s = s.replace("(*)", " ")
    folded = _fold_ascii(s)
    folded = re.sub(
        r"^(?:cong\s+ty\s+tnhh\s+mot\s+thanh\s+vien|cong\s+ty\s+tnhh\s+mtv|cong\s+ty\s+tnhh|"
        r"cong\s+ty\s+co\s+phan|ctcp|ngan\s+hang\s+tmcp|tap\s+doan)\s+",
        "",
        folded,
    )
    folded = re.sub(r"[^a-z0-9]+", " ", folded).strip()
    return folded or _fold_ascii(label)


_MACRO_LABEL_VI: dict[str, str] = {
    "usd_vnd_exchange": "Tỷ giá USD/VND",
    "vninbr_interbank_rate": "Lãi suất liên ngân hàng",
    "vnintr_interest_rate": "Lãi suất điều hành/huy động",
    "oil_price_brent": "Giá dầu Brent",
    "usd_10y_yield": "Lợi suất TPCP Mỹ 10Y",
    "vix": "Chỉ số biến động VIX",
}


async def _load_pit_subsidiary_summary(
    session: AsyncSession | None,
    *,
    tickers: Sequence[str],
    as_of_date: date,
) -> list[str]:
    """Load deduplicated 360° PIT relationship map (Subsidiaries/Associates + True Peers + Macro Sensitivity)."""
    if session is None or not tickers:
        return []
    norm_tks = [t.strip().upper() for t in tickers if t and t.strip()]
    src_ids = [f"TICKER:{t}" for t in norm_tks]
    if not src_ids:
        return []

    from sqlalchemy import or_

    edge_rows = (
        await session.execute(
            select(
                KgEdge.src_node_id,
                KgEdge.dst_node_id,
                KgEdge.layer,
                KgEdge.relation_type,
                KgEdge.weight,
                KgEdge.beta,
                KgEdge.evidence_json,
            )
            .where(
                or_(KgEdge.src_node_id.in_(src_ids), KgEdge.dst_node_id.in_(src_ids)),
                KgEdge.relation_type.in_(
                    (
                        "OWNS_SUBSIDIARY",
                        "ASSOCIATE_OF",
                        "TRUE_PEER_OF",
                        "MACRO_SENSITIVE_TO",
                        "CREDITOR_OF",
                        "RELATED_PARTY_GROUP",
                    )
                ),
                KgEdge.effective_from <= as_of_date,
                KgEdge.valid_to > as_of_date,
            )
            .order_by(KgEdge.weight.desc())
            .limit(120)
        )
    ).all()
    if not edge_rows:
        return []

    node_ids_needed = sorted(
        {str(r[0]) for r in edge_rows} | {str(r[1]) for r in edge_rows}
    )
    node_rows = (
        await session.execute(
            select(KgNode.node_id, KgNode.label, KgNode.ticker).where(
                KgNode.node_id.in_(node_ids_needed),
                KgNode.effective_from <= as_of_date,
                KgNode.valid_to > as_of_date,
            )
        )
    ).all()
    node_map = {str(nid): (str(lbl or nid), str(ntk or "").strip().upper()) for nid, lbl, ntk in node_rows}

    out_lines: list[str] = []
    src_set = set(src_ids)
    for tk in norm_tks:
        tk_nid = f"TICKER:{tk}"
        corp_by_key: dict[str, tuple[tuple[int, int, float], str]] = {}
        peer_items: list[tuple[float, str]] = []
        seen_peers: set[str] = set()
        macro_items: list[str] = []
        seen_macros: set[str] = set()

        for s_id, d_id, _layer, rel, w_val, beta_val, ev_json in edge_rows:
            s_str, d_str = str(s_id), str(d_id)
            if s_str != tk_nid and d_str != tk_nid:
                continue
            ev = ev_json if isinstance(ev_json, Mapping) else {}
            if rel in ("OWNS_SUBSIDIARY", "ASSOCIATE_OF", "CREDITOR_OF", "RELATED_PARTY_GROUP") and s_str == tk_nid:
                dst_lbl, dst_tk = node_map.get(d_str, (d_str, ""))
                ckey = _canonical_corp_entity_key(dst_lbl)
                pct = ev.get("ownership_pct")
                pct_f = float(pct) if isinstance(pct, (int, float)) else None
                role_txt = (
                    "Công ty liên kết"
                    if rel == "ASSOCIATE_OF"
                    else ("Công ty con" if rel == "OWNS_SUBSIDIARY" else "Bên liên quan/tín dụng")
                )
                tk_badge = f" [{dst_tk}]" if dst_tk and dst_tk != tk else ""
                pct_txt = f" ({pct_f:.2f}%)" if pct_f is not None else ""
                rendered = f"{role_txt}: {dst_lbl}{tk_badge}{pct_txt}"
                is_strategic_sector = int(
                    not any(
                        kw in ckey
                        for kw in ("bao hiem", "chung khoan", "vien thong", "ban le", "duoc", "tai chinh")
                    )
                )
                is_assoc_or_listed = 0 if (rel == "ASSOCIATE_OF" or bool(dst_tk)) else 1
                eff_pct = pct_f if pct_f is not None else float(w_val or 0.0)
                rank_key = (is_assoc_or_listed, is_strategic_sector, -eff_pct)
                prev = corp_by_key.get(ckey)
                # Prefer entry with explicit acronym in parentheses or higher information density
                if prev is None or (("(" in dst_lbl and "(" not in prev[1]) or rank_key < prev[0]):
                    corp_by_key[ckey] = (rank_key, rendered)
            elif rel == "TRUE_PEER_OF":
                other_id = d_str if s_str == tk_nid else s_str
                if other_id == tk_nid:
                    continue
                p_lbl, p_tk = node_map.get(other_id, (other_id, ""))
                p_code = p_tk or (other_id.split(":", 1)[1] if ":" in other_id else other_id)
                if p_code not in seen_peers and p_code != tk:
                    seen_peers.add(p_code)
                    disp = f"{p_code} ({p_lbl})" if p_lbl and p_lbl != p_code else p_code
                    peer_items.append((float(w_val or 0.0), disp))
            elif rel == "MACRO_SENSITIVE_TO" and d_str == tk_nid:
                m_code = s_str.split(":", 1)[1] if ":" in s_str else s_str
                if m_code in seen_macros:
                    continue
                seen_macros.add(m_code)
                m_lbl = _MACRO_LABEL_VI.get(m_code, m_code)
                ch = str(ev.get("channel") or "")
                b_f = float(beta_val) if isinstance(beta_val, (int, float)) else 0.0
                if ch == "AUDITED_NOTE" or abs(b_f) < 1e-6:
                    macro_items.append(f"{m_lbl} (ghi nhận tại thuyết minh rủi ro BCTC)")
                else:
                    macro_items.append(f"{m_lbl} (beta={b_f:+.2f})")

        sorted_corps = [item[1] for item in sorted(corp_by_key.values(), key=lambda x: x[0])[:6]]
        if sorted_corps:
            out_lines.append(f"{tk} · Hệ sinh thái vốn & công ty con/liên kết -> " + "; ".join(sorted_corps))
        peer_items.sort(key=lambda x: -x[0])
        if peer_items:
            out_lines.append(
                f"{tk} · Đối thủ cùng cụm mô hình kinh doanh -> " + ", ".join(p[1] for p in peer_items[:3])
            )
        if macro_items:
            out_lines.append(f"{tk} · Độ nhạy vĩ mô trọng yếu -> " + "; ".join(macro_items[:3]))
        del src_set
    return out_lines


def _latest_pit_ratios_by_ticker(
    raw_ratios: Sequence[Mapping[str, Any]],
    as_of_date: date,
) -> dict[str, dict[str, Any]]:
    best: dict[str, tuple[tuple[date, int], dict[str, Any]]] = {}
    for r in raw_ratios:
        sym = str(r.get("symbol") or r.get("ticker") or "").strip().upper()
        r_date = r.get("ratio_date")
        eff_pub = r.get("effective_published_date") or r.get("published_date") or r_date
        if not sym or not isinstance(r_date, date) or not isinstance(eff_pub, date):
            continue
        if r_date > as_of_date or eff_pub > as_of_date:
            continue
        freq = str(r.get("frequency") or "quarterly").lower()
        prio = 1 if freq == "quarterly" else 0
        rk = (r_date, prio)
        prev = best.get(sym)
        if prev is None or rk > prev[0]:
            # Normalize percentage columns if > 1.5
            roe = r.get("roe")
            pe = r.get("pe")
            pb = r.get("pb")
            ni_yoy = r.get("yoy_earnings_growth")
            rev_yoy = r.get("yoy_revenue_growth")

            def _norm_pct_col(v: Any) -> float | None:
                if not isinstance(v, (int, float)):
                    return None
                fv = float(v)
                if abs(fv) > 2.5:
                    fv = fv / 100.0
                return fv

            pe_f = (
                float(pe)
                if isinstance(pe, (int, float)) and 0 < float(pe) < 250 and float(pe) != float(r_date.year)
                else None
            )
            pb_f = float(pb) if isinstance(pb, (int, float)) and 0 < float(pb) < 50 else None
            best[sym] = (
                rk,
                {
                    "ticker": sym,
                    "ratio_date": r_date,
                    "roe": _norm_pct_col(roe),
                    "pe": pe_f,
                    "pb": pb_f,
                    "net_income_yoy": _norm_pct_col(ni_yoy),
                    "revenue_yoy": _norm_pct_col(rev_yoy),
                },
            )
    return {k: v[1] for k, v in best.items()}


def _compute_sector_stats(
    sector_code: str,
    members: Sequence[Mapping[str, Any]],
    ratios_by_tk: Mapping[str, Mapping[str, Any]],
    mcaps: Mapping[str, float],
    available_tickers: frozenset[str] | set[str],
    sector_ret_1m: Mapping[str, float],
    foreign_5d_by_sec: Mapping[str, float],
) -> dict[str, Any]:
    tks = [str(m["ticker"]).upper() for m in members if m.get("ticker")]
    bellwethers = sorted(t for t in tks if t in available_tickers)
    roes = [float(ratios_by_tk[t]["roe"]) for t in tks if t in ratios_by_tk and ratios_by_tk[t].get("roe") is not None]
    pes = [float(ratios_by_tk[t]["pe"]) for t in tks if t in ratios_by_tk and ratios_by_tk[t].get("pe") is not None]
    pbs = [float(ratios_by_tk[t]["pb"]) for t in tks if t in ratios_by_tk and ratios_by_tk[t].get("pb") is not None]
    ni_yoys = [
        (t, float(ratios_by_tk[t]["net_income_yoy"]))
        for t in tks
        if t in ratios_by_tk and ratios_by_tk[t].get("net_income_yoy") is not None
    ]
    pos_ni = sum(1 for _, g in ni_yoys if g > 0)
    pos_share = (pos_ni / len(ni_yoys)) if ni_yoys else None

    ni_sorted = sorted(ni_yoys, key=lambda kv: (-kv[1], -float(mcaps.get(kv[0]) or 0.0)))
    leaders = ni_sorted[:2]
    laggards = (
        list(reversed(ni_sorted[-2:])) if len(ni_sorted) >= 3 else (ni_sorted[-1:] if len(ni_sorted) == 2 else [])
    )

    return {
        "sector_code": sector_code,
        "sector_label_vi": SECTOR_DISPLAY_VI.get(sector_code, sector_code),
        "member_count": len(tks),
        "evaluated_growth_count": len(ni_yoys),
        "positive_growth_count": pos_ni,
        "positive_growth_share": pos_share,
        "median_ni_yoy": statistics.median([g for _, g in ni_yoys]) if ni_yoys else None,
        "median_roe": statistics.median(roes) if roes else None,
        "median_pe": statistics.median(pes) if pes else None,
        "median_pb": statistics.median(pbs) if pbs else None,
        "price_ret_1m": sector_ret_1m.get(sector_code),
        "foreign_net_5d_vnd": foreign_5d_by_sec.get(sector_code),
        "leaders": leaders,
        "laggards": laggards,
        "bellwethers": bellwethers,
    }


async def build_scope_gated_briefing(
    ctx: ResearchToolContext,
    *,
    query: str,
    explicit_tickers: Sequence[str] = (),
    fallback_tickers: Sequence[str] = (),
) -> ScopeBriefingBundle:
    """Build PIT-exact, scope-gated briefing (`0` LLM tokens, `<1ms` on same-day cache hit)."""
    lens_res = resolve_query_scope_lens(
        query=query,
        explicit_tickers=explicit_tickers,
        fallback_tickers=fallback_tickers,
        catalog_profiles=ctx.catalog_profiles,
        available_tickers=tuple(ctx.available_tickers),
    )

    if lens_res.lens == "COMPANY":
        scope_key = "CO:" + (",".join(lens_res.matched_tickers) or "QUANT_SCREEN")
    elif lens_res.lens == "SECTOR":
        scope_key = "SEC:" + ",".join(lens_res.matched_sectors)
    else:
        scope_key = f"MKT:{lens_res.lens}"

    avail_sig = ",".join(sorted(ctx.available_tickers)[:30])
    cache_key = (ctx.tenant_id, f"v3.2:{scope_key}", ctx.as_of_date.isoformat(), avail_sig)
    cached = _PULSE_CACHE.get(cache_key)
    if cached is not None:
        _PULSE_CACHE.move_to_end(cache_key)
        return cached

    allowed = ctx.principal.allowed_project_ids

    # ── LENS 1: COMPANY (Zero Market-Wide Anchoring Bias!) ────────────────────
    if lens_res.lens == "COMPANY":
        tks = lens_res.matched_tickers
        if not tks:
            bundle = ScopeBriefingBundle(lens="COMPANY", as_of_date=ctx.as_of_date)
            return bundle

        pointers: list[StructuralPointer] = []
        sub_lines: list[str] = []
        try:
            async with ctx.session_factory() as sess:
                if isinstance(sess, AsyncSession):
                    pointers = await load_pit_structural_pointers(
                        sess,
                        tenant_id=ctx.tenant_id,
                        as_of_date=ctx.as_of_date,
                        tickers=tks,
                        allowed_project_ids=allowed,
                        limit_per_ticker=7,
                        max_total=14,
                    )
                    sub_lines = await _load_pit_subsidiary_summary(
                        sess,
                        tickers=tks,
                        as_of_date=ctx.as_of_date,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.debug("Company structural pointer lookup skipped: %s", exc)

        lines: list[str] = []
        if sub_lines:
            lines.append("Bản đồ Hệ sinh thái Vốn, Đối thủ & Độ nhạy Vĩ mô PIT:")
            lines.extend(f"  • {sl}" for sl in sub_lines[:6])
        if pointers:
            lines.append(
                "Trục Xương sống Câu chuyện Doanh nghiệp (Story Spine — các đỉnh Chiến lược, Thị trường, "
                "Công nghệ, Sở hữu & Chất vấn ĐHĐCĐ cần khai thác sâu):"
            )
            for p in pointers[:10]:
                sig_lbl = ", ".join(_SIGNAL_LABEL_VI.get(s, s) for s in p.signals[:2])
                lines.append(f"  • [{p.ticker} · {p.sub_doc_slot} tr.{p.page_from} | {sig_lbl}] {p.excerpt}")

        bundle = ScopeBriefingBundle(
            lens="COMPANY",
            as_of_date=ctx.as_of_date,
            target_tickers=list(tks),
            bellwether_tickers=[t for t in tks if t in ctx.available_tickers],
            structural_pointers=pointers,
            briefing_text="\n".join(lines),
        )
        _store_pulse_cache(cache_key, bundle)
        return bundle

    # ── LENS 2 & 3: SECTOR / MARKET / OPEN_STRATEGY ───────────────────────────
    profiles: list[dict[str, Any]] = []
    ratios_by_tk: dict[str, dict[str, Any]] = {}
    mcaps: dict[str, float] = {}
    tape: dict[str, Any] = {}

    if ctx.core_reader is not None:
        try:
            raw_profs, raw_ratios, raw_mcaps, raw_tape = await _gather_core_pulse_inputs(
                ctx.core_reader,
                ctx.as_of_date,
            )
            for p in raw_profs:
                sym = str(p.get("ticker") or p.get("symbol") or "").strip().upper()
                if sym and ("*" in allowed or sym in allowed):
                    profiles.append({**dict(p), "ticker": sym})
            ratios_by_tk = _latest_pit_ratios_by_ticker(raw_ratios, ctx.as_of_date)
            mcaps = dict(raw_mcaps or {})
            tape = dict(raw_tape or {})
        except Exception as exc:  # noqa: BLE001
            logger.warning("Market/Sector Core PG pulse read degraded (%s)", type(exc).__name__)

    if not profiles and ctx.catalog_profiles:
        for p in ctx.catalog_profiles:
            sym = str(p.get("ticker") or "").strip().upper()
            if sym and ("*" in allowed or sym in allowed):
                profiles.append(dict(p))

    by_sector: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in profiles:
        sec = str(p.get("sector") or "").strip().upper() or "OTHER_INDUSTRIALS"
        by_sector[sec].append(p)

    sec_ret_1m = tape.get("sector_ret_1m") or {}
    for_5d_sec = tape.get("foreign_net_5d_by_sector") or {}

    if lens_res.lens == "SECTOR":
        target_secs = lens_res.matched_sectors or ["BANKS"]
        sec_members = [p for sc in target_secs for p in by_sector.get(sc, ())]
        primary_sec = target_secs[0]
        st = _compute_sector_stats(
            primary_sec,
            sec_members,
            ratios_by_tk,
            mcaps,
            ctx.available_tickers,
            sec_ret_1m,
            for_5d_sec,
        )
        st["sector_label_vi"] = lens_res.matched_sector_label_vi or st["sector_label_vi"]
        bellwethers = st["bellwethers"]

        pointers = []
        try:
            async with ctx.session_factory() as sess:
                if isinstance(sess, AsyncSession) and bellwethers:
                    pointers = await load_pit_structural_pointers(
                        sess,
                        tenant_id=ctx.tenant_id,
                        as_of_date=ctx.as_of_date,
                        tickers=bellwethers[:6],
                        allowed_project_ids=allowed,
                        limit_per_ticker=3,
                        max_total=12,
                    )
        except Exception:  # noqa: BLE001
            pointers = []

        calc_inputs: dict[str, dict[str, Any]] = {
            "sector_member_count": {"value": float(st["member_count"]), "unit": "NUMBER"},
            "indexed_bellwether_count": {"value": float(len(bellwethers)), "unit": "NUMBER"},
        }
        if st["positive_growth_share"] is not None:
            calc_inputs["positive_growth_share"] = {"value": float(st["positive_growth_share"]), "unit": "RATIO"}
            calc_inputs["positive_growth_count"] = {"value": float(st["positive_growth_count"]), "unit": "NUMBER"}
            calc_inputs["evaluated_growth_count"] = {"value": float(st["evaluated_growth_count"]), "unit": "NUMBER"}
        if st["median_ni_yoy"] is not None:
            calc_inputs["median_ni_yoy"] = {"value": float(st["median_ni_yoy"]), "unit": "RATIO"}
        if st["median_roe"] is not None:
            calc_inputs["median_roe"] = {"value": float(st["median_roe"]), "unit": "RATIO"}
        if st["median_pe"] is not None:
            calc_inputs["median_pe"] = {"value": float(st["median_pe"]), "unit": "X"}
        if st["median_pb"] is not None:
            calc_inputs["median_pb"] = {"value": float(st["median_pb"]), "unit": "X"}
        if st["price_ret_1m"] is not None:
            calc_inputs["price_ret_1m"] = {"value": float(st["price_ret_1m"]), "unit": "RATIO"}
        if st["foreign_net_5d_vnd"] is not None:
            calc_inputs["foreign_net_5d_vnd"] = {"value": float(st["foreign_net_5d_vnd"]), "unit": "VND"}
        for tk_l, g_l in [*st["leaders"], *st["laggards"]]:
            calc_inputs[f"{tk_l}_ni_yoy"] = {"value": float(g_l), "unit": "RATIO"}

        lead_txt = ", ".join(f"{tk} ({_fmt_pct(g)})" for tk, g in st["leaders"]) or "chưa đủ dữ liệu"
        lag_txt = ", ".join(f"{tk} ({_fmt_pct(g)})" for tk, g in st["laggards"]) or "chưa đủ dữ liệu"
        summary_line = (
            f"Toàn ngành {st['sector_label_vi']} (PIT {ctx.as_of_date.isoformat()}, "
            f"quy mô {st['member_count']} mã niêm yết Core PG): "
            f"{st['positive_growth_count']}/{st['evaluated_growth_count']} mã có số liệu đạt "
            f"tăng trưởng LNST dương YoY ({_fmt_pct(st['positive_growth_share'], signed=False)}), "
            f"trung vị tăng trưởng LNST {_fmt_pct(st['median_ni_yoy'])}, "
            f"trung vị ROE {_fmt_pct(st['median_roe'], signed=False)}, "
            f"P/E {_fmt_mult(st['median_pe'])}, P/B {_fmt_mult(st['median_pb'])}; "
            f"biến động giá 1M {_fmt_pct(st['price_ret_1m'])}, "
            f"khối ngoại 5 phiên {_fmt_t_vnd(st['foreign_net_5d_vnd'])}. "
            f"Dẫn dắt tăng trưởng: {lead_txt} | Chịu áp lực: {lag_txt}."
        )
        bell_note = (
            f"Nhóm đầu ngành đã nạp hồ sơ PDF thuyết minh & ĐHĐCĐ sâu trong SAG "
            f"({len(bellwethers)}/{st['member_count']} mã): "
            + (", ".join(bellwethers) if bellwethers else "chưa có mã nào nạp PDF")
            + " — KHÔNG ngoại suy phát ngôn thuyết minh của nhóm này thành toàn bộ các mã chưa nạp PDF."
        )
        ptr_lines = [
            f"  • [{p.ticker} · {p.sub_doc_slot} tr.{p.page_from} | "
            f"{', '.join(_SIGNAL_LABEL_VI.get(s, s) for s in p.signals)}] {p.excerpt}"
            for p in pointers[:8]
        ]
        brief_txt = "\n".join(
            [
                f"[BẢN ĐỒ PHÂN HÓA NGÀNH {st['sector_label_vi'].upper()} — PIT {ctx.as_of_date.isoformat()}]",
                f"- {summary_line}",
                f"- {bell_note}",
                *(["- Con trỏ Thuyết minh & ĐHĐCĐ của các mã đầu ngành:"] + ptr_lines if ptr_lines else []),
            ]
        )

        scr_rows = []
        for tk_s in bellwethers[:6] or [tk for tk, _ in st["leaders"]]:
            r_tk = ratios_by_tk.get(tk_s, {})
            p_tk = next((m for m in sec_members if m["ticker"] == tk_s), {})
            scr_rows.append(
                {
                    "ticker": tk_s,
                    "company_name": p_tk.get("company_name") or tk_s,
                    "sector": st["sector_label_vi"],
                    "roe": r_tk.get("roe"),
                    "pe": r_tk.get("pe"),
                    "pb": r_tk.get("pb"),
                    "valid_from": ctx.as_of_date.isoformat(),
                    "source_kind": "SECTOR_PULSE_PIT",
                }
            )

        bundle = ScopeBriefingBundle(
            lens="SECTOR",
            as_of_date=ctx.as_of_date,
            matched_sectors=list(target_secs),
            matched_sector_label_vi=st["sector_label_vi"],
            target_tickers=bellwethers[:5] or [tk for tk, _ in st["leaders"]],
            full_universe_count=st["member_count"],
            bellwether_tickers=bellwethers,
            structural_pointers=pointers,
            briefing_text=brief_txt,
            calc_traces=[
                {
                    "formula": "sector_pulse_aggregates",
                    "description": (
                        f"Tổng hợp toàn ngành {st['sector_label_vi']} trên {st['member_count']} mã Core PG "
                        f"(PIT {ctx.as_of_date.isoformat()})"
                    ),
                    "inputs": calc_inputs,
                    "result": st["median_roe"],
                    "formatted": summary_line,
                    "ticker": "*",
                    "format_style": "percent",
                    "source": "SECTOR_PULSE",
                }
            ],
            screening_rows=scr_rows,
            screening_description=(
                f"Tổng hợp ngành {st['sector_label_vi']} "
                f"({st['member_count']} mã Core PG, {len(bellwethers)} mã đầu ngành có PDF)"
            ),
        )
        _store_pulse_cache(cache_key, bundle)
        return bundle

    # ── MARKET / OPEN_STRATEGY World-State Snapshot ───────────────────────────
    sec_stats: list[dict[str, Any]] = []
    for sc, members in by_sector.items():
        if len(members) < 1:
            continue
        sec_stats.append(
            _compute_sector_stats(
                sc,
                members,
                ratios_by_tk,
                mcaps,
                ctx.available_tickers,
                sec_ret_1m,
                for_5d_sec,
            )
        )
    sec_stats.sort(
        key=lambda x: (
            1 if x["median_ni_yoy"] is not None else 0,
            float(x["median_ni_yoy"] or -999.0),
            float(x["median_roe"] or 0.0),
        ),
        reverse=True,
    )

    all_eval = sum(s["evaluated_growth_count"] for s in sec_stats)
    all_pos = sum(s["positive_growth_count"] for s in sec_stats)
    mkt_pos_share = (all_pos / all_eval) if all_eval > 0 else None

    bellwether_all = sorted(ctx.available_tickers)
    pointers = []
    try:
        async with ctx.session_factory() as sess:
            if isinstance(sess, AsyncSession) and bellwether_all:
                pointers = await load_pit_structural_pointers(
                    sess,
                    tenant_id=ctx.tenant_id,
                    as_of_date=ctx.as_of_date,
                    tickers=bellwether_all[:12],
                    allowed_project_ids=allowed,
                    limit_per_ticker=2,
                    max_total=10,
                )
    except Exception:  # noqa: BLE001
        pointers = []

    trade_d = tape.get("latest_trade_date")
    trade_d_str = trade_d.isoformat() if isinstance(trade_d, date) else ctx.as_of_date.isoformat()
    vni_close = tape.get("vnindex_close")
    ret_1d = tape.get("vnindex_ret_1d")
    ret_1m = tape.get("vnindex_ret_1m")
    ret_3m = tape.get("vnindex_ret_3m")
    liq_ratio = tape.get("liquidity_vs_20d_ratio")
    adv = tape.get("advancers")
    dec = tape.get("decliners")
    f5d = tape.get("foreign_net_5d_vnd")
    f20d = tape.get("foreign_net_20d_vnd")
    reg = tape.get("regime") or {}
    macro_map = tape.get("macro") or {}

    tape_inputs: dict[str, dict[str, Any]] = {
        "universe_stock_count": {"value": float(len(profiles)), "unit": "NUMBER"},
        "indexed_bellwether_count": {"value": float(len(bellwether_all)), "unit": "NUMBER"},
    }
    if all_eval > 0 and mkt_pos_share is not None:
        tape_inputs["evaluated_earnings_count"] = {"value": float(all_eval), "unit": "NUMBER"}
        tape_inputs["positive_earnings_count"] = {"value": float(all_pos), "unit": "NUMBER"}
        tape_inputs["positive_earnings_share"] = {"value": float(mkt_pos_share), "unit": "RATIO"}
    if isinstance(vni_close, (int, float)):
        tape_inputs["vnindex_close"] = {"value": float(vni_close), "unit": "NUMBER"}
    for k_r, v_r in (("vnindex_ret_1d", ret_1d), ("vnindex_ret_1m", ret_1m), ("vnindex_ret_3m", ret_3m)):
        if isinstance(v_r, (int, float)):
            tape_inputs[k_r] = {"value": float(v_r), "unit": "RATIO"}
    if isinstance(liq_ratio, (int, float)):
        tape_inputs["liquidity_vs_20d_ratio"] = {"value": float(liq_ratio), "unit": "X"}
    if isinstance(adv, int):
        tape_inputs["advancers"] = {"value": float(adv), "unit": "NUMBER"}
    if isinstance(dec, int):
        tape_inputs["decliners"] = {"value": float(dec), "unit": "NUMBER"}
    if isinstance(f5d, (int, float)):
        tape_inputs["foreign_net_5d_vnd"] = {"value": float(f5d), "unit": "VND"}
    if isinstance(f20d, (int, float)):
        tape_inputs["foreign_net_20d_vnd"] = {"value": float(f20d), "unit": "VND"}
    if isinstance(reg.get("vix_vn_analog"), (int, float)):
        tape_inputs["vix_vn_analog"] = {"value": float(reg["vix_vn_analog"]), "unit": "NUMBER"}
    if isinstance(reg.get("breadth_above_ma50_pct"), (int, float)):
        tape_inputs["breadth_above_ma50_pct"] = {"value": float(reg["breadth_above_ma50_pct"]) / 100.0, "unit": "RATIO"}

    macro_parts: list[str] = []
    for mk, m_lbl in (
        ("usd_vnd_exchange", "Tỷ giá USD/VND"),
        ("vninbr_interbank_rate", "Lãi suất liên ngân hàng"),
        ("oil_price_brent", "Dầu Brent"),
        ("usd_10y_yield", "Lợi suất TPCP Mỹ 10Y"),
    ):
        m_entry = macro_map.get(mk)
        if isinstance(m_entry, Mapping) and isinstance(m_entry.get("latest"), (int, float)):
            lv = float(m_entry["latest"])
            unit_s = str(m_entry.get("unit") or "").strip()
            is_pct_macro = unit_s == "%" or mk.endswith(("_rate", "_yield", "_pct", "_inflation"))
            tape_inputs[mk] = {"value": lv, "unit": "%" if is_pct_macro else "NUMBER"}
            chg = m_entry.get("change_1m_pct")
            if isinstance(chg, (int, float)):
                tape_inputs[f"{mk}_chg_1m"] = {"value": float(chg), "unit": "RATIO"}
            chg_txt = f" ({_fmt_pct(chg)} 1M)" if isinstance(chg, (int, float)) else ""
            macro_parts.append(f"{m_lbl}: {lv:,.2f} {unit_s}{chg_txt}".strip())

    lines_mkt: list[str] = [
        f"[BẢN ĐỒ TOÀN CẢNH THỊ TRƯỜNG — KHÓA SỔ PIT {ctx.as_of_date.isoformat()} "
        f"| PHIÊN GẦN NHẤT <= PIT: {trade_d_str}]"
    ]
    if isinstance(vni_close, (int, float)):
        reg_txt = (
            f" | Trạng thái HMM: {reg.get('current_regime')} "
            f"(tỷ lệ mã trên MA50: {float(reg['breadth_above_ma50_pct']):.1f}%)"
            if reg.get("current_regime") and isinstance(reg.get("breadth_above_ma50_pct"), (int, float))
            else ""
        )
        lines_mkt.append(
            f"- Nhịp đập Giao dịch ({trade_d_str}): VN-Index đóng cửa tại {vni_close:,.1f} điểm "
            f"(1D {_fmt_pct(ret_1d)}, 1M {_fmt_pct(ret_1m)}, 3M {_fmt_pct(ret_3m)}), "
            f"thanh khoản bằng {_fmt_mult(liq_ratio)} trung bình 20 phiên, "
            f"độ rộng {adv or 0} mã tăng / {dec or 0} mã giảm; "
            f"khối ngoại 5 phiên {_fmt_t_vnd(f5d)} (20 phiên {_fmt_t_vnd(f20d)}){reg_txt}."
        )
    else:
        lines_mkt.append(
            f"- Phạm vi dữ liệu cơ bản PIT {ctx.as_of_date.isoformat()}: {len(profiles)} doanh nghiệp niêm yết "
            f"({len(bellwether_all)} doanh nghiệp đầu ngành đã nạp thuyết minh PDF sâu trong SAG)."
        )

    if all_eval > 0:
        lines_mkt.append(
            f"- Độ rộng Lợi nhuận Toàn sàn ({len(profiles)} mã Core PG): {all_pos}/{all_eval} mã có báo cáo đạt "
            f"tăng trưởng LNST dương YoY ({_fmt_pct(mkt_pos_share, signed=False)})."
        )

    if sec_stats:
        top_secs = sec_stats[:3]
        bot_secs = list(reversed(sec_stats[-2:])) if len(sec_stats) >= 4 else []
        for s_item in sec_stats:
            sc_k = s_item["sector_code"]
            tape_inputs[f"{sc_k}_member_count"] = {"value": float(s_item["member_count"]), "unit": "NUMBER"}
            if s_item["positive_growth_share"] is not None:
                tape_inputs[f"{sc_k}_pos_growth_share"] = {
                    "value": float(s_item["positive_growth_share"]),
                    "unit": "RATIO",
                }
            if s_item["median_ni_yoy"] is not None:
                tape_inputs[f"{sc_k}_median_ni_yoy"] = {"value": float(s_item["median_ni_yoy"]), "unit": "RATIO"}
            if s_item["median_roe"] is not None:
                tape_inputs[f"{sc_k}_median_roe"] = {"value": float(s_item["median_roe"]), "unit": "RATIO"}
            if s_item["median_pe"] is not None:
                tape_inputs[f"{sc_k}_median_pe"] = {"value": float(s_item["median_pe"]), "unit": "X"}
            if s_item["median_pb"] is not None:
                tape_inputs[f"{sc_k}_median_pb"] = {"value": float(s_item["median_pb"]), "unit": "X"}
            if s_item["price_ret_1m"] is not None:
                tape_inputs[f"{sc_k}_ret_1m"] = {"value": float(s_item["price_ret_1m"]), "unit": "RATIO"}
            if s_item["foreign_net_5d_vnd"] is not None:
                tape_inputs[f"{sc_k}_foreign_5d_vnd"] = {
                    "value": float(s_item["foreign_net_5d_vnd"]),
                    "unit": "VND",
                }
        top_txt = "; ".join(
            f"{s['sector_label_vi']} ({s['member_count']} mã: trung vị LNST {_fmt_pct(s['median_ni_yoy'])}, "
            f"ROE {_fmt_pct(s['median_roe'], signed=False)}, "
            f"P/E {_fmt_mult(s['median_pe'])}, P/B {_fmt_mult(s['median_pb'])}"
            + (f", đầu ngành có PDF: {', '.join(s['bellwethers'][:3])}" if s["bellwethers"] else "")
            + ")"
            for s in top_secs
        )
        lines_mkt.append(f"- Nhóm Ngành Dẫn dắt Lợi nhuận: {top_txt}.")
        if bot_secs:
            bot_txt = "; ".join(
                f"{s['sector_label_vi']} ({s['member_count']} mã: trung vị LNST {_fmt_pct(s['median_ni_yoy'])}, "
                f"P/B {_fmt_mult(s['median_pb'])})"
                for s in bot_secs
            )
            lines_mkt.append(f"- Nhóm Ngành Phân hóa / Suy yếu: {bot_txt}.")

    if macro_parts:
        lines_mkt.append("- Biến số Vĩ mô PIT: " + " | ".join(macro_parts) + ".")

    if pointers:
        lines_mkt.append(
            f"- Điểm nóng Thuyết minh & ĐHĐCĐ từ rổ đầu ngành đã nạp PDF "
            f"({len(bellwether_all)} mã SAG — phân biệt rõ với toàn sàn {len(profiles)} mã):"
        )
        for p in pointers[:6]:
            sig_lbl = ", ".join(_SIGNAL_LABEL_VI.get(s, s) for s in p.signals)
            lines_mkt.append(f"  • [{p.ticker} · {p.sub_doc_slot} tr.{p.page_from} | {sig_lbl}] {p.excerpt}")

    summary_core_lines = [ln for ln in lines_mkt[1:] if not ln.startswith("  •") and "Điểm nóng Thuyết minh" not in ln]
    formatted_summary = " ".join(ln.lstrip("- ") for ln in summary_core_lines)
    rep_tickers: list[str] = []
    for s in sec_stats[:3]:
        for bt in s["bellwethers"][:2]:
            if bt not in rep_tickers:
                rep_tickers.append(bt)
    if not rep_tickers:
        rep_tickers = bellwether_all[:4]

    bundle = ScopeBriefingBundle(
        lens=lens_res.lens,
        as_of_date=ctx.as_of_date,
        target_tickers=rep_tickers[:5],
        full_universe_count=len(profiles),
        bellwether_tickers=bellwether_all,
        structural_pointers=pointers,
        briefing_text="\n".join(lines_mkt),
        calc_traces=[
            {
                "formula": "market_world_state_snapshot",
                "description": (
                    f"Tổng hợp toàn cảnh thị trường & phân hóa ngành "
                    f"(PIT {ctx.as_of_date.isoformat()}, phiên {trade_d_str})"
                ),
                "inputs": tape_inputs,
                "result": float(vni_close) if isinstance(vni_close, (int, float)) else mkt_pos_share,
                "formatted": formatted_summary[:1400],
                "ticker": "*",
                "format_style": "number",
                "source": "MARKET_PULSE",
            }
        ],
    )
    _store_pulse_cache(cache_key, bundle)
    return bundle


async def register_pulse_evidence_into_ledger(
    bundle: ScopeBriefingBundle,
    evidence: ResearchEvidenceLedger,
) -> tuple[list[str], list[str]]:
    """Register `ScopeBriefingBundle` quantitative traces into `ResearchEvidenceLedger` (`[CALC-*]`, `[SCR:*]`)."""
    lines: list[str] = []
    badges: list[str] = []
    if bundle.screening_rows:
        await evidence.register_screening(bundle.screening_rows, bundle.screening_description)
        for r in bundle.screening_rows[:6]:
            tk = str(r.get("ticker") or "")
            if tk:
                badges.append(f"[SCR:{tk}]")
    for trace in bundle.calc_traces:
        saved = await evidence.register_calculator_trace(trace)
        cid = str(saved["calc_id"])
        badges.append(f"[{cid}]")
        lines.append(f"[{cid}] {saved.get('formatted') or saved.get('description')}")
    return lines, badges


async def _gather_core_pulse_inputs(core_reader: Any, as_of_date: date) -> tuple[Any, Any, Any, Any]:
    import asyncio

    return await asyncio.gather(
        core_reader.fetch_issuer_profiles(),
        core_reader.fetch_pit_financial_ratios(as_of_date, lookback_years=2),
        core_reader.fetch_pit_market_caps(as_of_date),
        core_reader.fetch_pit_market_and_macro_tape(as_of_date),
    )


def _store_pulse_cache(key: tuple[str, str, str, str], bundle: ScopeBriefingBundle) -> None:
    _PULSE_CACHE[key] = bundle
    while len(_PULSE_CACHE) > _MAX_CACHE_ENTRIES:
        _PULSE_CACHE.popitem(last=False)
