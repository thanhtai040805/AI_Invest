from abc import ABC, abstractmethod
from typing import List, Optional, Dict, Any
from .models import NewsArticle

class INewsCrawler(ABC):
    @abstractmethod
    async def fetch_latest_links(self, category_url: str) -> List[str]:
        """Fetch latest article links from a category page."""
        pass

    @abstractmethod
    async def crawl_article(self, url: str, category: str) -> Optional[NewsArticle]:
        """Fetch and parse a full article."""
        pass

class INewsRepository(ABC):
    @abstractmethod
    def save_article(self, article: NewsArticle):
        """Save article to persistent and vector storage."""
        pass

    @abstractmethod
    def has_article(self, news_id: str) -> bool:
        """Check if article exists."""
        pass

    @abstractmethod
    def search_similar(self, query: str, symbol: Optional[str] = None, top_k: int = 5) -> List[Dict[str, Any]]:
        """Semantic search for relevant news."""
        pass

class INewsNotifier(ABC):
    @abstractmethod
    async def notify_new_article(self, article: NewsArticle, analysis: str):
        """Send notification about a new article."""
        pass

    @abstractmethod
    async def post_report(self, content: str, title: str):
        """Post a scheduled report."""
        pass
