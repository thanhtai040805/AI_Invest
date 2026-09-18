"""Unit tests for Counter-Thesis LLM integration, JSON parser and Guardrail clamping."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from app.infrastructure.llm.client import clean_and_parse_json
from app.domain.rules.counter_thesis import CounterThesisEngine


def test_clean_and_parse_json_variants():
    # 1. Plain JSON
    plain = '{"is_truly_independent": true, "blindspot_penalty": 5.0, "fatal_flaw": false}'
    res1 = clean_and_parse_json(plain)
    assert res1["is_truly_independent"] is True
    assert res1["blindspot_penalty"] == 5.0

    # 2. Markdown fenced JSON
    fenced = """```json
    {
        "is_truly_independent": false,
        "blindspot_penalty": 12.5,
        "fatal_flaw": true,
        "holes": ["Rủi ro nợ xấu lớn"]
    }
    ```"""
    res2 = clean_and_parse_json(fenced)
    assert res2["is_truly_independent"] is False
    assert res2["blindspot_penalty"] == 12.5
    assert res2["fatal_flaw"] is True
    assert res2["holes"] == ["Rủi ro nợ xấu lớn"]

    # 3. JSON with leading/trailing text and trailing commas
    dirty = """Dưới đây là kết quả phân tích phản biện:
    {
        "is_truly_independent": true,
        "blindspot_penalty": 8.0,
        "fatal_flaw": false,
        "holes": ["Cạnh tranh gay gắt",],
    }
    Hy vọng kết quả trên hữu ích."""
    res3 = clean_and_parse_json(dirty)
    assert res3["is_truly_independent"] is True
    assert res3["blindspot_penalty"] == 8.0
    assert res3["holes"] == ["Cạnh tranh gay gắt"]


def test_counter_thesis_llm_clamping_and_normalization():
    async def _run():
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(return_value={
            "is_truly_independent": "yes",
            "blindspot_penalty": 55.0,  # Vượt trần 20.0
            "fatal_flaw": "no",
            "holes": ["Dung Quất 2 trễ hạn"],
            "rationale": "Thử nghiệm kẹp biên độ"
        })

        engine = CounterThesisEngine(llm_client=mock_llm)
        is_indep, penalty, fatal_flaw, holes, rationale = await engine.analyze_thesis_with_llm(
            ticker="HPG",
            thesis_payload={"thesis_body": {}},
            signals=["Signal 1", "Signal 2", "Signal 3"]
        )

        # Penalty phải bị kẹp về tối đa 20.0
        assert penalty == 20.0
        assert is_indep is True
        assert fatal_flaw is False
        assert holes == ["Dung Quất 2 trễ hạn"]
        assert "Thử nghiệm" in rationale

    asyncio.run(_run())


def test_counter_thesis_llm_graceful_fallback_on_error():
    async def _run():
        mock_llm = MagicMock()
        mock_llm.complete_json = AsyncMock(side_effect=RuntimeError("Timeout connection"))

        engine = CounterThesisEngine(llm_client=mock_llm)
        is_indep, penalty, fatal_flaw, holes, rationale = await engine.analyze_thesis_with_llm(
            ticker="HPG",
            thesis_payload={"thesis_body": {}},
            signals=["Signal 1", "Signal 2", "Signal 3"]
        )

        # Phải fallback an toàn không làm crash chương trình
        assert is_indep is True
        assert penalty == 0.0
        assert fatal_flaw is False

    asyncio.run(_run())
