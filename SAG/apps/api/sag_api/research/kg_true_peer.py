"""Engine 3: Zero-Hardcode True-Peer & Thematic Leiden Clustering Engine.

Productionized from verified Stage 1 `block3_true_peer_clustering.py`.
- Pure `numpy` + `pandas` + `math` + `igraph` + `leidenalg` (zero `scikit-learn`).
- Boilerplate-stripped DeepInfra `Qwen/Qwen3-Embedding-0.6B` (1024d) with injectable
  embedder protocol for unit tests and corpus mean-centering to remove anisotropy.
- Dynamic multimodal similarity reweighting (`0.45 Biz + 0.30 MacroBeta + 0.25 FinStruct`).
- Mutual k-NN (`k=6`, `sim_threshold=0.45`, `strong_threshold=0.60`, `min_biz_sim=0.25`,
  `weight_power=2.5`) + `leidenalg.RBConfigurationVertexPartition` (`resolution=2.5`).
- Strict Point-in-Time filtering (`<= as_of_date`) across ratios and financial statements.
"""

from __future__ import annotations

import math
import re
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any, Protocol

import httpx
import igraph as ig
import leidenalg
import numpy as np
import pandas as pd

from sag_api.core.config import settings

_MAX_VALID_TO = date(9999, 12, 31)

_CORP_BOILERPLATE_PREFIX_RE = re.compile(
    r"^(?:tổng\s+công\s+ty\s+cổ\s+phần|tổng\s+ctcp|tổng\s+công\s+ty|công\s+ty\s+cổ\s+phần|"
    r"công\s+ty\s+tnhh\s+mtv|công\s+ty\s+tnhh|ctcp\s*-\s*tổng\s+công\s+ty|ctcp)\s+",
    re.IGNORECASE,
)
_CORP_BOILERPLATE_SUFFIX_RE = re.compile(
    r"\s*(?:-\s*ctcp|\(\s*ctcp\s*\)|-\s*công\s+ty\s+cổ\s+phần)$",
    re.IGNORECASE,
)


class TextEmbedderProtocol(Protocol):
    """Injectable text embedding interface for DeepInfra Qwen3 or test doubles."""

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        ...


