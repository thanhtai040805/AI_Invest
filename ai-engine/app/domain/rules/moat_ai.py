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
        import os
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def ingest_document(self, ticker: str, doc_path: str, doc_type: str, limit: int = 3) -> str:
        """Parse và làm sạch text từ tài liệu (PDF/HTML) hoặc lấy từ DB."""
        import os
        logger.info(f"Ingesting {doc_type} for {ticker}: {doc_path}")
        
        # 1. Nếu doc_path là đường dẫn file cục bộ thực tế tồn tại
        if doc_path and os.path.exists(doc_path) and doc_path.lower().endswith(".pdf"):
            try:
                import pdfplumber
                with pdfplumber.open(doc_path) as pdf:
                    if len(pdf.pages) > 0:
                        text = "\n".join(page.extract_text() or "" for page in pdf.pages).strip()
                        if text:
                            logger.info(f"Successfully extracted {len(text)} chars from local PDF: {doc_path}")
                            return text
            except Exception as e:
                logger.warning(f"Failed parsing local PDF with pdfplumber: {e}")
                
            try:
                from pdfminer.high_level import extract_text
                text = extract_text(doc_path).strip()
                if text:
                    logger.info(f"Successfully extracted {len(text)} chars via pdfminer from: {doc_path}")
                    return text
            except Exception as e:
                logger.warning(f"Failed parsing local PDF with pdfminer: {e}")

        # 2. Ngược lại, lấy văn bản đã được cào và lưu trong database corporate_documents
        import psycopg2
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            # Khớp từ khóa theo nhóm từ đồng nghĩa của từng loại tài liệu
            doc_type_clean = doc_type.lower().strip()
            search_patterns = [f"%{doc_type}%"]
            
            if "tài chính" in doc_type_clean or "bctc" in doc_type_clean:
                search_patterns = ["%tài chính%", "%bctc%"]
            elif any(kw in doc_type_clean for kw in ("đại hội", "nghị quyết", "biên bản", "đhđcđ", "hđqt")):
                search_patterns = ["%đại hội%", "%đhđcđ%", "%hđqt%", "%nghị quyết%", "%biên bản%"]
            elif "thường niên" in doc_type_clean:
                search_patterns = ["%thường niên%"]
            elif "điều lệ" in doc_type_clean:
                search_patterns = ["%điều lệ%"]
            elif "cáo bạch" in doc_type_clean:
                search_patterns = ["%cáo bạch%"]
                
            query_conds = " OR ".join(["title ILIKE %s"] * len(search_patterns))
            sql = f"""
                SELECT title, article_pdf_text, published_date
                FROM corporate_documents
                WHERE symbol = %s
                  AND ({query_conds})
                  AND article_pdf_text IS NOT NULL AND article_pdf_text != ''
                  AND article_pdf_text NOT LIKE '[FAILED_%%'
                ORDER BY published_date DESC
                LIMIT %s
            """
            
            cur.execute(sql, (ticker.upper(), *search_patterns, limit))
            rows = cur.fetchall()
            
            # Nếu không tìm thấy, thử dùng fallback rộng hơn
            if not rows:
                logger.info(f"No specific document matching group for '{doc_type}' found. Trying fallback keywords...")
                cur.execute("""
                    SELECT title, article_pdf_text, published_date
                    FROM corporate_documents
                    WHERE symbol = %s
                      AND (title ILIKE '%%thường niên%%' OR title ILIKE '%%đại hội cổ đông%%' OR title ILIKE '%%điều lệ%%' OR title ILIKE '%%tài chính%%' OR title ILIKE '%%bctc%%')
                      AND article_pdf_text IS NOT NULL AND article_pdf_text != ''
                      AND article_pdf_text NOT LIKE '[FAILED_%%'
                    ORDER BY published_date DESC
                    LIMIT %s
                """, (ticker.upper(), limit))
                rows = cur.fetchall()
                
            cur.close()
            conn.close()
            
            if rows:
                combined_texts = []
                logger.info(f"Retrieved {len(rows)} documents for {ticker} (doc_type={doc_type})")
                for title, text, pub_date in rows:
                    date_str = pub_date.strftime("%Y-%m-%d") if pub_date else "N/A"
                    combined_texts.append(f"--- DOCUMENT: {title} (Published Date: {date_str}) ---\n{text}\n")
                
                return "\n".join(combined_texts).strip()
            else:
                logger.warning(f"No document text found in DB for {ticker} with type {doc_type}")
        except Exception as e:
            logger.error(f"Error querying document from DB for {ticker}: {e}")

        return f"Fallback: Dummy cleaned text for {ticker} from {doc_path}"

    async def calculate_moat_score(self, ticker: str, doc_text: str) -> MoatAnalysis:
        """Sử dụng LLM để phân tích Moat (Lợi thế cạnh tranh)."""
        
        prompt = f"""
        Phân tích lợi thế cạnh tranh (Moat) của công ty: {ticker} dựa trên văn bản sau.
        Văn bản: {doc_text[:100000]} # Expanded to 100k characters for comprehensive context

        
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
