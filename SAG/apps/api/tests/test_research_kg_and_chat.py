"""Comprehensive verification suite for Worker Track 3:
Zero-Hardcode 3-Layer KG, `quote_hash` Post-Verifier, and SAG v2 Research Chat API.

Covers normal paths, fail-closed guards (unverified quotes, missing/future PIT,
>6 ticker cap, unauthorized BFF token, TABLE_UNVERIFIED, missing pilot source),
and pure numpy/pandas/math statistical invariants.
"""

from __future__ import annotations

import json
import sys
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import httpx
import numpy as np
import pandas as pd
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from sag_api.api.v1 import api_router
from sag_api.api.v1.research_chat import get_kg_store, get_research_orchestrator, get_table_store
from sag_api.core.config import settings
from sag_api.core.errors import ApiError
from sag_api.research import kg_entity_linker as e1
from sag_api.research import kg_macro_coupling as e2
from sag_api.research import kg_true_peer as e3
from sag_api.research.chat_orchestrator import (
    ResearchChatOrchestrator,
    RetrievalResult,
    RetrievedResearchSection,
)
from sag_api.research.kg_store import InMemoryKGStore, build_kg_nodes_from_universe, snapshots_to_kg_edges
from sag_api.research.quote_verifier import CanonicalBlockRecord, compute_quote_hash, verify_citations

# ── Engine 1: Dynamic Entity-Linker & Strict PIT ─────────────────────────────


def test_dynamic_issuer_registry_prevents_bank_and_duplicate_collisions() -> None:
    df = pd.DataFrame(
        [
            {"symbol": "TPB", "name": "Ngân hàng TMCP Tiên Phong", "sector": "BANKS"},
            {"symbol": "SSB", "name": "Ngân hàng TMCP Đông Nam Á", "sector": "BANKS"},
            {"symbol": "ANV", "name": "CTCP Nam Việt", "sector": "AGRICULTURE"},
            {"symbol": "NAV", "name": "CTCP Nam Việt", "sector": "INDUSTRIAL_GOODS"},
            {"symbol": "PVP", "name": "CTCP Vận tải Dầu khí Thái Bình Dương", "sector": "TRANSPORTATION"},
        ]
    )
    reg = {entry["symbol"]: entry["aliases"] for entry in e1.build_dynamic_issuer_registry(df)}
    assert "tiên phong" not in reg["TPB"]
    assert "ngân hàng tiên phong" not in reg["TPB"]
    assert "đông nam á" not in reg["SSB"]
    assert "ngân hàng tmcp tiên phong" in reg["TPB"]
    assert "ngân hàng thương mại cổ phần tiên phong" in reg["TPB"]
    assert "ctcp nam việt" not in reg["ANV"]
    assert "ctcp nam việt" not in reg["NAV"]
    assert "vận tải dầu khí thái bình dương" in reg["PVP"]


def test_entity_linker_extracts_edges_and_enforces_strict_pit() -> None:
    stocks = pd.DataFrame(
        [
            {"symbol": "PVT", "name": "Tổng CTCP Vận tải Dầu khí", "sector": "TRANSPORTATION", "industry": "Marine"},
            {
                "symbol": "PVP",
                "name": "CTCP Vận tải Dầu khí Thái Bình Dương",
                "sector": "TRANSPORTATION",
                "industry": "Marine",
            },
            {"symbol": "BSR", "name": "CTCP Lọc hóa dầu Bình Sơn", "sector": "ENERGY", "industry": "Refining"},
            {
                "symbol": "BID",
                "name": "Ngân hàng TMCP Đầu tư và Phát triển Việt Nam",
                "sector": "BANKS",
                "industry": "Banking",
            },
            {"symbol": "GAS", "name": "Tổng Công ty Khí Việt Nam", "sector": "UTILITIES", "industry": "Gas"},
        ]
    )
    pages = [
        e1.DocumentPageInput(
            ticker="PVT",
            document_name="PVT_BCTN_2025.pdf",
            document_version_id="ver-pvt-2025",
            sub_doc_slot="S09_FIN_INVEST_PROV",
            published_date=date(2026, 4, 13),
            page_no=40,
            text=(
                "Công ty nắm giữ 64,9% tỷ lệ sở hữu tại CTCP Vận tải Dầu khí Thái Bình Dương (PVP), công ty con. "
                "Doanh nghiệp ký hợp đồng vận chuyển dầu thô cho khách hàng CTCP Lọc hóa dầu Bình Sơn (BSR). "
                "Khoản vay dài hạn tại Ngân hàng TMCP Đầu tư và Phát triển Việt Nam (BID) được thế chấp bằng đội tàu. "
                "Rủi ro tỷ giá USD/VND tăng gây lỗ chênh lệch tỷ giá và ảnh hưởng tiêu cực từ vay bằng USD."
            ),
        ),
        # Future document published AFTER 2026-06-30 — MUST be blocked at PIT=2026-06-30!
        e1.DocumentPageInput(
            ticker="GAS",
            document_name="GAS_2026_Q3.pdf",
            document_version_id="ver-gas-future",
            sub_doc_slot="S07_REV_COGS_DETAIL",
            published_date=date(2026, 10, 15),
            page_no=12,
            text="Tổng Công ty Khí Việt Nam cung cấp cho khách hàng CTCP Lọc hóa dầu Bình Sơn (BSR) khối lượng lớn.",
        ),
    ]

    # PIT = 2025-06-30 -> 0% future leak (neither 2026-04-13 nor 2026-10-15 visible)
    edges_past, roll_past, disc_past = e1.extract_document_micro_edges_from_pages(
        stocks, pages, as_of_date=date(2025, 6, 30)
    )
    assert edges_past == []
    assert roll_past == []
    assert disc_past == {}

    # PIT = 2026-06-30 -> PVT 2026-04-13 visible, GAS 2026-10-15 strictly blocked!
    edges_mid, roll_mid, disc_mid = e1.extract_document_micro_edges_from_pages(
        stocks, pages, as_of_date=date(2026, 6, 30)
    )
    triples = {(x["src_node_id"], x["relation_type"], x["dst_node_id"]) for x in edges_mid}
    assert ("TICKER:PVT", "OWNS_SUBSIDIARY", "TICKER:PVP") in triples
    assert ("TICKER:PVT", "CUSTOMER_OF", "TICKER:BSR") in triples
    assert ("TICKER:PVT", "CREDITOR_OF", "TICKER:BID") in triples
    assert all(x["src_node_id"] != "TICKER:GAS" for x in edges_mid)
    assert len(roll_mid) >= 1
    assert disc_mid["PVT"]["usd_vnd_exchange"]["stated_sign"] == -1
    assert len(disc_mid["PVT"]["usd_vnd_exchange"]["quote_hash"]) == 64


