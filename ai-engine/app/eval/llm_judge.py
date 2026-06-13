import json
import logging
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

JUDGE_PROMPT = """Bạn là chuyên gia phân tích tài chính Việt Nam.
Đánh giá chất lượng bài phân tích cổ phiếu sau đây.

PHÂN TÍCH:
{analysis_text}

KẾT QUẢ THỰC TẾ SAU {holding_days} NGÀY:
- Actual return: {actual_return:+.2f}%
- Signal direction: {signal_direction}
- Hit: {hit}

Chấm điểm 1-10 cho từng tiêu chí (chỉ trả về JSON, không text khác):
{{
  "factual_accuracy": <1-10> (dữ liệu có chính xác không?),
  "reasoning_quality": <1-10> (luận điểm logic, có dẫn chứng?),
  "risk_awareness": <1-10> (có nhắc đến rủi ro không?),
  "vn_context": <1-10> (hiểu đặc thù VN: T+2, room ngoại, v.v.?),
  "actionability": <1-10> (recommendation cụ thể, khả thi?),
  "overall": <1-10>,
  "key_errors": ["<lỗi 1>", "<lỗi 2>"],
  "key_strengths": ["<điểm mạnh 1>", "<điểm mạnh 2>"],
  "verdict": "good" | "average" | "poor"
}}
"""


@dataclass
class EvalResult:
    factual_accuracy: float = 5.0
    reasoning_quality: float = 5.0
    risk_awareness: float = 5.0
    vn_context: float = 5.0
    actionability: float = 5.0
    overall: float = 5.0
    key_errors: List[str] = field(default_factory=list)
    key_strengths: List[str] = field(default_factory=list)
    verdict: str = "average"
    raw_response: str = ""


class LLMJudge:
    def __init__(self, provider: Optional[str] = None):
        self.provider = provider
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        try:
            from app.brain.providers.groq_client import GroqClient
            self._client = GroqClient()
            self.provider = "groq"
        except ImportError:
            pass
        try:
            if self._client is None:
                from openai import OpenAI
                self._client = OpenAI()
                self.provider = "openai"
        except ImportError:
            pass
        if self._client is None:
            logger.warning("No LLM client available for judge; using fallback scoring")
        return self._client

    def evaluate(self, analysis_text: str, outcome: Dict[str, Any]) -> EvalResult:
        client = self._get_client()
        if client is None:
            return self._fallback_evaluate(analysis_text, outcome)

        prompt = JUDGE_PROMPT.format(
            analysis_text=analysis_text[:8000],
            holding_days=outcome.get("holding_days", 5),
            actual_return=outcome.get("actual_return", 0.0),
            signal_direction=outcome.get("direction", "HOLD"),
            hit=outcome.get("hit", False),
        )

        try:
            if hasattr(client, "chat"):
                response = client.chat([{"role": "user", "content": prompt}])
            else:
                response = client.chat.completions.create(
                    model="qwen-32b",
                    messages=[{"role": "user", "content": prompt}],
                )
                response = response.choices[0].message.content

            raw = response
            if hasattr(response, "choices"):
                raw = response.choices[0].message.content

            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[-1]
                cleaned = cleaned.rsplit("```", 1)[0]
            if cleaned.startswith("```json"):
                cleaned = cleaned[7:]
                cleaned = cleaned.rsplit("```", 1)[0]

            data = json.loads(cleaned.strip())
            return EvalResult(
                factual_accuracy=data.get("factual_accuracy", 5),
                reasoning_quality=data.get("reasoning_quality", 5),
                risk_awareness=data.get("risk_awareness", 5),
                vn_context=data.get("vn_context", 5),
                actionability=data.get("actionability", 5),
                overall=data.get("overall", 5),
                key_errors=data.get("key_errors", []),
                key_strengths=data.get("key_strengths", []),
                verdict=data.get("verdict", "average"),
                raw_response=raw,
            )
        except Exception as e:
            logger.warning("LLM judge failed: %s", e)
            return self._fallback_evaluate(analysis_text, outcome)

    def _fallback_evaluate(self, analysis_text: str, outcome: Dict[str, Any]) -> EvalResult:
        hit = outcome.get("hit", False)
        return EvalResult(
            overall=8.0 if hit else 4.0,
            verdict="good" if hit else "poor",
            key_errors=[] if hit else ["Signal did not match outcome"],
            key_strengths=["Analysis was generated"] if hit else [],
            raw_response="",
        )
