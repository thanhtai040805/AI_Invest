"""News Vectorization ETL Pipeline

Quy trình xử lý 14k bản ghi tin tức:
1. Fetch dữ liệu từ DB (Batch processing).
2. Trích xuất Entities (Sử dụng PhoBERT/Regex placeholder).
3. Tạo Semantic Embeddings bằng bge-vi-base.
4. Lưu vào FAISS Index.
"""

import logging
import psycopg2
import os
from typing import List, Dict, Any
from dotenv import load_dotenv

import sys
sys.path.append(os.getcwd())

from app.infrastructure.llm.local_plm_client import local_plm
from app.infrastructure.llm.graph_rag.triage import triage_engine

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("News_ETL")

class NewsVectorizationPipeline:
    def __init__(self):
        load_dotenv()
        self.db_url = os.getenv('DATABASE_URL', 'postgresql://postgres:123@localhost:5432/aiinvest')

    def extract_entities_phobert(self, text: str) -> List[str]:
        """
        Placeholder cho PhoBERT NER.
        Hiện tại dùng thuật toán bóc tách Ticker cơ bản làm ví dụ.
        Sau này sẽ load model `vinai/phobert-base` vào đây.
        """
        # TODO: Implement PhoBERT pipeline for Named Entity Recognition
        import re
        # Giả lập: Tìm các từ in hoa 3 chữ cái (có khả năng là Ticker VN)
        potential_tickers = re.findall(r'\b[A-Z]{3}\b', text)
        return list(set(potential_tickers))

    def run_batch(self, batch_size: int = 500):
        """Chạy ETL cho một lô dữ liệu."""
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            # 1. Lấy dữ liệu (Giả định lấy các bài chưa có vector, 
            # ở đây ta lấy random để test logic)
            cur.execute("SELECT id, title, article_content FROM news_events ORDER BY id DESC LIMIT %s", (batch_size,))
            rows = cur.fetchall()
            logger.info(f"Fetched {len(rows)} articles from DB.")
            
            valid_ids = []
            clean_texts = []
            
            for row in rows:
                article_id, title, content = row
                article = {"title": title, "article_content": content}
                
                # 2. Triage (Lọc tin rác)
                if not triage_engine.is_eligible_for_graphrag(article):
                    continue
                    
                # 3. Clean Text
                clean_text = triage_engine.clean_html_content(content or "")
                if not clean_text:
                    continue
                    
                # 4. Extract Entities (PhoBERT)
                entities = self.extract_entities_phobert(clean_text)
                
                # Update DB với Entities (Metadata) để phục vụ Hybrid Search sau này
                if entities:
                    cur.execute("UPDATE news_events SET symbol = %s WHERE id = %s", (",".join(entities), article_id))
                
                # Chuẩn bị văn bản để nhúng Vector (Gộp Title và Content)
                full_text = f"{title}. {clean_text}"
                valid_ids.append(article_id)
                clean_texts.append(full_text)
                
            conn.commit()
            
            # 5. Semantic Vectorization (bge-vi-base) & Lưu FAISS
            if valid_texts := len(clean_texts):
                logger.info(f"Vectorizing {valid_texts} clean articles...")
                local_plm.add_to_index(valid_ids, clean_texts)
                local_plm.save_index()
                logger.info("Successfully added to FAISS index.")
            else:
                logger.info("No eligible articles found in this batch after Triage.")
                
        except Exception as e:
            logger.error(f"ETL Pipeline Error: {e}")
            if 'conn' in locals():
                conn.rollback()
        finally:
            if 'cur' in locals(): cur.close()
            if 'conn' in locals(): conn.close()

if __name__ == "__main__":
    # Chỉ định nghĩa logic, CHƯA chạy thực tế để đợi xử lý các vấn đề News khác
    logger.info("News Vectorization Pipeline initialized. Ready for execution.")
