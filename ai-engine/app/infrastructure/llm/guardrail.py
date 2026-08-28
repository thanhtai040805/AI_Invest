"""LLM Output Guardrail — Hard validation để LLM tuyệt đối không sinh số giao dịch.

ALLOWED outputs:
  - narrative_text: Diễn giải bằng ngôn ngữ tự nhiên
  - qualitative_flag: "tin tiêu cực", "rủi ro cao", "cần xem xét"
  - hypothesis_text: Gợi ý giả thuyết để quant test
  - news_classification: Phân loại tin positive/negative/neutral
  - report_section: Văn bản báo cáo có trích dẫn nguồn

BANNED outputs:
  - target_price: Phải từ quant model
  - stop_loss: Phải từ risk model (ATR, drawdown)
  - position_size: Phải từ vol-scaled sizing
  - buy_sell_hold: Phải từ factor composite + threshold
  - price_prediction: Phải từ ML model
  - confidence_score: Phải từ calibrated model
"""
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)

LLM_ALLOWED_OUTPUTS = {
    "narrative_text",
    "qualitative_flag",
    "hypothesis_text",
    "news_classification",
    "report_section",
}

LLM_BANNED_OUTPUTS = {
    "target_price",
    "stop_loss",
    "position_size",
    "buy_sell_hold",
    "price_prediction",
    "confidence_score",
}


@dataclass
class ValidationResult:
    valid: bool
    reason: str = ""
    untraced_numbers: list[str] = field(default_factory=list)


class LLMOutputGuardrail:
    """Hard validation: parse LLM output, reject if contains untraceable numbers."""

    NUMBER_PATTERN = re.compile(r"\b\d+(?:\.\d+)?(?:%|VND|đồng|%)?\b")

    def __init__(self, allowed_keys: Optional[set[str]] = None):
        self.allowed_keys = allowed_keys or LLM_ALLOWED_OUTPUTS

    def validate(self, llm_output: str, tool_results: dict[str, Any]) -> ValidationResult:
        """Validate LLM output contains only traceable numbers.

        Args:
            llm_output: Raw text from LLM
            tool_results: Dict of tool results (source of truth for numbers)

        Returns:
            ValidationResult with valid=False if untraceable numbers found
        """
        numbers_in_output = self.NUMBER_PATTERN.findall(llm_output)

        if not numbers_in_output:
            return ValidationResult(valid=True, reason="No numbers in output")

        untraced = [
            n for n in numbers_in_output
            if not self._is_in_tool_results(n, tool_results)
        ]

        if untraced:
            return ValidationResult(
                valid=False,
                reason=f"LLM sinh số không truy vết: {untraced}. "
                       f"Yêu cầu mọi số phải đến từ tool result.",
                untraced_numbers=untraced,
            )
        return ValidationResult(valid=True, reason="All numbers traced to tool results")

    def validate_output_type(self, output_type: str) -> bool:
        """Check if output type is in the allowed list."""
        return output_type in self.allowed_keys

    def _is_in_tool_results(self, number_str: str, tool_results: dict) -> bool:
        """Check if a number string appears in any tool result."""
        for key, value in tool_results.items():
            if isinstance(value, str) and number_str in value:
                return True
            if isinstance(value, (int, float)) and str(value) == number_str:
                return True
            if isinstance(value, dict):
                if self._is_in_dict(number_str, value):
                    return True
        return False

    def _is_in_dict(self, number_str: str, d: dict) -> bool:
        for v in d.values():
            if isinstance(v, str) and number_str in v:
                return True
            if isinstance(v, (int, float)) and str(v) == number_str:
                return True
        return False


Guardrail = LLMOutputGuardrail
GuardrailResult = ValidationResult


class GuardrailViolationError(Exception):
    """Raised when LLM output violates guardrail constraints."""
    pass

