"""Async Cross-Encoder Reranker & CRAG Evidence Grader for `/research-chat`.

Supports SiliconFlow `/v1/rerank` and DeepInfra `/v1/inference/{model}` for
`Qwen/Qwen3-Reranker-0.6B` / `Qwen/Qwen3-Reranker-4B` / `BAAI/bge-reranker-v2-m3`,
with fail-safe fallback to hybrid RRF + non-entity domain-term overlap grading
when online reranking is disabled or unreachable.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from sag_api.core.config import settings
from sag_api.core.logging import get_logger
from sag_api.services.search_index_service import COMMON_STOPWORDS

log = get_logger("reranker_client")

CragGrade = Literal["CORRECT", "AMBIGUOUS", "INCORRECT"]

_TOKEN_RE = re.compile(r"[^\W_]+", flags=re.UNICODE)
_YEAR_RE = re.compile(r"^(?:19|20)\d{2}$")
_LOCATION_OR_GENERIC_TOKENS = frozenset(
    {
        "việt", "nam", "hà", "nội", "hcm", "tphcm", "sài", "gòn", "đà", "nẵng",
        "công", "ty", "ctcp", "tập", "đoàn", "cổ", "phần", "cổ_phiếu", "mã",
        "năm", "quý", "tháng", "ngày", "báo", "cáo", "tài", "chính", "hợp", "nhất",
        "hiện", "tại", "hơn", "nhiều", "ít", "tốt", "xấu", "cao", "thấp",
        "so", "sánh", "phân", "tích", "đánh", "giá", "cho", "tôi", "biết", "xem",
        "giúp", "hãy", "liệt", "kê", "tìm", "lọc", "kiểm", "tra", "chi", "tiết",
        "report", "annual", "financial", "company", "stock", "corp", "group", "jsc",
    }
)


@dataclass(frozen=True)
class RerankOutcome:
    sections: list[Any]
    crag_grade: CragGrade
    crag_reason: str
    reranker_used: bool
    reranker_provider: str | None
    reranker_model: str | None
    top_rerank_score: float | None
    domain_term_coverage: float
    dropped_count: int = 0


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-min(x, 60.0))
        return 1.0 / (1.0 + z)
    z = math.exp(max(x, -60.0))
    return z / (1.0 + z)


def _normalize_rerank_score(raw: float) -> float:
    if not math.isfinite(raw):
        return 0.0
    if 0.0 <= raw <= 1.0:
        return round(raw, 6)
    return round(_sigmoid(raw), 6)


def _extract_domain_terms(query: str, *, exclude_tickers: Sequence[str] = ()) -> set[str]:
    """Extract non-entity, non-year domain tokens from query for honest off-topic detection."""
    clean = unicodedata.normalize("NFC", (query or "").lower())
    tk_exclude = {str(t).strip().lower() for t in exclude_tickers if str(t).strip()}
    terms: set[str] = set()
    for tok in _TOKEN_RE.findall(clean):
        if len(tok) < 2:
            continue
        if tok in COMMON_STOPWORDS or tok in _LOCATION_OR_GENERIC_TOKENS or tok in tk_exclude:
            continue
        if _YEAR_RE.match(tok) or tok.isdigit():
            continue
        terms.add(tok)
    return terms


def _section_text_for_rerank(sec: Any, *, max_chars: int = 1600) -> str:
    if isinstance(sec, Mapping):
        heading = str(sec.get("heading") or sec.get("section_path") or "")
        content = str(sec.get("content") or "")
        ticker = str(sec.get("ticker") or sec.get("project_id") or "")
    else:
        heading = str(getattr(sec, "heading", "") or getattr(sec, "section_path", "") or "")
        content = str(getattr(sec, "content", "") or "")
        ticker = str(getattr(sec, "ticker", "") or getattr(sec, "project_id", "") or "")
    prefix = f"[{ticker}] " if ticker else ""
    body = f"{prefix}{heading}\n{content}".strip()
    return body[:max_chars]


def _is_table_card_section(sec: Any) -> bool:
    if isinstance(sec, Mapping):
        return bool(sec.get("table_id")) or str(sec.get("anchor") or "").startswith("table_card:")
    return bool(getattr(sec, "table_id", None)) or str(getattr(sec, "anchor", "") or "").startswith("table_card:")


def _section_ticker(sec: Any) -> str:
    if isinstance(sec, Mapping):
        return str(sec.get("ticker") or sec.get("project_id") or "").strip().upper()
    return str(getattr(sec, "ticker", "") or getattr(sec, "project_id", "") or "").strip().upper()


def _section_score(sec: Any) -> float:
    val = sec.get("score") if isinstance(sec, Mapping) else getattr(sec, "score", 0.0)
    try:
        return float(val or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _with_updated_score(sec: Any, new_score: float) -> Any:
    if isinstance(sec, Mapping):
        out = dict(sec)
        out["score"] = round(new_score, 6)
        return out
    if hasattr(sec, "model_copy"):
        return sec.model_copy(update={"score": round(new_score, 6)})
    try:
        from dataclasses import is_dataclass, replace

        if is_dataclass(sec):
            return replace(sec, score=round(new_score, 6))
    except Exception:  # noqa: BLE001
        pass
    return sec


class CrossEncoderRerankerClient:
    """Async HTTP Cross-Encoder client supporting SiliconFlow `/v1/rerank` and DeepInfra."""

    def __init__(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        timeout_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._provider = (provider or settings.rerank_provider or "disabled").strip().lower()
        self._model = (model or settings.rerank_model or "Qwen/Qwen3-Reranker-0.6B").strip()
        self._base_url = (base_url or settings.rerank_base_url or "").strip()
        self._api_key = (api_key or settings.rerank_api_key or "").strip()
        self._timeout = float(
            timeout_seconds if timeout_seconds is not None else settings.rerank_timeout_seconds
        )
        self._http_client = http_client

    @property
    def provider(self) -> str:
        return self._provider

    @property
    def model(self) -> str:
        return self._model

    def is_enabled(self) -> bool:
        if self._provider == "disabled":
            return False
        return bool(self._resolve_api_key())

    def _resolve_api_key(self) -> str:
        if self._api_key:
            return self._api_key
        if self._provider == "siliconflow":
            return (settings.siliconflow_api_key or "").strip()
        if self._provider == "deepinfra":
            return (settings.llm_api_key or settings.embedding_api_key or "").strip()
        return ""

    def _resolve_endpoint(self) -> str:
        if self._base_url:
            clean = self._base_url.rstrip("/")
            if clean.endswith("/rerank") or "/inference/" in clean:
                return clean
            if self._provider == "deepinfra":
                return f"{clean}/inference/{self._model}"
            return f"{clean}/rerank"
        if self._provider == "siliconflow":
            return f"{settings.siliconflow_base_url.rstrip('/')}/rerank"
        return f"https://api.deepinfra.com/v1/inference/{self._model}"

    async def score_documents(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[float] | None:
        """Return calibrated relevance probabilities `[0.0..1.0]` aligned 1:1 with `documents`."""
        if not self.is_enabled() or not documents:
            return None
        api_key = self._resolve_api_key()
        url = self._resolve_endpoint()
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        if self._provider == "deepinfra":
            body: dict[str, Any] = {
                "queries": [query],
                "documents": list(documents),
            }
        else:
            body = {
                "model": self._model,
                "query": query,
                "documents": list(documents),
                "top_n": len(documents),
                "return_documents": False,
            }

        timeout = httpx.Timeout(self._timeout, connect=min(1.0, self._timeout))
        try:
            if self._http_client is not None:
                resp = await self._http_client.post(url, headers=headers, json=body, timeout=timeout)
            else:
                async with httpx.AsyncClient(timeout=timeout) as client:
                    resp = await client.post(url, headers=headers, json=body)
            resp.raise_for_status()
            payload = resp.json()
            return self._parse_scores(payload, len(documents))
        except httpx.HTTPStatusError as exc:
            log.warning(
                "Cross-encoder reranker (%s) failed with HTTP %d; falling back to hybrid RRF",
                self._provider,
                exc.response.status_code,
            )
            return None
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "Cross-encoder reranker (%s) failed (%s); falling back to hybrid RRF",
                self._provider,
                type(exc).__name__,
            )
            return None

    @staticmethod
    def _parse_scores(payload: Any, doc_count: int) -> list[float] | None:
        if not isinstance(payload, Mapping):
            return None
        # SiliconFlow / Jina / vLLM standard `/v1/rerank` shape
        results = payload.get("results")
        if isinstance(results, list):
            scores = [0.0] * doc_count
            matched = 0
            for item in results:
                if not isinstance(item, Mapping):
                    continue
                idx = item.get("index")
                raw = item.get("relevance_score", item.get("score"))
                if isinstance(idx, int) and 0 <= idx < doc_count and isinstance(raw, (int, float)):
                    scores[idx] = _normalize_rerank_score(float(raw))
                    matched += 1
            return scores if matched > 0 else None

        # DeepInfra `/v1/inference/{model}` shape: `{"scores": [...]}`
        raw_scores = payload.get("scores")
        if isinstance(raw_scores, list) and len(raw_scores) == doc_count:
            return [_normalize_rerank_score(float(s)) for s in raw_scores]
        return None


async def rerank_and_grade_sections(
    query: str,
    sections: Sequence[Any],
    *,
    top_k: int = 6,
    target_tickers: Sequence[str] = (),
    protect_table_cards: bool = True,
    min_table_cards: int = 1,
    min_rerank_score: float | None = None,
    reranker: CrossEncoderRerankerClient | None = None,
) -> RerankOutcome:
    """Run Cross-Encoder rerank (when enabled) + Table-Card floor + Per-Ticker Quota + CRAG grading."""
    items = list(sections)
    if not items:
        return RerankOutcome(
            sections=[],
            crag_grade="INCORRECT",
            crag_reason="NO_CANDIDATE_SECTIONS",
            reranker_used=False,
            reranker_provider=None,
            reranker_model=None,
            top_rerank_score=None,
            domain_term_coverage=0.0,
        )

    client = reranker or CrossEncoderRerankerClient()
    threshold = float(min_rerank_score if min_rerank_score is not None else settings.rerank_min_score)
    bounded_pool = items[:48]

    scores: list[float] | None = None
    reranker_used = False
    if client.is_enabled():
        docs = [_section_text_for_rerank(sec) for sec in bounded_pool]
        scores = await client.score_documents(query, docs)
        reranker_used = scores is not None and len(scores) == len(bounded_pool)

    scored_pairs: list[tuple[float, int, Any]] = []
    for idx, sec in enumerate(bounded_pool):
        if reranker_used and scores is not None:
            s_val = scores[idx]
            scored_pairs.append((s_val, idx, _with_updated_score(sec, s_val)))
        else:
            scored_pairs.append((_section_score(sec), idx, sec))

    scored_pairs.sort(key=lambda t: (-t[0], t[1]))
    top_rerank_score = scored_pairs[0][0] if (reranker_used and scored_pairs) else None

    # Selection with Per-Ticker Floor Quota + Audited Table-Card Protection
    selected: list[Any] = []
    selected_positions: set[int] = set()
    eff_top_k = max(1, top_k)

    # 1) Protect 1..2 top table_card sections when numerical tables are relevant
    if protect_table_cards and min_table_cards > 0:
        tbl_added = 0
        for score, orig_idx, sec in scored_pairs:
            if tbl_added >= min_table_cards or len(selected) >= eff_top_k:
                break
            if _is_table_card_section(sec) and (not reranker_used or score >= threshold * 0.6):
                selected.append(sec)
                selected_positions.add(orig_idx)
                tbl_added += 1

    # 2) Multi-ticker floor quota so no requested ticker is starved by longer filings
    norm_targets = sorted({str(t).strip().upper() for t in target_tickers if str(t).strip()})
    if len(norm_targets) >= 2:
        per_tk_floor = max(1, eff_top_k // len(norm_targets))
        counts_by_tk: dict[str, int] = {tk: 0 for tk in norm_targets}
        for sec in selected:
            tk = _section_ticker(sec)
            if tk in counts_by_tk:
                counts_by_tk[tk] += 1
        for round_no in range(per_tk_floor):
            del round_no
            for tk in norm_targets:
                if len(selected) >= eff_top_k or counts_by_tk[tk] >= per_tk_floor:
                    continue
                for _score, orig_idx, sec in scored_pairs:
                    if orig_idx in selected_positions:
                        continue
                    if _section_ticker(sec) == tk:
                        selected.append(sec)
                        selected_positions.add(orig_idx)
                        counts_by_tk[tk] += 1
                        break

    # 3) Fill remaining slots by highest score
    for _score, orig_idx, sec in scored_pairs:
        if len(selected) >= eff_top_k:
            break
        if orig_idx not in selected_positions:
            selected.append(sec)
            selected_positions.add(orig_idx)

    # Preserve descending relevance order so the top cross-encoder hit is always [C1]
    selected.sort(key=lambda s: -_section_score(s))

    # Compute non-entity domain-term overlap for honest CRAG grading (zero fake cosine threshold!)
    domain_terms = _extract_domain_terms(query, exclude_tickers=norm_targets)
    if domain_terms:
        combined_text = unicodedata.normalize(
            "NFC",
            "\n".join(_section_text_for_rerank(sec, max_chars=2000).lower() for sec in selected),
        )
        matched_terms = sum(1 for term in domain_terms if term in combined_text)
        domain_cov = round(matched_terms / float(len(domain_terms)), 4)
    else:
        domain_cov = 1.0

    # CRAG Grade assignment
    if reranker_used and top_rerank_score is not None:
        if top_rerank_score >= max(0.35, threshold * 2.0):
            grade: CragGrade = "CORRECT"
            reason = f"rerank_top={top_rerank_score:.3f} >= strong_floor"
        elif top_rerank_score >= threshold:
            grade = "AMBIGUOUS"
            reason = f"rerank_top={top_rerank_score:.3f} in ambiguous_band"
        else:
            grade = "INCORRECT"
            reason = f"rerank_top={top_rerank_score:.3f} < threshold={threshold:.3f}"
    else:
        if domain_terms and domain_cov <= 0.0:
            grade = "INCORRECT"
            reason = "zero_domain_term_overlap_across_retrieved_sections"
        elif domain_terms and domain_cov < 0.34:
            grade = "AMBIGUOUS"
            reason = f"partial_domain_term_overlap={domain_cov:.2f}"
        else:
            grade = "CORRECT"
            reason = f"domain_term_overlap={domain_cov:.2f}"

    return RerankOutcome(
        sections=selected,
        crag_grade=grade,
        crag_reason=reason,
        reranker_used=reranker_used,
        reranker_provider=client.provider if reranker_used else None,
        reranker_model=client.model if reranker_used else None,
        top_rerank_score=top_rerank_score,
        domain_term_coverage=domain_cov,
        dropped_count=max(0, len(items) - len(selected)),
    )