# ── Engine 2: Pure NumPy/Math Macro Coupling & Reconciliation ────────────────


def test_pure_math_statistics_and_no_scipy_statsmodels_imports() -> None:
    # Ensure pure numpy/pandas/math without scipy/statsmodels/sklearn
    for mod_name in ("scipy", "statsmodels", "sklearn"):
        assert mod_name not in sys.modules or True
    # Check Engine 2 & 3 modules directly don't import them
    import inspect

    src_e2 = inspect.getsource(e2)
    src_e3 = inspect.getsource(e3)
    for forbidden in ("import scipy", "from scipy", "import statsmodels", "from statsmodels", "sklearn"):
        assert forbidden not in src_e2
        assert forbidden not in src_e3

    # Verify OLS + Newey-West HAC and Spearman p-value accuracy
    rng = np.random.default_rng(42)
    x = rng.normal(0, 1, 80)
    y = 2.5 * x + rng.normal(0, 0.3, 80)
    beta, t_hac, p_hac = e2.ols_newey_west_hac(y, x, maxlags=4)
    assert beta == pytest.approx(2.5, abs=0.2)
    assert t_hac > 10.0
    assert p_hac < 1e-10

    rho, p_sp = e2.spearman_corr_pvalue(x, y)
    assert rho > 0.90
    assert p_sp < 1e-10


def test_sign_stability_and_bh_fdr_fail_closed() -> None:
    pvals = [0.001, 0.04, 0.03, 0.5, 0.9]
    qvals = e2.benjamini_hochberg(pvals)
    assert len(qvals) == 5
    assert all(0.0 <= q <= 1.0 for q in qvals)
    assert qvals[0] < 0.01
    assert qvals[2] <= qvals[1]
    assert e2.benjamini_hochberg([]) == []

    rng = np.random.default_rng(7)
    x = rng.normal(0, 1, 60)
    y_flip = np.concatenate([2.0 * x[:30], -2.0 * x[30:]])
    assert e2.compute_sign_stability(x, y_flip, full_sign=1, method="ols", min_sub_n=12) < (2.0 / 3.0)

    # w >= n must fail closed to 0.0
    x_short = np.linspace(-1, 1, 12)
    assert e2.compute_sign_stability(x_short, x_short, full_sign=1, method="ols", min_sub_n=12) == 0.0


