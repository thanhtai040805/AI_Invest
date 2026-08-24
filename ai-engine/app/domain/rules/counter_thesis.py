"""Counter Thesis Engine — TASK-311

Sử dụng LLM làm "Devil's Advocate" để phản biện luận đề đầu tư.
Kiểm tra "Luật Thông Tin" (Hard Law Điều 3): ít nhất 3 tín hiệu độc lập.
Xác định các lỗ hổng (blind spots) và rủi ro bị bỏ sót.
"""

import logging
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from enum import Enum

logger = logging.getLogger(__name__)

class Verdict(Enum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"
    NEEDS_REVISION = "NEEDS_REVISION"

@dataclass
class CounterThesisReport:
    verdict: Verdict
    holes: List[str]
    rule_of_three_passed: bool
    rationale: str

class CounterThesisEngine:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def analyze_thesis(self, ticker: str, thesis_text: str, signals: List[str]) -> CounterThesisReport:
        """Phân tích luận đề và đưa ra phản biện."""
        
        # 1. Kiểm tra Hard Law Điều 3 (Rule of Three)
        num_signals = len(set(signals)) # Unique signals
        rule_passed = num_signals >= 3
        
        if not rule_passed:
            return CounterThesisReport(
                verdict=Verdict.REJECT,
                holes=["Vi phạm Hard Law Điều 3: Cần ít nhất 3 tín hiệu độc lập."],
                rule_of_three_passed=False,
                rationale=f"Luận đề chỉ có {num_signals} tín hiệu. Yêu cầu tối thiểu 3."
            )

        # 2. LLM Devil's Advocate Prompt
        prompt = f"""
        Bạn là một Chuyên gia Phản biện Đầu tư (Devil's Advocate) cực kỳ khắt khe.
        Nhiệm vụ: Tìm ra các lỗ hổng, giả định sai lầm hoặc rủi ro bị bỏ sót trong luận đề đầu tư sau.
        
        Cổ phiếu: {ticker}
        Luận đề: {thesis_text}
        Tín hiệu hỗ trợ: {', '.join(signals)}
        
        Hãy phân tích theo các khía cạnh:
        1. Rủi ro vĩ mô/ngành có bị xem nhẹ không?
        2. Tín hiệu kỹ thuật có phải là "trap" không?
        3. Dữ liệu tài chính có dấu hiệu xào nấu (Beneish) hoặc nợ vay tiềm ẩn không?
        4. Thanh khoản thực tế có đủ để thoát vị thế không?
        
        Yêu cầu trả về định dạng JSON:
        {{
            "holes": ["lỗ hổng 1", "lỗ hổng 2"],
            "risk_score": 0-100,
            "verdict": "APPROVE" | "REJECT" | "NEEDS_REVISION",
            "rationale": "Lý do chi tiết"
        }}
        """
        
        try:
            if self.llm_client:
                # Giả định llm_client có phương thức chat/analyze
                response = await self.llm_client.chat(prompt)
                data = json.loads(response)
                
                return CounterThesisReport(
                    verdict=Verdict(data["verdict"]),
                    holes=data["holes"],
                    rule_of_three_passed=True,
                    rationale=data["rationale"]
                )
            else:
                # THEO MANDATE: Không dùng mock/fallback data
                logger.error("LLM client not configured for CounterThesisEngine")
                raise RuntimeError("LLM client not configured for CounterThesisEngine")
        except Exception as e:
            logger.error(f"Error in LLM Counter Thesis: {e}")
            raise RuntimeError(f"Error in LLM Counter Thesis: {e}")

counter_thesis_engine = CounterThesisEngine()
