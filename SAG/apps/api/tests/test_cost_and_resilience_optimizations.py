"""Unit tests for DeepInfra LLM Option 1, jittered backoff, and zero-loss embedding optimizations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from sag_api.core.config import Settings
from sag_api.core.litellm_policy import apply_litellm_completion_policy
from sag_api.core.token_usage import (
    DEFAULT_MODEL_PRICING_PER_1M_USD,
    reset_runtime_token_usage,
    token_usage_scope,
    wrap_embedding_client_with_cache_and_metering,
)
from sag_api.db.models.routing_rag import CanonicalBlock
from sag_api.generation.llm import _DEEPINFRA_BUSY_FALLBACK_CHAIN, LLMClient
from sag_api.services import search_index_service
from sag_api.services.settings_service import _embedding_identity


def test_deepinfra_litellm_policy_sets_flex_and_disables_thinking() -> None:
    cfg = Settings(
        llm_provider="openai",
        llm_base_url="https://api.deepinfra.com/v1/openai",
        llm_model="deepseek-ai/DeepSeek-V4-Flash",
        llm_api_key="test-key",
    )

    req_v4 = apply_litellm_completion_policy(cfg, {"model": "openai/deepseek-ai/DeepSeek-V4-Flash", "messages": []})
    assert req_v4["extra_body"]["thinking"] == {"type": "disabled"}
    assert req_v4["extra_body"]["service_tier"] == "flex"

    req_glm = apply_litellm_completion_policy(cfg, {"model": "openai/zai-org/GLM-4.7-Flash", "messages": []})
    assert req_glm["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert req_glm["extra_body"]["service_tier"] == "flex"

    req_ling = apply_litellm_completion_policy(cfg, {"model": "openai/inclusionAI/Ling-3.0-flash-Fin", "messages": []})
    assert req_ling["extra_body"]["chat_template_kwargs"]["enable_thinking"] is False
    assert "service_tier" not in req_ling["extra_body"]

    req_nemo = apply_litellm_completion_policy(
        cfg, {"model": "openai/nvidia/NVIDIA-Nemotron-3.5-Lightning", "messages": []}
    )
    assert "service_tier" not in (req_nemo.get("extra_body") or {})

    # Non-DeepInfra endpoints must NOT receive DeepInfra service_tier="flex"
    cfg_other = Settings(
        llm_provider="openai",
        llm_base_url="https://api.siliconflow.com/v1",
        llm_model="deepseek-ai/DeepSeek-V4-Flash",
        llm_api_key="test-key",
    )
    req_other = apply_litellm_completion_policy(
        cfg_other, {"model": "openai/deepseek-ai/DeepSeek-V4-Flash", "messages": []}
    )
    assert "service_tier" not in req_other["extra_body"]


def test_fallback_chain_excludes_expensive_deepseek_v3_2() -> None:
    for primary, chain in _DEEPINFRA_BUSY_FALLBACK_CHAIN.items():
        for fb in chain:
            assert "deepseek-v3.2" not in fb.casefold(), f"Expensive V3.2 leaked in chain for {primary}"
    assert _DEEPINFRA_BUSY_FALLBACK_CHAIN["deepseek-ai/deepseek-v4-flash"] == (
        "zai-org/GLM-4.7-Flash",
        "nvidia/NVIDIA-Nemotron-3.5-Lightning",
        "inclusionAI/Ling-3.0-flash-Fin",
    )


@pytest.mark.asyncio
async def test_llm_complete_uses_jitter_backoff_then_low_cost_failover_and_records_actual_model() -> None:
    reset_runtime_token_usage()
    cfg = Settings(
        llm_provider="openai",
        llm_base_url="https://api.deepinfra.com/v1/openai",
        llm_model="deepseek-ai/DeepSeek-V4-Flash",
        llm_api_key="test-key",
    )
    client = LLMClient(cfg)
    called_models: list[str] = []

    class Busy429(Exception):
        status_code = 429

    async def fake_litellm(**kwargs):
        model = kwargs["model"]
        called_models.append(model)
        if "DeepSeek-V4-Flash" in model:
            raise Busy429("Model busy, retry later")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK fallback"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120),
        )

    with (
        patch("sag_api.generation.llm._litellm_completion", side_effect=fake_litellm),
        patch("sag_api.generation.llm.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
    ):
        with token_usage_scope(component="answer_generation", project_id="proj-fb") as ledger:
            out = await client.complete([{"role": "user", "content": "hi"}])

    assert out == "OK fallback"
    # 1 initial + 2 jittered backoff retries on primary, then failover to GLM-4.7-Flash
    assert called_models == [
        "openai/deepseek-ai/DeepSeek-V4-Flash",
        "openai/deepseek-ai/DeepSeek-V4-Flash",
        "openai/deepseek-ai/DeepSeek-V4-Flash",
        "openai/zai-org/GLM-4.7-Flash",
    ]
    assert mock_sleep.await_count == 2
    summary = ledger.to_summary()
    assert "zai-org/GLM-4.7-Flash" in summary["by_model"]
    assert "deepseek-ai/DeepSeek-V4-Flash" not in summary["by_model"]


@pytest.mark.asyncio
async def test_embedding_wrapper_retries_429_with_backoff_and_prices_batch_model() -> None:
    reset_runtime_token_usage()
    search_index_service._SHARED_TEXT_EMBEDDING_CACHE.clear()
    assert DEFAULT_MODEL_PRICING_PER_1M_USD["qwen/qwen3-embedding-0.6b-batch"]["embedding_input_per_1m"] == 0.005

    class FlakyEmbeddingClient:
        model = "Qwen/Qwen3-Embedding-0.6B-batch"

        def __init__(self) -> None:
            self.attempts = 0

        async def _create_embeddings(self, input_data, *, label: str):
            self.attempts += 1
            if self.attempts == 1:
                err = RuntimeError("HTTP 429 Model busy")
                err.status_code = 429  # type: ignore[attr-defined]
                raise err
            items = [input_data] if isinstance(input_data, str) else list(input_data)
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=[0.5, 0.5]) for _ in items],
                usage=SimpleNamespace(prompt_tokens=1000, total_tokens=1000),
            )

        async def generate(self, text: str) -> list[float]:
            resp = await self._create_embeddings(text, label="single")
            return list(resp.data[0].embedding)

        async def batch_generate(self, texts: list[str]) -> list[list[float]]:
            resp = await self._create_embeddings(texts, label="batch")
            return [list(item.embedding) for item in resp.data]

    emb = FlakyEmbeddingClient()
    wrap_embedding_client_with_cache_and_metering(emb, default_model="Qwen/Qwen3-Embedding-0.6B-batch")

    with patch("sag_api.core.token_usage.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
        with token_usage_scope(component="search_index_embedding", project_id="proj-emb") as ledger:
            vecs = await emb.batch_generate(["Doanh thu thuần 2025"])

    assert len(vecs) == 1
    assert emb.attempts == 2
    assert mock_sleep.await_count == 1
    assert ledger.to_summary()["cost_usd"] == pytest.approx(1000 * 0.005 / 1_000_000)


def test_embedding_identity_treats_batch_variant_as_compatible_space() -> None:
    cfg = Settings(embedding_model="Qwen/Qwen3-Embedding-0.6B", embedding_dimensions=1024)
    assert _embedding_identity(cfg, {"embedding_model": "Qwen/Qwen3-Embedding-0.6B-batch"}) == _embedding_identity(
        cfg, {}
    )
    assert _embedding_identity(cfg, {"embedding_model": "BAAI/bge-m3"}) != _embedding_identity(cfg, {})
    assert _embedding_identity(cfg, {"embedding_dimensions": 768}) != _embedding_identity(cfg, {})


def test_filter_covered_raw_table_blocks_preserves_uncovered_tables() -> None:
    def _blk(bid: str, btype: str, p1: int, p2: int, text: str = "content") -> CanonicalBlock:
        return CanonicalBlock(
            id=bid,
            document_version_id="ver-1",
            ordinal=0,
            block_type=btype,
            page_from=p1,
            page_to=p2,
            section_path="BCTC > Thuyết minh",
            normalized_text=text,
            content_hash=f"h-{bid}",
        )

    blocks = [
        _blk("p1", "paragraph", 1, 1, "Intro"),
        _blk("t_cov", "table", 2, 3, "| A | B |\n| 1 | 2 |"),
        _blk("t_uncov", "table", 5, 5, "| Phụ lục | Giá trị |\n| X | 10 |"),
        _blk("tc1", "table_card", 2, 2, "[Bảng: CĐKT] Chỉ tiêu: A | 1"),
    ]
    kept = search_index_service._filter_covered_raw_table_blocks(blocks)
    kept_ids = [b.id for b in kept]
    assert kept_ids == ["p1", "t_uncov", "tc1"]


@pytest.mark.asyncio
async def test_precompute_skips_table_cards_and_oversized_blocks_while_keeping_full_section_path() -> None:
    search_index_service._SHARED_TEXT_EMBEDDING_CACHE.clear()
    embedded_inputs: list[str] = []

    class RecordingEmbedder:
        model = "Qwen/Qwen3-Embedding-0.6B-batch"

        async def batch_generate(self, texts: list[str]) -> list[list[float]]:
            embedded_inputs.extend(texts)
            return [[0.1, 0.2] for _ in texts]

    long_section = (
        "THUYẾT MINH BÁO CÁO TÀI CHÍNH HỢP NHẤT > 5. CHI TIẾT KHOẢN MỤC "
        "> 5.21. Vay và nợ thuê tài chính dài hạn"
    )
    oversized_body = " ".join(f"khoan_muc_{i}" for i in range(700))

    blocks = [
        CanonicalBlock(
            id="b-prose",
            document_version_id="v1",
            ordinal=1,
            block_type="paragraph",
            page_from=1,
            page_to=1,
            section_path=long_section,
            normalized_text="Lãi suất vay dài hạn 6.8%/năm đáo hạn 2028.",
            content_hash="h1",
        ),
        CanonicalBlock(
            id="b-tcard",
            document_version_id="v1",
            ordinal=2,
            block_type="table_card",
            page_from=2,
            page_to=2,
            section_path="BCTC",
            normalized_text="[Bảng: Vay nợ] Chỉ tiêu: Vay dài hạn | 12.000 tỷ",
            content_hash="h2",
        ),
        CanonicalBlock(
            id="b-big",
            document_version_id="v1",
            ordinal=3,
            block_type="paragraph",
            page_from=3,
            page_to=3,
            section_path="BCTC",
            normalized_text=oversized_body,
            content_hash="h3",
        ),
    ]

    vecs, _ = await search_index_service.precompute_canonical_block_embeddings(
        blocks,
        embedder=RecordingEmbedder(),
        project_id="HPG",
        tenant_id="tenant_default",
    )
    assert list(vecs.keys()) == ["b-prose"]
    assert len(embedded_inputs) == 1
    # Full section_path must remain completely intact (Option 5 rejected)
    assert long_section in embedded_inputs[0]


def test_persistent_sqlite_embedding_cache_survives_ram_eviction_and_shares_batch_family(tmp_path: Path) -> None:
    db_path = tmp_path / "emb_cache.sqlite3"
    search_index_service._SHARED_TEXT_EMBEDDING_CACHE.clear()

    search_index_service.put_cached_text_embedding(
        "Doanh thu thuần HPG FY2025",
        [0.125, -0.25, 0.5, 0.75],
        model_signature="Qwen/Qwen3-Embedding-0.6B",
        disk_cache_path=db_path,
    )
    # Simulate RAM cache eviction / process restart
    search_index_service._SHARED_TEXT_EMBEDDING_CACHE.clear()

    loaded = search_index_service.get_cached_text_embedding(
        "Doanh thu thuần HPG FY2025",
        model_signature="Qwen/Qwen3-Embedding-0.6B-batch",
        disk_cache_path=db_path,
    )
    assert loaded is not None
    assert loaded == pytest.approx([0.125, -0.25, 0.5, 0.75], abs=1e-6)

    # Different model family must NOT collide
    other = search_index_service.get_cached_text_embedding(
        "Doanh thu thuần HPG FY2025",
        model_signature="BAAI/bge-m3",
        disk_cache_path=db_path,
    )
    assert other is None