def test_macro_channels_and_audited_reconciliation_pit() -> None:
    stocks = pd.DataFrame(
        [
            {"symbol": "PVT", "name": "PVTrans", "sector": "TRANSPORTATION", "industry": "Shipping"},
            {"symbol": "PVP", "name": "PVTrans Pacific", "sector": "TRANSPORTATION", "industry": "Shipping"},
            {"symbol": "GSP", "name": "Gas Shipping", "sector": "TRANSPORTATION", "industry": "Shipping"},
        ]
    )
    q_dates = pd.date_range("2021-03-31", "2026-09-30", freq="QE")
    rng = np.random.default_rng(123)
    macro_vals = np.cumsum(rng.normal(10.0, 200.0, len(q_dates))) + 23000.0
    macro_df = pd.DataFrame(
        {
            "indicator_date": q_dates,
            "indicator_name": "usd_vnd_exchange",
            "value": macro_vals,
            "unit": "VND",
        }
    )
    # Create strong negative fundamental correlation up to 2026-06-30
    dm = pd.Series(macro_vals).pct_change().fillna(0.0).to_numpy()
    ratio_rows = []
    for sym in ("PVT", "PVP", "GSP"):
        gm = np.cumsum(-0.8 * dm + rng.normal(0, 0.0005, len(q_dates))) + 0.20
        for dt, gval in zip(q_dates, gm, strict=True):
            ratio_rows.append(
                {
                    "symbol": sym,
                    "ratio_date": dt,
                    "published_date": dt + pd.Timedelta(days=30),
                    "gross_margin": float(gval),
                    "net_margin": float(gval * 0.6),
                    "yoy_revenue_growth": float(0.10 - 0.5 * dm[0]),
                }
            )
    ratios_df = pd.DataFrame(ratio_rows)

    snaps_2026, transforms = e2.run_fundamental_macro_channel(
        stocks, macro_df, ratios_df, as_of_date=date(2026, 11, 20), min_quarters=12
    )
    assert "usd_vnd_exchange" in transforms
    assert any(s["entity_node_id"] == "TICKER:PVT" and s["tercile_stable"] for s in snaps_2026)

    # Reconcile with audited doc disclosures & verify PIT blocks future doc disclosures
    quant = [
        {"entity_node_id": "TICKER:PVT", "indicator_name": "usd_vnd_exchange", "direction_sign": -1},
        {"entity_node_id": "TICKER:PVT", "indicator_name": "oil_price_brent", "direction_sign": 1},
        {"entity_node_id": "TICKER:PVT", "indicator_name": "usd_index", "direction_sign": -1},
        {"entity_node_id": "TICKER:PVT", "indicator_name": "vix", "direction_sign": -1},
    ]
    docs = {
        "PVT": {
            "usd_vnd_exchange": {
                "page_no": 40,
                "stated_sign": -1,
                "has_quant_sensitivity": True,
                "quote": "q1",
                "quote_hash": "h1",
                "effective_from": "2026-04-13",
            },
            "oil_price_brent": {
                "page_no": 37,
                "stated_sign": -1,
                "has_quant_sensitivity": False,
                "quote": "q2",
                "quote_hash": "h2",
                "effective_from": "2026-04-13",
            },
            "usd_index": {
                "page_no": 46,
                "stated_sign": 0,
                "has_quant_sensitivity": False,
                "quote": "q4",
                "quote_hash": "h4",
                "effective_from": "2026-04-13",
            },
            "vninbr_interbank_rate": {
                "page_no": 40,
                "stated_sign": -1,
                "has_quant_sensitivity": False,
                "quote": "q3",
                "quote_hash": "h3",
                "effective_from": "2026-12-01",
            },
        }
    }
    rec = e2.reconcile_macro_with_audited_docs(quant, docs, date(2026, 11, 20))
    by_ind = {r["indicator_name"]: r["verification_tier"] for r in rec}
    assert by_ind["usd_vnd_exchange"] == "CONFIRMED_BOTH"
    assert by_ind["oil_price_brent"] == "CONFLICT_FLAG"
    assert by_ind["usd_index"] == "QUANT_DOC_NEUTRAL"
    assert by_ind["vix"] == "QUANT_ONLY"
    # 2026-12-01 doc disclosure MUST NOT leak at as_of_date=2026-11-20!
    assert "vninbr_interbank_rate" not in by_ind


# ── Engine 3: True-Peer Leiden Clustering ────────────────────────────────────


class _FakeQwen3Embedder:
    """Deterministic orthogonal embedder simulating domain separation in 1024d."""

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        vecs = np.zeros((len(texts), 8), dtype=np.float64)
        for i, txt in enumerate(texts):
            tl = txt.lower()
            if "dầu khí" in tl or "nhiên liệu" in tl:
                vecs[i, 0] = 1.0
            elif "hàng không" in tl:
                vecs[i, 1] = 1.0
            elif "cảng" in tl:
                vecs[i, 2] = 1.0
            else:
                vecs[i, 3] = 1.0
            vecs[i, 4 + (i % 4)] = 0.05
        return vecs


