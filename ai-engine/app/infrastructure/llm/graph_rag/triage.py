"""GraphRAG Triage Engine — The Gatekeeper

Không phải tin tức nào cũng xứng đáng được đưa vào GraphRAG.
Module này lọc 14,000+ bản ghi để loại bỏ nhiễu, spam, tin rác,
tiết kiệm chi phí LLM và giữ cho Đồ thị Tri thức (Knowledge Graph) sạch sẽ.
"""

import logging
import re
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

class NewsTriageEngine:
    def __init__(self):
        # Bộ từ khóa nhận diện tin "High Signal" (Có tác động cốt lõi)
        self.high_signal_keywords = [
            "lợi nhuận", "doanh thu", "m&a", "mua lại", "sáp nhập", 
            "cổ tức", "chia tách", "bổ nhiệm", "từ nhiệm", "chủ tịch",
            "khởi tố", "bắt giam", "lãi suất", "phá sản", "hợp đồng",
            "khởi công", "trúng thầu", "fed", "ngân hàng nhà nước"
        ]
        
        # Bộ từ khóa nhận diện tin "Noise" (Tin rác, điểm tin chung chung)
        self.noise_keywords = [
            "nhận định thị trường", "nhịp đập thị trường", "góc nhìn kỹ thuật",
            "cổ phiếu nóng", "dự báo phiên", "chứng khoán hôm nay",
            "top cổ phiếu", "khuyến nghị mua bán" # Phân tích của CTCK khác thường là nhiễu
        ]

    def is_eligible_for_graphrag(self, article: Dict[str, Any]) -> bool:
        """Đánh giá xem bài báo có đáng để đưa vào GraphRAG không."""
        
        content = article.get("article_content", "")
        title = article.get("title", "")
        
        # 1. BỘ LỌC CHIỀU DÀI (Sanity Check)
        # Tin quá ngắn (dưới 300 ký tự) thường không chứa đủ ngữ cảnh để vẽ đồ thị
        if not content or len(content.strip()) < 300:
            return False
            
        # 2. BỘ LỌC TIN RÁC (Noise Filter)
        title_lower = title.lower()
        if any(noise in title_lower for noise in self.noise_keywords):
            return False
            
        # 3. BỘ LỌC TÍN HIỆU CAO (High Signal Filter)
        content_lower = content.lower()
        signal_score = sum(1 for kw in self.high_signal_keywords if kw in content_lower or kw in title_lower)
        
        # Cần ít nhất 1 từ khóa cốt lõi để được xem xét
        if signal_score < 1:
            return False
            
        return True

    def clean_html_content(self, raw_html: str) -> str:
        """Trích xuất text sạch từ HTML lộn xộn."""
        try:
            from bs4 import BeautifulSoup
            # Parse HTML
            soup = BeautifulSoup(raw_html, "html.parser")
            
            # Xóa các thẻ không mang tính thông tin (script, style, nav, footer)
            for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
                element.decompose()
                
            # Lấy text và chuẩn hóa khoảng trắng
            text = soup.get_text(separator=' ')
            cleaned_text = re.sub(r'\s+', ' ', text).strip()
            return cleaned_text
        except ImportError:
            # Fallback regex nếu chưa cài bs4
            clean = re.sub('<[^<]+>', ' ', raw_html)
            return re.sub(r'\s+', ' ', clean).strip()

triage_engine = NewsTriageEngine()
