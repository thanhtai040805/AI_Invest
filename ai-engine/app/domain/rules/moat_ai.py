"""Moat AI Engine — TASK-221, 222

Module thu thập và phân tích tài liệu phi cấu trúc bằng LLM.
Hỗ trợ parse PDF/HTML và sinh Moat Score.
"""

import logging
import json
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from datetime import date

logger = logging.getLogger(__name__)

@dataclass
class MoatAnalysis:
    score: float # 0-100
    breakdown: Dict[str, float]
    evidence: List[str]
    hallucination_risk: str # "LOW", "MEDIUM", "HIGH"

class MoatAIEngine:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def ingest_document(self, ticker: str, doc_path: str, doc_type: str) -> str:
        """Parse và làm sạch text từ tài liệu (PDF/HTML)."""
        # TODO: Sử dụng pdfplumber hoặc thư viện tương đương
        logger.info(f"Ingesting {doc_type} for {ticker}: {doc_path}")
        return f"Dummy cleaned text for {ticker} from {doc_path}"

    async def calculate_moat_score(self, ticker: str, doc_text: str) -> MoatAnalysis:
        """Sử dụng LLM để phân tích Moat (Lợi thế cạnh tranh)."""
        
        prompt = f"""
        Phân tích lợi thế cạnh tranh (Moat) của công ty: {ticker} dựa trên văn bản sau.
        Văn bản: {doc_text[:4000]} # Truncated for token limit
        
        Hãy chấm điểm từ 0-100 trên 4 tiêu chí:
        1. Brand Power (Thương hiệu)
        2. Cost Advantage (Lợi thế chi phí)
        3. Switching Costs (Chi phí chuyển đổi)
        4. Network Effect (Hiệu ứng mạng lưới)
        
        Trả về JSON:
        {{
            "score": trung_binh_tong,
            "breakdown": {{ "brand": x, "cost": y, "switching": z, "network": w }},
            "evidence": ["trích dẫn 1", "trích dẫn 2"],
            "hallucination_risk": "LOW" | "HIGH"
        }}
        """
        
        try:
            if self.llm_client:
                response = await self.llm_client.chat(prompt)
                data = json.loads(response)
                
                return MoatAnalysis(
                    score=data["score"],
                    breakdown=data["breakdown"],
                    evidence=data["evidence"],
                    hallucination_risk=data["hallucination_risk"]
                )
            else:
                # THEO MANDATE: Không dùng mock data
                logger.error("LLM client not configured for MoatAIEngine")
                return MoatAnalysis(0.0, {}, [], "HIGH")
        except Exception as e:
            logger.error(f"Error in Moat AI Scoring: {e}")
            return MoatAnalysis(0.0, {}, [], "HIGH")

moat_ai_engine = MoatAIEngine()