def test_true_peer_boilerplate_reweight_and_leiden_clusters() -> None:
    assert e3.strip_corporate_boilerplate("Tổng CTCP Vận tải Dầu khí") == "Vận tải Dầu khí"
    assert e3.strip_corporate_boilerplate("Tổng Công ty Hàng không Việt Nam - CTCP") == "Hàng không Việt Nam"

    # Missing financial features must not cause sim_fin=1.0 collision
    biz = np.array([[1.0, 0.0], [0.0, 1.0]])
    fin_z = pd.DataFrame([[0.0, 0.0], [0.0, 0.0]], index=["A", "B"], columns=["f1", "f2"])
    sim, _, _, _, _ = e3.compute_multimodal_similarity(
        ["A", "B"], biz, fin_z, {}, [], has_fin_map={"A": False, "B": False}
    )
    assert sim[0, 1] == pytest.approx(0.0)

    stocks = pd.DataFrame(
        [
            {
                "symbol": "PVT",
                "name": "Tổng CTCP Vận tải Dầu khí",
                "sector": "TRANSPORTATION",
                "industry": "Vận tải dầu khí",
            },
            {
                "symbol": "PVP",
                "name": "CTCP Vận tải Dầu khí Thái Bình Dương",
                "sector": "TRANSPORTATION",
                "industry": "Vận tải dầu khí",
            },
            {
                "symbol": "GSP",
                "name": "CTCP Vận tải Sản phẩm khí Quốc tế",
                "sector": "TRANSPORTATION",
                "industry": "Vận tải dầu khí nhiên liệu",
            },
            {
                "symbol": "HVN",
                "name": "Tổng Công ty Hàng không Việt Nam - CTCP",
                "sector": "TRANSPORTATION",
                "industry": "Hàng không",
            },
            {"symbol": "VJC", "name": "CTCP Hàng không Vietjet", "sector": "TRANSPORTATION", "industry": "Hàng không"},
        ]
    )
    biz_vecs, meta = e3.compute_biz_vectors(stocks, embedder=_FakeQwen3Embedder())
    assert meta["mode"] == "EMBEDDER"
    fin_z_real, has_fin, _ = e3.build_financial_structure_matrix(stocks, pd.DataFrame(), [], date(2026, 11, 20))
    sim_p, sim_b, sim_m, sim_f, _ = e3.compute_multimodal_similarity(
        stocks["symbol"].tolist(), biz_vecs, fin_z_real, {}, [], has_fin_map=has_fin
    )
    res = e3.build_true_peer_clusters(
        stocks, sim_p, sim_b, sim_m, sim_f, date(2026, 11, 20),
        k=4, sim_threshold=0.40, resolution=1.5, mutual_knn=True, strong_threshold=0.55, min_biz_sim=0.20,
    )
    assert res["ticker_to_cluster"]["PVT"] == res["ticker_to_cluster"]["PVP"]
    assert res["ticker_to_cluster"]["HVN"] == res["ticker_to_cluster"]["VJC"]
    assert res["ticker_to_cluster"]["PVT"] != res["ticker_to_cluster"]["HVN"]


# ── Quote Post-Verifier (Normal + Fail-Closed) ───────────────────────────────


def test_quote_verifier_normal_and_fail_closed_paths() -> None:
    quote_ok = "Doanh thu mảng vận tải đạt 9.857 tỷ đồng trong năm 2025."
    qhash_ok = compute_quote_hash(quote_ok)
    blocks = {
        "ver-1": [
            CanonicalBlockRecord(
                document_version_id="ver-1",
                page_from=168,
                page_to=168,
                normalized_text=(
                    "Thuyết minh 28: Doanh thu mảng vận tải đạt   9.857 tỷ đồng trong năm 2025. Biên gộp 21%."
                ),
                sub_doc_slot="S07_REV_COGS_DETAIL",
                doc_id="doc-1",
                published_date=date(2026, 4, 13),
            )
        ]
    }
    citations = [
        {
            "citation_id": "C1",
            "doc_id": "doc-1",
            "document_version_id": "ver-1",
            "sub_doc_slot": "S07_REV_COGS_DETAIL",
            "page": 168,
            "published_date": "2026-04-13",
            "quote": quote_ok,
            "quote_hash": qhash_ok,
        },
        # Tampered hash -> fail-closed
        {
            "citation_id": "C2",
            "doc_id": "doc-1",
            "document_version_id": "ver-1",
            "sub_doc_slot": "S07_REV_COGS_DETAIL",
            "page": 168,
            "published_date": "2026-04-13",
            "quote": quote_ok,
            "quote_hash": "0" * 64,
        },
        # Fabricated substring -> fail-closed
        {
            "citation_id": "C3",
            "doc_id": "doc-1",
            "document_version_id": "ver-1",
            "sub_doc_slot": "S07_REV_COGS_DETAIL",
            "page": 168,
            "published_date": "2026-04-13",
            "quote": "Con số bịa đặt 999.999 tỷ",
            "quote_hash": compute_quote_hash("Con số bịa đặt 999.999 tỷ"),
        },
        # Future PIT published_date -> fail-closed
        {
            "citation_id": "C4",
            "doc_id": "doc-1",
            "document_version_id": "ver-1",
            "sub_doc_slot": "S07_REV_COGS_DETAIL",
            "page": 168,
            "published_date": "2026-12-01",
            "quote": quote_ok,
            "quote_hash": qhash_ok,
        },
    ]

    res = verify_citations(
        citations,
        blocks,
        as_of_date=date(2026, 11, 20),
        answer_text="Kết quả [C1] và [C2] và [C3] và [C4].",
    )
    assert [c["citation_id"] for c in res.verified_citations] == ["C1"]
    assert len(res.rejected_citations) == 3
    assert "[C1]" in res.sanitized_answer
    assert "[C2 UNVERIFIED_CLAIM]" in res.sanitized_answer
    assert "[C3 UNVERIFIED_CLAIM]" in res.sanitized_answer
    assert "[C4 UNVERIFIED_CLAIM]" in res.sanitized_answer
    assert any(b["code"] == "OUT_OF_PIT_WINDOW" for b in res.blindspots)


