"""Tests for LLM Output Guardrail."""
import pytest

from app.infrastructure.llm.guardrail import (
    LLMOutputGuardrail,
    LLM_ALLOWED_OUTPUTS,
    LLM_BANNED_OUTPUTS,
)


class TestGuardrailValidation:
    def test_clean_output_passes(self):
        guardrail = LLMOutputGuardrail()
        result = guardrail.validate(
            "Cổ phiếu VNM có tín hiệu tích cực từ khối ngoại.",
            {},
        )
        assert result.valid

    def test_untraced_number_rejected(self):
        guardrail = LLMOutputGuardrail()
        result = guardrail.validate(
            "Giá mục tiêu của VNM là 85000 VND.",
            {"pe": 15.5, "eps": 5000},
        )
        assert not result.valid
        assert "untraced_numbers" in result.reason or result.untraced_numbers

    def test_traced_number_passes(self):
        guardrail = LLMOutputGuardrail()
        result = guardrail.validate(
            "P/E của VNM là 15.5.",
            {"pe": 15.5},
        )
        assert result.valid

    def test_percentage_in_output(self):
        guardrail = LLMOutputGuardrail()
        result = guardrail.validate(
            "ROE của VNM là 25%.",
            {},
        )
        assert not result.valid

    def test_no_numbers_passes(self):
        guardrail = LLMOutputGuardrail()
        result = guardrail.validate(
            "Tình hình vĩ mô đang có dấu hiệu tích cực.",
            {},
        )
        assert result.valid

    def test_allowed_output_type(self):
        guardrail = LLMOutputGuardrail()
        assert guardrail.validate_output_type("narrative_text")
        assert guardrail.validate_output_type("report_section")

    def test_banned_output_type(self):
        guardrail = LLMOutputGuardrail()
        assert not guardrail.validate_output_type("target_price")
        assert not guardrail.validate_output_type("position_size")

    def test_number_in_nested_dict(self):
        guardrail = LLMOutputGuardrail()
        result = guardrail.validate(
            "Giá đóng cửa là 85000.",
            {"market_data": {"close": 85000}},
        )
        assert result.valid

    def test_llm_banned_outputs_not_in_allowed(self):
        for banned in LLM_BANNED_OUTPUTS:
            assert banned not in LLM_ALLOWED_OUTPUTS, (
                f"{banned} should not be in allowed outputs"
            )


class TestIntegrationGuardrail:
    def test_reject_fake_target_price(self):
        guardrail = LLMOutputGuardrail()
        fake_llm_output = (
            "Tôi khuyên bạn nên mua VNM ở mức giá 85000, "
            "với mục tiêu 95000 VND, dừng lỗ ở 80000."
        )
        result = guardrail.validate(fake_llm_output, {})
        assert not result.valid, "Fake LLM output with target prices should be rejected"
        assert len(result.untraced_numbers) >= 3

    def test_accept_qualitative_narrative(self):
        guardrail = LLMOutputGuardrail()
        narrative = "Khối ngoại đang mua ròng mạnh trong tuần qua. Dòng tiền cải thiện."
        result = guardrail.validate(narrative, {})
        assert result.valid