class DeepInfraQwen3Embedder:
    """DeepInfra `Qwen/Qwen3-Embedding-0.6B` client using configured SAG settings."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout: float = 60.0,
        batch_size: int = 64,
    ) -> None:
        self._api_key = api_key or settings.embedding_api_key or ""
        self._base_url = (base_url or settings.embedding_base_url or "https://api.deepinfra.com/v1/openai").rstrip("/")
        self._model = model or settings.embedding_model or "Qwen/Qwen3-Embedding-0.6B"
        self._timeout = timeout
        self._batch_size = batch_size

    def embed_texts(self, texts: Sequence[str]) -> np.ndarray:
        if not texts:
            return np.zeros((0, 1024), dtype=np.float64)
        if not self._api_key:
            raise ValueError("SAG_EMBEDDING_API_KEY is required for DeepInfra embedding")

        all_vecs: list[list[float]] = []
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self._timeout) as client:
            for start in range(0, len(texts), self._batch_size):
                batch = list(texts[start : start + self._batch_size])
                resp = client.post(
                    f"{self._base_url}/embeddings",
                    headers=headers,
                    json={"model": self._model, "input": batch, "encoding_format": "float"},
                )
                resp.raise_for_status()
                payload = resp.json()
                data_sorted = sorted(payload["data"], key=lambda x: x["index"])
                all_vecs.extend([item["embedding"] for item in data_sorted])

        arr = np.asarray(all_vecs, dtype=np.float64)
        arr /= np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-12)
        return arr


def strip_corporate_boilerplate(name: str) -> str:
    """Strip generic Vietnamese legal-entity prefixes/suffixes so embeddings focus on domain."""
    s = re.sub(r"\s+", " ", (name or "").strip())
    s = _CORP_BOILERPLATE_PREFIX_RE.sub("", s).strip(" -–—")
    s = _CORP_BOILERPLATE_SUFFIX_RE.sub("", s).strip(" -–—")
    return s if len(s) >= 2 else (name or "").strip()


def center_and_normalize_embeddings(raw: np.ndarray) -> np.ndarray:
    """Apply corpus mean-centering and L2-normalization to remove anisotropy."""
    if raw.size == 0:
        return raw.astype(np.float64)
    arr = np.asarray(raw, dtype=np.float64)
    if arr.shape[0] > 1:
        arr = arr - arr.mean(axis=0, keepdims=True)
    norms = np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-12)
    return arr / norms


def compute_biz_vectors_local_tfidf(stocks_df: pd.DataFrame) -> np.ndarray:
    """Pure-NumPy local Vietnamese word + char_wb TF-IDF business representation (0 external deps)."""
    if stocks_df.empty:
        return np.zeros((0, 1), dtype=np.float64)

    texts = [
        (
            f"{strip_corporate_boilerplate(str(getattr(r, 'name', '') or ''))} | "
            f"{getattr(r, 'industry', '') or ''} | {getattr(r, 'sector', '') or ''}"
        ).lower()
        for r in stocks_df.itertuples(index=False)
    ]

    def _word_ngrams(text: str) -> list[str]:
        words = re.findall(r"[0-9a-zà-ỹ]+", text)
        grams = list(words)
        grams.extend(f"{words[i]}_{words[i + 1]}" for i in range(len(words) - 1))
        return grams

    def _char_ngrams(text: str) -> list[str]:
        grams: list[str] = []
        for word in re.findall(r"[0-9a-zà-ỹ]+", text):
            padded = f" {word} "
            for n in (3, 4, 5):
                if len(padded) >= n:
                    grams.extend(padded[i : i + n] for i in range(len(padded) - n + 1))
        return grams

    def _tfidf_matrix(docs_tokens: list[list[str]], *, min_df: int, max_features: int | None = None) -> np.ndarray:
        n_docs = len(docs_tokens)
        df_counts: Counter[str] = Counter()
        for toks in docs_tokens:
            df_counts.update(set(toks))
        vocab_items = [(tok, cnt) for tok, cnt in df_counts.items() if cnt >= min_df]
        if max_features is not None and len(vocab_items) > max_features:
            vocab_items.sort(key=lambda x: (-x[1], x[0]))
            vocab_items = vocab_items[:max_features]
        vocab = {tok: idx for idx, (tok, _) in enumerate(sorted(vocab_items))}
        if not vocab:
            return np.zeros((n_docs, 1), dtype=np.float64)

        mat = np.zeros((n_docs, len(vocab)), dtype=np.float64)
        for row_idx, toks in enumerate(docs_tokens):
            tf = Counter(t for t in toks if t in vocab)
            for tok, cnt in tf.items():
                col_idx = vocab[tok]
                sublinear_tf = 1.0 + math.log(cnt)
                idf = math.log((1.0 + n_docs) / (1.0 + df_counts[tok])) + 1.0
                mat[row_idx, col_idx] = sublinear_tf * idf
        norms = np.maximum(np.linalg.norm(mat, axis=1, keepdims=True), 1e-12)
        return mat / norms

    Xw = _tfidf_matrix([_word_ngrams(t) for t in texts], min_df=1)
    Xc = _tfidf_matrix([_char_ngrams(t) for t in texts], min_df=1 if len(texts) < 4 else 2, max_features=4000)
    X = np.hstack([Xw * 0.75, Xc * 0.25])
    X /= np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)
    return X


def compute_biz_vectors(
    stocks_df: pd.DataFrame,
    *,
    embedder: TextEmbedderProtocol | None = None,
    use_local_fallback: bool = True,
) -> tuple[np.ndarray, dict[str, Any]]:
    """Compute boilerplate-stripped business embeddings via DeepInfra Qwen3 or injected embedder."""
    if stocks_df.empty:
        return np.zeros((0, 1024), dtype=np.float64), {"mode": "empty", "count": 0}

    texts = [
        f"{strip_corporate_boilerplate(str(getattr(r, 'name', '') or ''))} — {getattr(r, 'industry', '') or ''}"
        for r in stocks_df.itertuples(index=False)
    ]

    if embedder is not None:
        raw = embedder.embed_texts(texts)
        return center_and_normalize_embeddings(raw), {
            "mode": "EMBEDDER",
            "dims": int(raw.shape[1]) if raw.ndim == 2 else 0,
            "count": len(texts),
            "mean_centered": True,
        }

    if settings.embedding_api_key:
        client_embedder = DeepInfraQwen3Embedder()
        raw = client_embedder.embed_texts(texts)
        return center_and_normalize_embeddings(raw), {
            "mode": "DEEPINFRA_QWEN3",
            "model": settings.embedding_model,
            "dims": int(raw.shape[1]) if raw.ndim == 2 else 0,
            "count": len(texts),
            "mean_centered": True,
        }

    if not use_local_fallback:
        raise ValueError("No embedder configured and local fallback disabled")

    arr = compute_biz_vectors_local_tfidf(stocks_df)
    return arr, {
        "mode": "LOCAL_TFIDF_0D",
        "dims": int(arr.shape[1]),
        "count": len(texts),
        "mean_centered": False,
    }


def _pick_num(d: Mapping[str, Any] | None, *substrs: str) -> float | None:
    if not isinstance(d, Mapping):
        return None
    for s in substrs:
        if s in d and d[s] is not None:
            try:
                v = float(d[s])
                if math.isfinite(v):
                    return v
            except (TypeError, ValueError):
                pass
    for k, v in d.items():
        if v is None:
            continue
        kl = str(k).lower()
        for s in substrs:
            if s.lower() in kl:
                try:
                    fv = float(v)
                    if math.isfinite(fv):
                        return fv
                except (TypeError, ValueError):
                    pass
    return None


def build_financial_structure_matrix(
    stocks_df: pd.DataFrame,
    ratios_df: pd.DataFrame,
    stmt_rows: Sequence[Mapping[str, Any]] | None,
    as_of_date: date,
) -> tuple[pd.DataFrame, dict[str, bool], dict[str, Any]]:
    """Build winsorized & cross-sectionally Z-scored structural financial matrix strictly <= as_of_date."""
    symbols = [str(s) for s in stocks_df["symbol"].tolist()]
    if not symbols:
        return pd.DataFrame(), {}, {"as_of_date": str(as_of_date), "stocks_count": 0}

    # PIT filter ratios
    r_pit = ratios_df.copy() if ratios_df is not None else pd.DataFrame()
    max_ratio_date: str | None = None
    if not r_pit.empty:
        r_pit["ratio_date"] = pd.to_datetime(r_pit["ratio_date"])
        cutoff = pd.Timestamp(as_of_date)
        r_pit = r_pit[r_pit["ratio_date"] <= cutoff]
        if "published_date" in r_pit.columns:
            pub = pd.to_datetime(r_pit["published_date"], errors="coerce")
            valid_pub = (
                pub.notna()
                & (pub >= r_pit["ratio_date"])
                & (pub <= r_pit["ratio_date"] + pd.Timedelta(days=180))
            )
            eff_pub = pub.where(valid_pub, r_pit["ratio_date"] + pd.Timedelta(days=90))
            r_pit = r_pit[eff_pub <= cutoff]
        if not r_pit.empty:
            max_ratio_date = str(r_pit["ratio_date"].max().date())

    # PIT filter financial statements
    by_sym_period: dict[tuple[str, str], dict[str, Mapping[str, Any]]] = defaultdict(dict)
    max_stmt_date: date | None = None
    for row in stmt_rows or ():
        pe_raw = row.get("period_end")
        if pe_raw is None:
            continue
        pe = date.fromisoformat(str(pe_raw)[:10]) if not isinstance(pe_raw, date) else pe_raw
        if pe > as_of_date:
            continue
        pub_raw = row.get("published_date")
        if pub_raw is not None:
            pub_d = date.fromisoformat(str(pub_raw)[:10]) if not isinstance(pub_raw, date) else pub_raw
            if not (pe <= pub_d <= date.fromordinal(pe.toordinal() + 180)):
                pub_d = date.fromordinal(pe.toordinal() + 90)
            if pub_d > as_of_date:
                continue
        sym = str(row.get("symbol", "")).strip().upper()
        st = str(row.get("statement_type", "")).strip().upper()
        data = row.get("data")
        if sym and st in {"BS", "IS", "CF"} and isinstance(data, Mapping):
            by_sym_period[(sym, str(pe))][st] = data
            if max_stmt_date is None or pe > max_stmt_date:
                max_stmt_date = pe

    sym_stmt_metrics: dict[str, list[dict[str, float | None]]] = defaultdict(list)
    for (sym, _pe), st_map in by_sym_period.items():
        bs = st_map.get("BS")
        inc = st_map.get("IS")
        cf = st_map.get("CF")

        total_assets = _pick_num(
            bs, "TỔNG CỘNG TÀI SẢN", "TỔNG CỘNG TÀI SẢN (270=100+200)", "tổng_cộng_tài_sản"
        )
        equity = _pick_num(
            bs,
            "D. VỐN CHỦ SỞ HỮU",
            "B. VỐN CHỦ SỞ HỮU (400=410+420)",
            "VỐN CHỦ SỞ HỮU",
            "I. Vốn chủ sở hữu",
            "vốn_chủ_sở_hữu",
        )
        liab = _pick_num(bs, "A. NỢ PHẢI TRẢ", "C. NỢ PHẢI TRẢ", "TỔNG NỢ PHẢI TRẢ", "nợ_phải_trả")
        fixed_assets = _pick_num(
            bs,
            "II. Tài sản cố định",
            "IX. Tài sản cố định",
            "X. Tài sản cố định",
            "tài_sản_cố_định_hữu_hình",
        )
        inventory = _pick_num(bs, "IV. Hàng tồn kho", "1. Hàng tồn kho", "Hàng tồn kho", "hàng_tồn_kho")
        loans_cust = _pick_num(bs, "VI. Cho vay khách hàng", "cho_vay_khách_hàng")
        fin_assets = _pick_num(bs, "I. Tài sản tài chính ngắn hạn", "tài_sản_tài_chính")
        revenue = _pick_num(
            inc,
            "3. Doanh thu thuần",
            "Doanh thu thuần",
            "I. DOANH THU HOẠT ĐỘNG",
            "I. Thu nhập lãi thuần",
            "doanh_thu_thuần",
        )
        capex = _pick_num(
            cf,
            "1. Tiền chi để mua sắm, xây dựng TSCĐ",
            "mua sắm, xây dựng TSCĐ",
            "1. Mua sắm tài sản cố định",
            "mua_sắm_tài_sản_cố_định",
        )

        has_assets = total_assets is not None and total_assets > 0
        asset_turnover = (revenue / total_assets) if (revenue is not None and has_assets) else None
        fa_ratio = (fixed_assets / total_assets) if (fixed_assets is not None and has_assets) else None
        inv_ratio = (
            (inventory / total_assets)
            if (inventory is not None and has_assets)
            else (0.0 if (loans_cust is not None or fin_assets is not None) else None)
        )
        capex_rev = (abs(capex) / abs(revenue)) if (capex is not None and revenue and abs(revenue) > 1e6) else None
        de_bs = (liab / equity) if (liab is not None and equity and equity > 0) else None
        bank_loan_ratio = (loans_cust / total_assets) if (loans_cust is not None and has_assets) else 0.0

        sym_stmt_metrics[sym].append(
            {
                "asset_turnover": asset_turnover,
                "fa_ratio": fa_ratio,
                "inv_ratio": inv_ratio,
                "capex_rev": capex_rev,
                "de_bs": de_bs,
                "bank_loan_ratio": bank_loan_ratio,
            }
        )

    ratio_cols = [c for c in ("gross_margin", "net_margin", "debt_equity", "roe") if c in r_pit.columns]
    ratio_med = r_pit.groupby("symbol")[ratio_cols].median() if (not r_pit.empty and ratio_cols) else pd.DataFrame()

    def _med(records: list[dict[str, float | None]], key: str) -> float:
        vals = [float(x[key]) for x in records if x.get(key) is not None and math.isfinite(float(x[key]))]
        return float(np.median(vals)) if vals else np.nan

    rows = []
    for sym in symbols:
        rm = ratio_med.loc[sym] if (not ratio_med.empty and sym in ratio_med.index) else None
        slist = sym_stmt_metrics.get(sym, [])
        de_ratio = (
            float(rm["debt_equity"])
            if (rm is not None and "debt_equity" in rm and pd.notna(rm["debt_equity"]))
            else _med(slist, "de_bs")
        )
        gm_val = (
            float(rm["gross_margin"])
            if (rm is not None and "gross_margin" in rm and pd.notna(rm["gross_margin"]))
            else np.nan
        )
        nm_val = (
            float(rm["net_margin"])
            if (rm is not None and "net_margin" in rm and pd.notna(rm["net_margin"]))
            else np.nan
        )
        rows.append(
            {
                "symbol": sym,
                "gross_margin": gm_val,
                "net_margin": nm_val,
                "asset_turnover": _med(slist, "asset_turnover"),
                "debt_equity": de_ratio,
                "fa_ratio": _med(slist, "fa_ratio"),
                "inv_ratio": _med(slist, "inv_ratio"),
                "capex_rev": _med(slist, "capex_rev"),
                "bank_loan_ratio": _med(slist, "bank_loan_ratio"),
            }
        )

    fin_df = pd.DataFrame(rows).set_index("symbol")
    fin_valid_counts = fin_df.notna().sum(axis=1)
    has_fin_map = {str(sym): bool(cnt >= 3) for sym, cnt in fin_valid_counts.items()}

    fin_z = pd.DataFrame(index=fin_df.index)
    for col in fin_df.columns:
        s = fin_df[col].copy()
        valid = s.dropna()
        if len(valid) < 5:
            fin_z[col] = 0.0
            continue
        lo, hi = np.nanpercentile(valid, [1, 99])
        s = s.clip(lo, hi)
        med = float(s.median())
        s = s.fillna(med)
        std = float(s.std())
        fin_z[col] = (s - s.mean()) / (std if std > 1e-12 else 1.0)

    pit_info = {
        "as_of_date": str(as_of_date),
        "max_ratio_date": max_ratio_date,
        "max_stmt_date": str(max_stmt_date) if max_stmt_date else None,
        "stocks_count": len(symbols),
        "stocks_with_fin_ge_3": int(sum(has_fin_map.values())),
    }
    return fin_z, has_fin_map, pit_info


def compute_multimodal_similarity(
    symbols: Sequence[str],
    biz_vecs: np.ndarray,
    fin_z: pd.DataFrame,
    macro_beta_matrix: Mapping[str, Mapping[str, float]],
    macro_cols: Sequence[str],
    has_fin_map: Mapping[str, bool] | None = None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    """Compute 3-modality `Sim_peer(i, j)` with automatic reweighting when macro/fin features missing."""
    n = len(symbols)
    if n == 0:
        empty = np.zeros((0, 0), dtype=np.float64)
        return empty, empty, empty, empty, []

    sim_biz = np.clip(biz_vecs @ biz_vecs.T, 0.0, 1.0)

    if fin_z.empty:
        sim_fin = np.ones((n, n), dtype=np.float64)
    else:
        F = fin_z.reindex(list(symbols)).fillna(0.0).to_numpy(dtype=np.float64)
        d_f = max(1, F.shape[1])
        diff = F[:, None, :] - F[None, :, :]
        dist = np.sqrt(np.sum(diff * diff, axis=2))
        sim_fin = np.exp(-dist / math.sqrt(d_f))

    has_macro = np.zeros(n, dtype=bool)
    M = np.zeros((n, max(1, len(macro_cols))), dtype=np.float64)
    peer_basis: list[str] = []
    min_macro_dims = max(2, len(macro_cols) // 2) if macro_cols else 1

    for idx, sym in enumerate(symbols):
        bmap = macro_beta_matrix.get(sym, {})
        if macro_cols and bmap and len(bmap) >= min_macro_dims:
            vec = np.array([float(bmap.get(c, 0.0)) for c in macro_cols], dtype=np.float64)
            norm = float(np.linalg.norm(vec))
            if norm > 1e-8:
                M[idx] = vec / norm
                has_macro[idx] = True
                peer_basis.append("MULTIMODAL_FULL")
                continue
        peer_basis.append("QUANT_FALLBACK_NO_MACRO")

    sim_macro = np.clip(M @ M.T, 0.0, 1.0)

    has_fin = np.array(
        [bool(has_fin_map.get(str(s), True)) if has_fin_map is not None else True for s in symbols],
        dtype=bool,
    )
    both_macro = np.outer(has_macro, has_macro)
    both_fin = np.outer(has_fin, has_fin)

    w_biz = np.full((n, n), 0.45, dtype=np.float64)
    w_mac = np.where(both_macro, 0.30, 0.0)
    w_fin = np.where(both_fin, 0.25, 0.0)
    w_sum = w_biz + w_mac + w_fin
    sim_peer = (w_biz * sim_biz + w_mac * sim_macro + w_fin * sim_fin) / np.maximum(w_sum, 1e-12)
    np.fill_diagonal(sim_peer, 1.0)
    return sim_peer, sim_biz, sim_macro, sim_fin, peer_basis


def run_leiden_clustering(
    n_nodes: int,
    edges: Sequence[tuple[int, int, float]],
    *,
    resolution: float = 2.5,
    seed: int = 42,
) -> dict[str, Any]:
    """Run deterministic Leiden clustering in-process via `igraph` + `leidenalg`."""
    if n_nodes <= 0:
        return {"membership": [], "modularity": 0.0, "quality": 0.0, "edge_count": 0}
    g = ig.Graph(n=n_nodes, directed=False)
    if edges:
        g.add_edges([(int(u), int(v)) for u, v, _ in edges])
        g.es["weight"] = [float(w) for _, _, w in edges]
        part = leidenalg.find_partition(
            g,
            leidenalg.RBConfigurationVertexPartition,
            weights="weight",
            resolution_parameter=float(resolution),
            seed=int(seed),
        )
        membership = list(part.membership)
        modularity = float(g.modularity(membership, weights="weight"))
        quality = float(part.quality())
    else:
        membership = list(range(n_nodes))
        modularity = 0.0
        quality = 0.0
    return {
        "membership": membership,
        "modularity": round(modularity, 4),
        "quality": round(quality, 4),
        "edge_count": len(edges),
    }


def build_true_peer_clusters(
    stocks_df: pd.DataFrame,
    sim_peer: np.ndarray,
    sim_biz: np.ndarray,
    sim_macro: np.ndarray,
    sim_fin: np.ndarray,
    as_of_date: date,
    *,
    k: int = 6,
    sim_threshold: float = 0.45,
    resolution: float = 2.5,
    mutual_knn: bool = True,
    strong_threshold: float | None = 0.60,
    min_biz_sim: float = 0.25,
    weight_power: float = 2.5,
    seed: int = 42,
) -> dict[str, Any]:
    """Build Mutual k-NN + Biz Floor graph, run Leiden, and emit PIT `kg_edges` + cluster maps."""
    symbols = [str(s) for s in stocks_df["symbol"].tolist()]
    n = len(symbols)
    if n == 0:
        return {
            "modularity": 0.0,
            "num_clusters": 0,
            "num_singletons": 0,
            "edge_count": 0,
            "clusters": {},
            "ticker_to_cluster": {},
            "kg_edges": [],
        }

    top_sets: list[set[int]] = []
    for i in range(n):
        row = sim_peer[i].copy()
        row[i] = -1.0
        top_idx = np.argsort(row)[::-1][:k]
        top_sets.append(
            {
                int(j)
                for j in top_idx
                if float(row[j]) >= sim_threshold and float(sim_biz[i, j]) >= min_biz_sim
            }
        )

    edge_map: dict[tuple[int, int], tuple[float, float]] = {}
    for i in range(n):
        for j in top_sets[i]:
            w_raw = float(sim_peer[i, j])
            if mutual_knn:
                is_mutual = i in top_sets[j]
                is_strong = (strong_threshold is not None) and (w_raw >= strong_threshold)
                if not (is_mutual or is_strong):
                    continue
            u, v = (i, j) if i < j else (j, i)
            w_pow = w_raw ** weight_power
            if (u, v) not in edge_map or w_pow > edge_map[(u, v)][0]:
                edge_map[(u, v)] = (w_pow, w_raw)

    leiden_edges = [(u, v, w_pow) for (u, v), (w_pow, _) in sorted(edge_map.items())]
    leiden_res = run_leiden_clustering(n, leiden_edges, resolution=resolution, seed=seed)
    membership: list[int] = leiden_res["membership"]

    clusters: dict[str, list[str]] = defaultdict(list)
    ticker_to_cluster: dict[str, str] = {}
    for idx, cid in enumerate(membership):
        cname = f"PEER:cluster_{cid:02d}"
        clusters[cname].append(symbols[idx])
        ticker_to_cluster[symbols[idx]] = cname

    kg_edges: list[dict[str, Any]] = []
    for (u, v), (_w_pow, w_raw) in sorted(edge_map.items()):
        su, sv = symbols[u], symbols[v]
        same_cluster = membership[u] == membership[v]
        src_node = f"TICKER:{su}"
        dst_node = f"TICKER:{sv}"
        kg_edges.append(
            {
                "edge_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, f"TRUE_PEER:{su}:{sv}:{as_of_date}")),
                "src_node_id": src_node,
                "dst_node_id": dst_node,
                "source_node_id": src_node,
                "target_node_id": dst_node,
                "relation_type": "TRUE_PEER_OF",
                "layer": "TRUE_PEER",
                "direction": "UNDIRECTED",
                "weight": round(w_raw, 4),
                "verification_tier": "CLUSTER",
                "evidence_json": {
                    "sim_peer": round(w_raw, 4),
                    "sim_biz": round(float(sim_biz[u, v]), 4),
                    "sim_macro": round(float(sim_macro[u, v]), 4),
                    "sim_fin": round(float(sim_fin[u, v]), 4),
                    "same_cluster": same_cluster,
                    "cluster_id": ticker_to_cluster[su] if same_cluster else None,
                },
                "effective_from": as_of_date.isoformat(),
                "valid_to": _MAX_VALID_TO.isoformat(),
            }
        )

    sizes = sorted((len(v) for v in clusters.values()), reverse=True)
    return {
        "modularity": leiden_res["modularity"],
        "num_clusters": len(clusters),
        "num_multi_member_clusters": sum(1 for s in sizes if s >= 2),
        "num_singletons": sum(1 for s in sizes if s == 1),
        "edge_count": leiden_res["edge_count"],
        "clusters": {k_: sorted(v_) for k_, v_ in sorted(clusters.items())},
        "ticker_to_cluster": ticker_to_cluster,
        "kg_edges": kg_edges,
    }
