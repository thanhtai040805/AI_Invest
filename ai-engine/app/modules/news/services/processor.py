import logging
import asyncio
from typing import Optional
from ..domain.models import NewsArticle
from ..domain.ports import INewsRepository, INewsNotifier
from app.services.sentiment_scorer import sentiment_scorer
from app.services.ai_service import ai_svc
from app.services.market_data_service import market_data_svc

logger = logging.getLogger(__name__)

class NewsProcessor:
    def __init__(self, repository: INewsRepository, notifier: INewsNotifier):
        self.repository = repository
        self.notifier = notifier

    async def process_article(self, article: NewsArticle):
        if self.repository.has_article(article.news_id):
            return

        # 1. Sentiment Analysis
        sentiment_res = sentiment_scorer.analyze(article.title + " " + article.content)
        article.sentiment_label = sentiment_res['label']
        article.sentiment_score = sentiment_res['score']

        # 2. Save to DB and RAG
        self.repository.save_article(article)

        # 3. AI Analysis & Notification
        try:
            analysis = await self._generate_ai_analysis(article)
            await self.notifier.notify_new_article(article, analysis)
        except Exception as e:
            logger.error(f"AI analysis failed for {article.title}: {e}")

    async def _generate_ai_analysis(self, article: NewsArticle) -> str:
        symbol = article.symbol
        quote = await market_data_svc.get_quote(symbol) if symbol != "GENERAL" else {}
        
        rag_results = self.repository.search_similar(article.title, symbol=symbol if symbol != "GENERAL" else None, top_k=3)
        rag_context = "\n".join([f"- {a.get('publishDate')}: {a.get('title')} ({a.get('sentimentLabel')})" for a in rag_results])

        prompt = f"""
Bạn là một Giám đốc phân tích định lượng và cơ bản cứng rắn. Cấm sử dụng các cụm từ sáo rỗng như 'có thể', 'cần theo dõi thêm', 'tùy thuộc vào thị trường'. Bắt buộc đưa ra kết luận: Tích cực, Tiêu cực hay Trung lập.

[TIN TỨC MỚI NHẤT]
Mã CP: {symbol}
Tiêu đề: {article.title}
Sentiment sơ bộ: {article.sentiment_label}
Link: {article.url}

[DỮ LIỆU THỊ TRƯỜNG HIỆN TẠI]
Giá: {quote.get('price')} | Biến động: {quote.get('changePercent')}%

[TIN TỨC LIÊN QUAN (RAG)]
{rag_context if rag_context else "Không có tin tức liên quan."}

Vui lòng phân tích dựa trên sự kiện này:
- Mức độ tác động: [1 đến 10]
- Phe kiểm soát: [Bò / Gấu]
- Hành động giá dự kiến: [Kháng cự / Hỗ trợ]
- Rủi ro phản chứng: [Tại sao có thể sai?]
"""
        return ai_svc._generate_analysis(prompt, {"indices": []})
