"""Batch Processor for GraphRAG Construction.

Kịch bản chạy nền (Background Worker) để xử lý 14k bản ghi news_events
từ từ chuyển hóa thành Knowledge Graph mà không làm nghẽn hệ thống.
"""

import asyncio
import logging
import psycopg2
import os
from dotenv import load_dotenv

# Setup path
import sys
sys.path.append(os.getcwd())

from app.infrastructure.llm.graph_rag.triage import triage_engine
from app.infrastructure.llm.graph_rag.extractor import graph_extractor

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GraphRAG_Batch")

# Mock LLM Client for demonstration
class MockLLMForGraphRAG:
    async def chat(self, prompt: str) -> str:
        # Giả lập LLM extract thành công
        return """
        {
            "entities": [
                {"id": "VHM", "type": "COMPANY", "description": "Vinhomes"},
                {"id": "Fed", "type": "MACRO", "description": "Cục dự trữ liên bang"}
            ],
            "relationships": [
                {"source": "Fed", "target": "VHM", "relation": "NEGATIVE_IMPACT", "evidence": "Fed giữ lãi suất cao làm chi phí vốn của VHM tăng"}
            ]
        }
        """

async def process_batch(batch_size: int = 100):
    """Xử lý một lô tin tức."""
    load_dotenv()
    db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:123@localhost:5432/aiinvest')
    
    try:
        conn = psycopg2.connect(db_url)
        cur = conn.cursor()
        
        # Lấy các bài báo chưa được xử lý (Giả sử ta thêm cột graphrag_processed)
        # Trong ví dụ này, ta lấy random 100 bài để chạy thử logic Triage
        cur.execute("SELECT id, title, article_content FROM news_events ORDER BY id DESC LIMIT %s", (batch_size,))
        articles = cur.fetchall()
        
        logger.info(f"Loaded {len(articles)} articles from DB.")
        
        eligible_count = 0
        extracted_graphs = []
        
        graph_extractor.llm_client = MockLLMForGraphRAG()
        
        for row in articles:
            article = {
                "id": row[0],
                "title": row[1] or "",
                "article_content": row[2] or ""
            }
            
            # 1. Triage: Lọc tin nhiễu
            if not triage_engine.is_eligible_for_graphrag(article):
                continue
                
            eligible_count += 1
            
            # 2. Clean HTML content
            clean_text = triage_engine.clean_html_content(article["article_content"])
            
            # 3. Extract Graph (Gọi LLM)
            graph_data = await graph_extractor.extract_graph_from_text(article["id"], clean_text)
            extracted_graphs.append(graph_data)
            
        logger.info(f"Triage Result: {eligible_count}/{len(articles)} articles passed the filter.")
        logger.info(f"Extracted {len(extracted_graphs)} graph segments.")
        
        # 4. Save to Graph Database or PostgreSQL (knowledge_graph_edges table)
        # TODO: Cập nhật DB
        
        conn.close()
        
    except Exception as e:
        logger.error(f"Batch Processing Error: {e}")

if __name__ == "__main__":
    asyncio.run(process_batch())