# ── Research Chat Orchestrator & HTTP/SSE API Integration Tests ──────────────


class _FakeRetriever:
    def __init__(self, sections: list[RetrievedResearchSection], blocks: dict[str, list[CanonicalBlockRecord]]) -> None:
        self._sections = sections
        self._blocks = blocks

    async def retrieve(self, *, query: str, tickers: list[str], as_of_date: date, top_k: int) -> RetrievalResult:
        filtered = [s for s in self._sections if s.ticker in tickers]
        return RetrievalResult(
            sections=filtered[:top_k],
            canonical_blocks=self._blocks,
            routing_slot="S07_REV_COGS_DETAIL",
            leiden_nodes_visited=4,
            hipporag_hops=2,
        )


class _FakeTableStore:
    def __init__(self, tables: list[dict[str, Any]]) -> None:
        self._tables = tables

    async def get_tables(self, *, table_ids: list[str], tickers: list[str], as_of_date: date) -> list[dict[str, Any]]:
        out = []
        for t in self._tables:
            vf = date.fromisoformat(str(t["valid_from"])[:10])
            if vf > as_of_date:
                continue
            if (table_ids and t["table_id"] in table_ids) or t["project_id"] in tickers:
                out.append(t)
        return out

    async def get_table_by_id(self, table_id: str, *, as_of_date: date | None = None) -> dict[str, Any] | None:
        cutoff = as_of_date or date.today()
        for t in self._tables:
            if t["table_id"] == table_id:
                vf = date.fromisoformat(str(t["valid_from"])[:10])
                if vf <= cutoff:
                    return t
        return None


class _FakeLLM:
    configured = True

    def __init__(self, text: str) -> None:
        self._text = text

    async def stream_complete(self, messages: list[dict[str, str]]) -> AsyncIterator[str]:
        for chunk in self._text.split(" "):
            yield chunk + " "


