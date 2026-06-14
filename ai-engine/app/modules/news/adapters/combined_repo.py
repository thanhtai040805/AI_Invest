import logging
from typing import List, Optional, Dict, Any
from ..domain.models import NewsArticle
from ..domain.ports import INewsRepository
from app.services.news_event_store import NewsEventStore
from app.services.news_rag import news_rag_svc

logger = logging.getLogger(__name__)

class CombinedNewsRepository(INewsRepository):
    def __init__(self, event_store: Optional[NewsEventStore] = None):
        self.event_store = event_store or NewsEventStore()

    def save_article(self, article: NewsArticle):
        # 1. Save to Postgres (Event Store)
        try:
            event_data = {
                "symbol": article.symbol,
                "published_date": article.publish_date,
                "title": article.title,
                "url": article.url,
                "source": "CafeF",
                "sentiment_score": article.sentiment_score or 0.0
            }
            self.event_store.store_events([event_data])
        except Exception as e:
            logger.error(f"Failed to save article to EventStore: {e}")

        # 2. Save to RAG (In-memory Vector Store)
        try:
            news_rag_svc.add_articles([article.dict(by_alias=True)])
        except Exception as e:
            logger.error(f"Failed to save article to RAG: {e}")

    def has_article(self, news_id: str) -> bool:
        return news_rag_svc.has_article(news_id)

    def search_similar(self, query: str, symbol: Optional[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        return news_rag_svc.query(query, symbol=symbol, top_k=top_k)
