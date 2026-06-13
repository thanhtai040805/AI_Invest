"""LLM Layer Redesign — Qualitative-Only, Tool-Augmented.

Roles:
1. Market context summary (never generates numbers)
2. News sentiment (qualitative: positive/negative/neutral)
3. Risk event identification (scenario description)
4. Natural language reports from quant data
"""
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Optional

from app.llm.guardrail import Guardrail, GuardrailResult, GuardrailViolationError


@dataclass
class LLMReportRequest:
    report_type: str
    context: dict[str, Any] = field(default_factory=dict)
    as_of_date: Optional[date] = None


@dataclass
class LLMReportResponse:
    narrative: str = ""
    sentiment: str = "neutral"
    risks: list[str] = field(default_factory=list)
    guardrail_result: Optional[GuardrailResult] = None
    error: Optional[str] = None


class LLMReportGenerator:
    """Generates qualitative-only reports. Never outputs trade signals or numbers."""

    def __init__(self, guardrail: Optional[Guardrail] = None):
        self.guardrail = guardrail or Guardrail()

    def generate_market_context(self, request: LLMReportRequest) -> LLMReportResponse:
        ...

    def analyze_risk_events(self, request: LLMReportRequest) -> LLMReportResponse:
        ...

    def generate_report_narrative(self, request: LLMReportRequest) -> LLMReportResponse:
        ...


class ToolCallResult:
    """Structured result from a quant tool call for LLM consumption."""

    def __init__(self, tool_name: str, output: str):
        self.tool_name = tool_name
        self.output = output


class LLMAgent:
    """Agent that calls quant tools and formats qualitative summaries."""

    def __init__(self, tools: dict[str, Any]):
        self.tools = tools
        self.guardrail = Guardrail()

    def call_tool(self, tool_name: str, **kwargs) -> ToolCallResult:
        if tool_name not in self.tools:
            return ToolCallResult(tool_name, "Tool not available")
        result = self.tools[tool_name](**kwargs)
        return ToolCallResult(tool_name, str(result))

    def summarize(self, results: list[ToolCallResult]) -> str:
        """Natural language summary of tool results."""
        lines = []
        for r in results:
            lines.append(f"[{r.tool_name}] {r.output}")
        narrative = "\n".join(lines)

        gr = self.guardrail.validate(narrative)
        if not gr.passed:
            raise GuardrailViolationError(gr.reason)

        return narrative