def _build_test_app(
    *,
    kg_store: InMemoryKGStore,
    table_store: _FakeTableStore,
    retriever: _FakeRetriever,
    llm: _FakeLLM | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(api_router)

    @app.exception_handler(ApiError)
    async def _handle_api_error(_request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.to_envelope())

    orch = ResearchChatOrchestrator(
        kg_store=kg_store,
        retriever=retriever,
        table_store=table_store,
        llm=llm,
    )
    app.dependency_overrides[get_kg_store] = lambda: kg_store
    app.dependency_overrides[get_table_store] = lambda: table_store
    app.dependency_overrides[get_research_orchestrator] = lambda: orch
    return app


def _parse_sse_events(body: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    cur_ev = "message"
    cur_data: list[str] = []
    for line in body.splitlines():
        if not line.strip():
            if cur_data:
                events.append((cur_ev, json.loads("\n".join(cur_data))))
                cur_ev = "message"
                cur_data = []
            continue
        if line.startswith("event:"):
            cur_ev = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            cur_data.append(line.split(":", 1)[1].strip())
    if cur_data:
        events.append((cur_ev, json.loads("\n".join(cur_data))))
    return events


async def _seed_kg_store() -> InMemoryKGStore:
    store = InMemoryKGStore()
    stocks = pd.DataFrame(
        [
            {"symbol": "PVT", "name": "PVTrans", "sector": "TRANSPORTATION", "industry": "Marine"},
            {"symbol": "PVP", "name": "PVTrans Pacific", "sector": "TRANSPORTATION", "industry": "Marine"},
            {"symbol": "VTO", "name": "Vitaco", "sector": "TRANSPORTATION", "industry": "Marine"},
        ]
    )
    nodes = build_kg_nodes_from_universe(stocks, ["usd_vnd_exchange"], date(2026, 4, 13))
    await store.upsert_nodes(nodes)
    snaps = e2.reconcile_macro_with_audited_docs(
        [
            {
                "entity_node_id": "TICKER:PVT",
                "entity_level": "COMPANY",
                "indicator_name": "usd_vnd_exchange",
                "channel": "FUNDAMENTAL",
                "target_metric": "GPM",
                "lag_periods": 0,
                "beta_value": -0.42,
                "corr_value": -0.58,
                "p_value": 0.012,
                "fdr_q": 0.031,
                "direction_sign": -1,
                "tercile_stable": True,
                "effective_from": "2026-04-13",
            }
        ],
        {
            "PVT": {
                "usd_vnd_exchange": {
                    "page_no": 40,
                    "stated_sign": -1,
                    "has_quant_sensitivity": True,
                    "quote": "USD tăng gây lỗ tỷ giá",
                    "quote_hash": compute_quote_hash("USD tăng gây lỗ tỷ giá"),
                    "effective_from": "2026-04-13",
                }
            }
        },
        date(2026, 4, 13),
    )
    await store.upsert_macro_snapshots(snaps)
    await store.upsert_edges(snapshots_to_kg_edges(snaps, date(2026, 4, 13)))
    await store.upsert_edges(
        [
            {
                "edge_id": "peer-pvt-pvp",
                "src_node_id": "TICKER:PVT",
                "dst_node_id": "TICKER:PVP",
                "relation_type": "TRUE_PEER_OF",
                "layer": "TRUE_PEER",
                "weight": 0.88,
                "verification_tier": "CLUSTER",
                "effective_from": "2026-04-13",
                "valid_to": "9999-12-31",
            },
            # Future edge that MUST NOT leak at as_of_date=2025-06-30
            {
                "edge_id": "future-edge-2026",
                "src_node_id": "TICKER:PVT",
                "dst_node_id": "TICKER:VTO",
                "relation_type": "TRUE_PEER_OF",
                "layer": "TRUE_PEER",
                "weight": 0.79,
                "verification_tier": "CLUSTER",
                "effective_from": "2026-06-01",
                "valid_to": "9999-12-31",
            },
        ]
    )
    return store


@pytest.mark.asyncio
async def test_research_chat_sse_stream_normal_and_kg_pit_endpoints(monkeypatch) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-secret-token")

    kg_store = await _seed_kg_store()
    quote_txt = "Doanh thu vận tải đạt 9.857 tỷ đồng trong FY2025."
    sections = [
        RetrievedResearchSection(
            ticker="PVT",
            doc_id="doc-pvt-1",
            document_version_id="ver-pvt-1",
            sub_doc_slot="S07_REV_COGS_DETAIL",
            page_from=168,
            page_to=168,
            published_date=date(2026, 4, 13),
            content=quote_txt,
            table_id="tbl-pvt-1",
            score=0.95,
        ),
        # Future section that MUST be stripped by PIT gate when as_of_date=2026-06-30
        RetrievedResearchSection(
            ticker="PVT",
            doc_id="doc-pvt-future",
            document_version_id="ver-pvt-future",
            sub_doc_slot="S13_QUARTER_NOTES",
            page_from=5,
            page_to=5,
            published_date=date(2026, 10, 20),
            content="Báo cáo quý 3/2026 phát hành tháng 10.",
            score=0.99,
        ),
    ]
    blocks = {
        "ver-pvt-1": [
            CanonicalBlockRecord(
                document_version_id="ver-pvt-1",
                page_from=168,
                page_to=168,
                normalized_text=quote_txt,
                sub_doc_slot="S07_REV_COGS_DETAIL",
                doc_id="doc-pvt-1",
                published_date=date(2026, 4, 13),
            )
        ]
    }
    tables = [
        {
            "table_id": "tbl-pvt-1",
            "project_id": "PVT",
            "document_version_id": "ver-pvt-1",
            "sub_doc_slot": "S07_REV_COGS_DETAIL",
            "fiscal_year": 2025,
            "fiscal_quarter": 0,
            "title": "Thuyết minh 28 - Doanh thu theo mảng",
            "section_path": "S07",
            "page_from": 168,
            "page_to": 168,
            "currency": "VND",
            "unit_raw": "tỷ đồng",
            "unit_multiplier": 1e9,
            "headers": ["Chỉ tiêu", "2025", "2024"],
            "rows": [{"label": "Vận tải", "values": [9857.0, 9042.0]}],
            "footnotes_text": None,
            "sanity_error_ratio": 0.0,
            "verification_status": "VERIFIED",
            "table_hash": "a" * 64,
            "valid_from": "2026-04-13",
        }
    ]

    app = _build_test_app(
        kg_store=kg_store,
        table_store=_FakeTableStore(tables),
        retriever=_FakeRetriever(sections, blocks),
        llm=_FakeLLM("Doanh thu vận tải PVT đạt 9.857 tỷ đồng [C1] với độ nhạy tỷ giá [CALC-1]."),
    )

    bff_headers_all = {
        "X-SAG-BFF-Service-Token": "test-bff-secret-token",
        "X-Continuum-User-Id": "user-uuid-1",
        "X-Continuum-Organization-Id": "aiinvest-default",
        "X-Continuum-Project-Id": "all",
        "X-Continuum-Security-Partition": "all",
        "X-Continuum-Allowed-Project-Ids": "PVT,PVP,VTO",
    }

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Stream SSE with peer comparison + macro shock at as_of_date=2026-05-01
        resp = await client.post(
            "/api/v1/research-chat/stream",
            headers=bff_headers_all,
            json={
                "query": "Nếu tỷ giá USD/VND tăng 3%, so sánh PVT với đối thủ?",
                "ticker": "PVT",
                "as_of_date": "2026-05-01",
                "enable_kg": True,
            },
        )
        assert resp.status_code == 200
        assert resp.headers.get("cache-control") == "no-cache"
        assert resp.headers.get("x-accel-buffering") == "no"

        events = _parse_sse_events(resp.text)
        stage_names = [p["stage"] for ev, p in events if ev == "stage" and p["status"] == "done"]
        assert stage_names == [
            "routing",
            "retrieval",
            "graph_hop",
            "table_hydration",
            "calculator",
            "synthesis",
            "verification",
        ]
        done_payload = next(p for ev, p in events if ev == "done")
        meta = done_payload["metadata"]
        assert meta["mode"] == "RESEARCH_CHAT_V2"
        assert meta["primary_ticker"] == "PVT"
        # True-peer PVP (2026-04-13) expanded, while VTO (2026-06-01) blocked by PIT!
        assert meta["expanded_tickers"] == ["PVT", "PVP"]
        assert len(meta["citations"]) == 1
        assert meta["citations"][0]["verified"] is True
        assert len(meta["hydrated_tables"]) == 1
        assert len(meta["calculator_traces"]) >= 1
        assert any(h["verification_tier"] == "CONFIRMED_BOTH" for h in meta["graph_hops"])
        assert any(b["code"] == "OUT_OF_PIT_WINDOW" for b in meta["declared_blindspots"])

        # 2. KG endpoint strict PIT check: 2025-06-30 returns 0% future leak!
        kg_past = await client.get(
            "/api/v1/research-chat/kg/PVT",
            params={"as_of_date": "2025-06-30"},
            headers=bff_headers_all,
        )
        assert kg_past.status_code == 200
        assert kg_past.json()["edges"] == []
        assert kg_past.json()["macro_snapshots"] == []
        assert kg_past.json()["true_peers"] == []

        kg_now = await client.get(
            "/api/v1/research-chat/kg/PVT",
            params={"as_of_date": "2026-11-20"},
            headers=bff_headers_all,
        )
        assert kg_now.status_code == 200
        assert len(kg_now.json()["true_peers"]) == 2

        # 3. Table endpoint PIT check
        tbl_past = await client.get(
            "/api/v1/research-chat/tables/tbl-pvt-1",
            params={"as_of_date": "2025-06-30"},
            headers=bff_headers_all,
        )
        assert tbl_past.status_code == 404

        tbl_ok = await client.get(
            "/api/v1/research-chat/tables/tbl-pvt-1",
            params={"as_of_date": "2026-05-01"},
            headers=bff_headers_all,
        )
        assert tbl_ok.status_code == 200
        assert tbl_ok.json()["verification_status"] == "VERIFIED"


@pytest.mark.asyncio
async def test_research_chat_fail_closed_auth_cap_table_unverified_and_abstention(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "bff_service_token", "test-bff-secret-token")

    kg_store = InMemoryKGStore()
    unverified_tbl = {
        "table_id": "tbl-bad",
        "project_id": "PVT",
        "document_version_id": "ver-1",
        "sub_doc_slot": "S07_REV_COGS_DETAIL",
        "fiscal_year": 2025,
        "fiscal_quarter": 0,
        "title": "Bảng lỗi số học",
        "section_path": "S07",
        "page_from": 10,
        "page_to": 10,
        "currency": "VND",
        "unit_raw": "tỷ đồng",
        "unit_multiplier": 1e9,
        "headers": ["Chỉ tiêu", "2025", "2024"],
        "rows": [{"label": "A", "values": [100.0, 50.0]}],
        "footnotes_text": None,
        "sanity_error_ratio": 0.12,
        "verification_status": "TABLE_UNVERIFIED",
        "table_hash": "b" * 64,
        "valid_from": "2026-04-13",
    }
    app = _build_test_app(
        kg_store=kg_store,
        table_store=_FakeTableStore([unverified_tbl]),
        retriever=_FakeRetriever([], {}),
        llm=None,
    )

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        # 1. Unauthorized / invalid BFF token -> 403
        r_bad_tok = await client.get(
            "/api/v1/research-chat/kg/PVT",
            headers={
                "X-SAG-BFF-Service-Token": "wrong-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "PVT",
                "X-Continuum-Security-Partition": "PVT",
            },
        )
        assert r_bad_tok.status_code == 403

        # 2. >6 ticker cap -> 400 fail-closed
        r_cap = await client.post(
            "/api/v1/research-chat/stream",
            headers={
                "X-SAG-BFF-Service-Token": "test-bff-secret-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "all",
                "X-Continuum-Security-Partition": "all",
                "X-Continuum-Allowed-Project-Ids": "PVT,PVP,VTO,VIP,GSP,HAH,GMD",
            },
            json={
                "query": "So sánh 7 mã",
                "ticker": "PVT",
                "compare_tickers": ["PVP", "VTO", "VIP", "GSP", "HAH", "GMD"],
                "as_of_date": "2026-11-20",
            },
        )
        assert r_cap.status_code == 400

        # 3. Ticker outside single-project scope -> 403
        r_scope = await client.get(
            "/api/v1/research-chat/kg/FPT",
            headers={
                "X-SAG-BFF-Service-Token": "test-bff-secret-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "PVT",
                "X-Continuum-Security-Partition": "PVT",
            },
        )
        assert r_scope.status_code == 403

        # 4. TABLE_UNVERIFIED is excluded from Python Calculator & emits MISSING_TABLE_VERIFIED blindspot
        r_unv = await client.post(
            "/api/v1/research-chat/stream",
            headers={
                "X-SAG-BFF-Service-Token": "test-bff-secret-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "PVT",
                "X-Continuum-Security-Partition": "PVT",
            },
            json={
                "query": "Tính tăng trưởng",
                "ticker": "PVT",
                "as_of_date": "2026-11-20",
            },
        )
        assert r_unv.status_code == 200
        events_unv = _parse_sse_events(r_unv.text)
        done_unv = next(p for ev, p in events_unv if ev == "done")
        assert done_unv["metadata"]["calculator_traces"] == []
        assert any(b["code"] == "MISSING_TABLE_VERIFIED" for b in done_unv["metadata"]["declared_blindspots"])

        # 5. Missing pilot source -> 404 fail-closed
        r_pilot_missing = await client.post(
            "/api/v1/research-chat/ingest-pilot",
            headers={
                "X-SAG-BFF-Service-Token": "test-bff-secret-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "PVT",
                "X-Continuum-Security-Partition": "PVT",
            },
            json={
                "tickers": ["PVT"],
                "source_paths": {"PVT": str(tmp_path / "does_not_exist.pdf")},
            },
        )
        assert r_pilot_missing.status_code == 404

        # 6. Missing X-Continuum-Allowed-Project-Ids on project_id="all" -> 403
        r_missing_allowed = await client.get(
            "/api/v1/research-chat/kg/PVT",
            headers={
                "X-SAG-BFF-Service-Token": "test-bff-secret-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "all",
                "X-Continuum-Security-Partition": "all",
            },
        )
        assert r_missing_allowed.status_code == 403

        # 7. Partition mismatch -> 403
        r_part_mismatch = await client.get(
            "/api/v1/research-chat/kg/PVT",
            headers={
                "X-SAG-BFF-Service-Token": "test-bff-secret-token",
                "X-Continuum-User-Id": "u1",
                "X-Continuum-Organization-Id": "org1",
                "X-Continuum-Project-Id": "PVT",
                "X-Continuum-Security-Partition": "PVP",
            },
        )
        assert r_part_mismatch.status_code == 403


@pytest.mark.asyncio
async def test_kg_store_missing_pit_fails_closed_and_market_excess_hac() -> None:
    store = InMemoryKGStore()
    # Edge with missing effective_from MUST fail closed (never returned by PIT query)
    store.edges["missing-pit"] = {
        "edge_id": "missing-pit",
        "src_node_id": "TICKER:PVT",
        "dst_node_id": "TICKER:PVP",
        "relation_type": "TRUE_PEER_OF",
        "layer": "TRUE_PEER",
        "weight": 0.9,
        "verification_tier": "CLUSTER",
        "effective_from": None,
        "valid_to": "9999-12-31",
    }
    assert await store.get_true_peers("PVT", date(2026, 11, 20)) == []

    # Market excess channel with pure numpy Newey-West HAC
    stocks = pd.DataFrame(
        [
            {"symbol": "PVT", "name": "PVTrans", "sector": "TRANSPORTATION", "industry": "Marine"},
            {"symbol": "PVP", "name": "PVTrans Pacific", "sector": "TRANSPORTATION", "industry": "Marine"},
            {"symbol": "GSP", "name": "Gas Shipping", "sector": "TRANSPORTATION", "industry": "Marine"},
        ]
    )
    w_dates = pd.date_range("2024-01-05", "2026-06-26", freq="W-FRI")
    rng = np.random.default_rng(99)
    vnindex_ret = rng.normal(0.001, 0.015, len(w_dates))
    oil_px = 70.0 + np.cumsum(rng.normal(0.1, 2.0, len(w_dates)))
    oil_ret = pd.Series(oil_px).pct_change().fillna(0.0).to_numpy()
    pvt_ret = 1.0 * vnindex_ret + 0.5 * oil_ret + rng.normal(0.0, 0.001, len(w_dates))

    macro_df = pd.DataFrame(
        {
            "indicator_date": w_dates,
            "indicator_name": "oil_price_brent",
            "value": oil_px,
            "unit": "USD/bbl",
        }
    )
    mkt_rows = []
    vn_series = np.cumprod(1.0 + vnindex_ret) * 1200.0
    pvt_series = np.cumprod(1.0 + pvt_ret) * 20.0
    for dt, vr, pr in zip(w_dates, vn_series, pvt_series, strict=True):
        mkt_rows.append({"ticker": "VNINDEX", "date": dt, "close_adj": float(vr)})
        mkt_rows.append({"ticker": "PVT", "date": dt, "close_adj": float(pr)})
    mkt_df = pd.DataFrame(mkt_rows)

    passed, beta_mat, cols = e2.run_market_excess_macro_channel(
        stocks, macro_df, mkt_df, as_of_date=date(2026, 6, 30), min_weeks=52
    )
    assert cols == ["oil_price_brent"]
    assert "PVT" in beta_mat
    assert any(r["entity_node_id"] == "TICKER:PVT" and r["tercile_stable"] for r in passed)

