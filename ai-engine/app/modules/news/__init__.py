from .coordinator import NewsCoordinator
from .adapters.cafef_crawler import CafeFCrawler
from .adapters.combined_repo import CombinedNewsRepository
from .adapters.community_notifier import CommunityNotifier

def create_news_module() -> NewsCoordinator:
    crawler = CafeFCrawler()
    repository = CombinedNewsRepository()
    notifier = CommunityNotifier()
    return NewsCoordinator(crawler, repository, notifier)

# Global instance for lifespan management
news_module = create_news_module()
