"""Automated regression & ablation benchmark verification (V0 -> V5) for SAG-ECCE."""

from __future__ import annotations

import pytest

from tests.eval_ecce_baseline.benchmark_suite import run_ablation_benchmark


@pytest.mark.asyncio
async def test_ecce_ablation_progression_v0_to_v5() -> None:
    results = await run_ablation_benchmark()

    v0 = results["V0_legacy"]
    v1 = results["V1_hybrid"]
    v2 = results["V2_graph_ppr"]
    v4 = results["V4_closure_temporal"]
    v5 = results["V5_ecce_full"]

    # 1. Zero LLM memory calls across all token-free / ECCE rungs (V1..V5) vs V0 legacy compression calls
    assert v0["llm_memory_calls"] == 12
    for mode in ("V1_hybrid", "V2_graph_ppr", "V3_dual_hierarchy", "V4_closure_temporal", "V5_ecce_full"):
        assert results[mode]["llm_memory_calls"] == 0

    # 2. Monotonic recall gains from V0 -> V1 -> V2 (2-hop graph PPR) -> V4/V5 (anaphoric & lineage closure)
    assert v0["mean_recall"] < v1["mean_recall"]
    assert v2["mean_recall"] > v1["mean_recall"]
    assert v4["mean_recall"] >= v2["mean_recall"]
    assert v5["mean_recall"] == 1.0
    assert v5["full_hit_count"] == 12

    # 3. Decision superseding & homonym disambiguation eliminate stale contamination in V5
    assert v1["stale_contamination_rate"] > v4["stale_contamination_rate"]
    assert v5["stale_contamination_rate"] == 0.0

    # 4. V5 Submodular Budget Optimizer improves token efficiency & evidence density over V4 and V0
    assert v5["avg_history_tokens"] < v4["avg_history_tokens"]
    assert v5["avg_history_tokens"] < v0["avg_history_tokens"]
    assert v5["evidence_density_per_1k_tokens"] > v4["evidence_density_per_1k_tokens"]
    assert v5["p95_latency_ms"] < 150.0
