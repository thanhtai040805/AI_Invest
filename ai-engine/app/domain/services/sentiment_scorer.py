"""
Vietnamese Stock Market Sentiment Scorer.
Provides specialized lexicon-based sentiment analysis for Vietnamese financial news.
"""
import re
from typing import Dict, Any

# Specialized Vietnamese financial market lexicon
POSITIVE_WORDS = [
    "tăng trưởng", "vượt kế hoạch", "lợi nhuận khủng", "lãi lớn", "thắng lớn",
    "bứt phá", "đột phá", "hấp dẫn", "mua ròng", "tích cực", "khả quan",
    "đạt đỉnh", "mở rộng", "ký kết", "thành công", "phục hồi", "đi lên",
    "dẫn dắt", "kỷ lục", "vượt trội", "tăng mạnh", "đại hội cổ đông thông qua",
    "chi trả cổ tức", "chia cổ tức", "chia thưởng"
]

NEGATIVE_WORDS = [
    "thua lỗ", "giảm sâu", "bán tháo", "cơ quan điều tra", "khởi tố", "bị bắt",
    "giảm sàn", "tiêu cực", "kém khả quan", "sụt giảm", "ảnh hưởng nặng",
    "hoãn", "hủy bỏ", "thất bại", "lo ngại", "rủi ro", "nợ xấu", "áp lực bán",
    "cảnh báo", "hạn chế giao dịch", "đình chỉ", "vi phạm", "bị phạt", "bán ròng"
]

class FinancialSentimentScorer:
    def analyze(self, text: str) -> Dict[str, Any]:
        """Analyze text and return sentiment score and label."""
        if not text:
            return {"score": 0.0, "label": "NEUTRAL"}

        text_lower = text.lower()
        
        # Calculate scores
        pos_count = sum(len(re.findall(re.escape(word), text_lower)) for word in POSITIVE_WORDS)
        neg_count = sum(len(re.findall(re.escape(word), text_lower)) for word in NEGATIVE_WORDS)
        
        total = pos_count + neg_count
        if total == 0:
            return {"score": 0.0, "label": "NEUTRAL"}

        score = (pos_count - neg_count) / total
        
        if score > 0.15:
            label = "POSITIVE"
        elif score < -0.15:
            label = "NEGATIVE"
        else:
            label = "NEUTRAL"
            
        return {
            "score": round(score, 2),
            "label": label,
            "positiveCount": pos_count,
            "negativeCount": neg_count
        }

sentiment_scorer = FinancialSentimentScorer()
