"""Phase 1 + Phase 2 + Config Parity L1-L3 Contract Tests: Async Decision Router & PIT Zero-Mem Ledger."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from sag_api.core.config import Settings, settings
from sag_api.research.session_memory import build_financial_zeromem_ledger
from sag_api.services.decision_router import (
    RESEARCH_SHORT_CIRCUIT_MIN_CONF,
    aroute_research_query,
)


def _mock_sf_transport(answers_by_query: dict[str, dict[str, Any]], *, status_code: int = 200) -> httpx.MockTransport:
    def _handler(request: httpx.Request) -> httpx.Response:
        if status_code != 200:
            return httpx.Response(status_code=status_code, json={"error": "upstream down"})
        body = json.loads(request.content.decode("utf-8"))
        q = str(body.get("state", {}).get("query") or "")
        ans = answers_by_query.get(q) or next(iter(answers_by_query.values()))
        return httpx.Response(200, json={"answers": ans})

    return httpx.MockTransport(_handler)


@pytest.mark.asyncio
async def test_aroute_research_query_short_circuits_at_high_conf_and_fails_open_below_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "decision_provider", "siliconflow")
    monkeypatch.setattr(settings, "siliconflow_api_key", "sf-test-key")
    monkeypatch.setattr(settings, "decision_model", "SiliconFlow/Kev-4b")

    high_conf_chitchat = {
        "domain_class": {"choice": "chitchat_casual", "confidence": 0.94},
        "task_action": {"choice": "direct_reply", "confidence": 0.92},
        "evidence_source_hint": {"choice": "none", "confidence": 0.95},
        "reasoning_budget_tier": {"choice": "zero_tool", "confidence": 0.96},
        "temporal_scope": {"choice": "atemporal", "confidence": 0.95},
        "ambiguity_and_guard": {"choice": "clear", "confidence": 0.94},
        "output_style": {"choice": "conversational_short", "confidence": 0.90},
    }
    low_conf_chitchat = {
        **high_conf_chitchat,
        "domain_class": {"choice": "chitchat_casual", "confidence": 0.62},  # < 0.78 -> MUST fail open!
    }
    advice_guard = {
        "domain_class": {"choice": "out_of_domain", "confidence": 0.60},
        "task_action": {"choice": "direct_reply", "confidence": 0.85},
        "evidence_source_hint": {"choice": "none", "confidence": 0.90},
        "reasoning_budget_tier": {"choice": "zero_tool", "confidence": 0.90},
        "temporal_scope": {"choice": "atemporal", "confidence": 0.90},
        "ambiguity_and_guard": {"choice": "investment_advice_disclaimer", "confidence": 0.93},
        "output_style": {"choice": "conversational_short", "confidence": 0.88},
    }

    transport = _mock_sf_transport(
        {
            "Chào bạn, hôm nay thế nào?": high_conf_chitchat,
            "Chào bạn xem giúp mình FPT nhé": low_conf_chitchat,
            "Mai có nên tất tay mua FPT không?": advice_guard,
        }
    )
    async with httpx.AsyncClient(transport=transport) as client:
        r_chat = await aroute_research_query("Chào bạn, hôm nay thế nào?", http_client=client)
        assert r_chat["is_short_circuit"] is True
        assert r_chat["short_circuit_kind"] == "chitchat_casual"
        assert r_chat["eval_eligible"] is False
        assert r_chat["max_agent_turns"] == 0
        assert r_chat["confidence"] >= RESEARCH_SHORT_CIRCUIT_MIN_CONF

        r_fail_open = await aroute_research_query("Chào bạn xem giúp mình FPT nhé", http_client=client)
        assert r_fail_open["is_short_circuit"] is False
        assert r_fail_open["domain_class"] == "stock_research_data"
        assert r_fail_open["eval_eligible"] is True
        assert r_fail_open["max_agent_turns"] >= 2
        assert "LOW_CONFIDENCE_FAIL_OPEN_TO_RESEARCH" in r_fail_open["reason_codes"]

        r_guard = await aroute_research_query("Mai có nên tất tay mua FPT không?", http_client=client)
        assert r_guard["is_short_circuit"] is True
        assert r_guard["short_circuit_kind"] == "investment_advice_disclaimer"
        assert r_guard["eval_eligible"] is False

    # HTTP 500 error MUST fail open cleanly to stock_research_data (never block user!)
    err_transport = _mock_sf_transport({}, status_code=500)
    async with httpx.AsyncClient(transport=err_transport) as err_client:
        r_err = await aroute_research_query("So sánh FPT và MWG", chat_mode="MOAT", http_client=err_client)
        assert r_err["is_short_circuit"] is False
        assert r_err["domain_class"] == "stock_research_data"
        assert r_err["fallback_used"] is True
        assert r_err["max_agent_turns"] >= 4  # MOAT elevates budget


def test_zeromem_ledger_cross_turn_pit_quarantine_blocks_future_leakage() -> None:
    # Turn 1 ran in 2026 with FY2025 FPT numbers and a screening leaderboard.
    # Turn 2 rolls back `as_of_date` to 2023-06-30 -> Turn 1 MUST be 100% quarantined!
    history = [
        {
            "role": "user",
            "content": "Phân tích FPT năm 2025",
            "turn_as_of_date": "2026-06-30",
        },
        {
            "role": "assistant",
            "content": "Doanh thu FPT năm 2025 đạt 70,113 tỷ VND, tăng trưởng mạnh.",
            "turn_as_of_date": "2026-06-30",
            "primary_ticker": "FPT",
            "expanded_tickers": ["FPT"],
            "screened_tickers": ["FPT", "MWG"],
            "screening_summary": [{"ticker": "FPT", "fiscal_year": 2025, "roe": 0.28}],
        },
        {
            "role": "user",
            "content": "Xem giúp mình doanh thu HPG 2022",
            "turn_as_of_date": "2023-06-30",
        },
        {
            "role": "assistant",
            "content": "Doanh thu HPG năm 2022 đạt 141,409 tỷ VND.",
            "turn_as_of_date": "2023-06-30",
            "primary_ticker": "HPG",
            "expanded_tickers": ["HPG"],
        },
    ]

    ledger = build_financial_zeromem_ledger(
        history,
        request_as_of_date=date(2023, 6, 30),
        reference_today=date(2026, 11, 24),
    )
    assert ledger.quarantined_future_turns == 1
    assert len(ledger.valid_turns) == 1
    assert ledger.active_episode_tickers == ["HPG"]
    assert "FPT" not in ledger.all_session_tickers
    assert ledger.last_screening_leaderboard == []

    prompt_ctx = ledger.to_prompt_context()
    assert "70,113" not in prompt_ctx
    assert "FPT" not in prompt_ctx
    assert "HPG" in prompt_ctx
    assert "cách ly PIT 1 lượt" in prompt_ctx

    recalled = ledger.recall(tickers=["FPT"])
    assert recalled["recalled_turns"] == []


def test_zeromem_ledger_long_25_turn_session_preserves_all_tickers_and_pairs() -> None:
    tickers = ["FPT", "HPG", "MWG", "PVT", "VCB", "CTG", "DCM", "NT2", "VCI", "PNJ"]
    history: list[dict[str, Any]] = []
    for idx in range(25):
        tk = tickers[idx % len(tickers)]
        history.append(
            {
                "role": "user",
                "content": f"Lượt {idx + 1}: Phân tích biên lợi nhuận và dòng tiền của {tk}?",
                "turn_as_of_date": "2026-06-30",
            }
        )
        history.append(
            {
                "role": "assistant",
                "content": (
                    f"Kết quả lượt {idx + 1} cho {tk}: Doanh thu ổn định, biên gộp cải thiện "
                    f"theo BCTC kiểm toán [C1]. " + ("Chi tiết bổ sung. " * 25)
                ),
                "turn_as_of_date": "2026-06-30",
                "primary_ticker": tk,
                "expanded_tickers": [tk],
            }
        )

    ledger = build_financial_zeromem_ledger(
        history,
        request_as_of_date=date(2026, 6, 30),
        reference_today=date(2026, 6, 30),
    )
    # Zero SUPERSEDED ticker drops: ALL 10 distinct tickers remain in all_session_tickers
    assert set(ledger.all_session_tickers) == set(tickers)
    assert len(ledger.valid_turns) == 25
    # Pair integrity: every recent pair keeps BOTH user_content and assistant_content
    assert 1 <= len(ledger.recent_pairs) <= 3
    for pair in ledger.recent_pairs:
        assert pair.user_content and pair.assistant_content

    # Deep recall retrieves Turn 1 (FPT) even after 25 turns
    rec = ledger.recall(tickers=["FPT"], turn_indices=[1])
    assert any(t["turn_index"] == 1 for t in rec["recalled_turns"])


def test_six_file_docker_and_env_config_parity() -> None:
    repo_root = Path(__file__).resolve().parents[4]
    required_keys = [
        "SAG_CORE_DATABASE_URL",
        "SAG_DECISION_MODEL",
        "SAG_RERANK_MODEL",
    ]
    banned_switch_keys = {
        "SAG_KNOWLEDGE_TREE_REBUILD_ENABLED",
        "SAG_USER_MEMORY_MODE",
        "SAG_RESEARCH_BRAIN_MODE",
        "SAG_DOCUMENT_EVENT_EXTRACTION_ENABLED",
        "SAG_DECISION_PROVIDER",
        "SAG_RERANK_PROVIDER",
    }
    env_files = [
        repo_root / ".env.example",
        repo_root / "SAG" / ".env.example",
        repo_root / "SAG" / "apps" / "api" / ".env.example",
    ]
    for env_path in env_files:
        text = env_path.read_text(encoding="utf-8")
        seen_keys: dict[str, int] = {}
        for line in text.splitlines():
            m = re.match(r"^\s*([A-Z][A-Z0-9_]+)\s*=", line)
            if m:
                k = m.group(1)
                seen_keys[k] = seen_keys.get(k, 0) + 1
        for rk in required_keys:
            assert rk in seen_keys, f"Missing {rk} in {env_path}"
            assert seen_keys[rk] == 1, f"Duplicate {rk} in {env_path}"
        for bk in banned_switch_keys:
            assert bk not in seen_keys, f"Banned feature switch {bk} found in {env_path}"

    for compose_path in (repo_root / "docker-compose.yml", repo_root / "SAG" / "compose.yaml"):
        ctext = compose_path.read_text(encoding="utf-8")
        for rk in required_keys:
            assert rk in ctext, f"Missing {rk} in {compose_path}"
        for bk in banned_switch_keys:
            assert bk not in ctext, f"Banned feature switch {bk} found in {compose_path}"

    # Verify Pydantic Settings declares every corresponding field with production-matching defaults
    bare = Settings(_env_file=None)
    assert bare.rerank_provider == "deepinfra"
    assert bare.user_memory_mode == "on"
    assert bare.sag_qdrant_shared_collection == "aiinvest_search_units"
    for field_name in ("core_database_url", "decision_model", "rerank_provider", "rerank_model"):
        assert field_name in Settings.model_fields
