"""GraphRAG Extractor — The Knowledge Graph Builder

Chuyển đổi Text tự do thành Đồ thị Tri thức (Entities & Relationships).
Đây là bước cốt lõi của công nghệ GraphRAG (Microsoft approach).
"""

import json
import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class GraphRAGExtractor:
    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    async def extract_graph_from_text(self, article_id: int, text: str) -> Dict[str, List[Dict]]:
        """Dùng LLM để trích xuất Thực thể và Mối quan hệ từ Text."""
        
        prompt = f"""
        Nhiệm vụ: Trích xuất Đồ thị tri thức (Knowledge Graph) từ văn bản tin tức tài chính sau.
        
        Văn bản:
        ---
        {text[:4000]} # Truncate để an toàn token
        ---
        
        Hãy xác định:
        1. "Entities" (Thực thể): Các Công ty (TICKER), Cá nhân, Cơ quan nhà nước, Sự kiện vĩ mô.
        2. "Relationships" (Mối quan hệ): Mối liên hệ có ý nghĩa giữa các thực thể (VD: ACQUIRES, SUES, COMPETES_WITH, REGULATES, IMPACTS).
        
        Trả về ĐÚNG định dạng JSON sau, không có thêm text nào khác:
        {{
            "entities": [
                {{"id": "VHM", "type": "COMPANY", "description": "Vinhomes"}},
                {{"id": "Luật Đất đai 2024", "type": "REGULATION", "description": "Luật sửa đổi..."}}
            ],
            "relationships": [
                {{"source": "Luật Đất đai 2024", "target": "VHM", "relation": "POSITIVE_IMPACT", "evidence": "Luật mới giúp VHM gỡ vướng pháp lý tại dự án X"}}
            ]
        }}
        """
        
        try:
            if not self.llm_client:
                raise ValueError("LLM Client is required for GraphRAG Extraction")
                
            response = await self.llm_client.chat(prompt)
            # Làm sạch response để đảm bảo parse được JSON
            response = response.replace("```json", "").replace("```", "").strip()
            data = json.loads(response)
            
            # Gắn ID bài báo vào để có thể truy ngược (Traceability)
            for rel in data.get("relationships", []):
                rel["article_id"] = article_id
                
            return data
            
        except Exception as e:
            logger.error(f"GraphRAG Extraction Error for article {article_id}: {e}")
            return {"entities": [], "relationships": []}

graph_extractor = GraphRAGExtractor()
