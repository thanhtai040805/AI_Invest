import httpx
import logging
import os
from ..domain.models import NewsArticle
from ..domain.ports import INewsNotifier

logger = logging.getLogger(__name__)

BACKEND_API_URL = os.getenv("BACKEND_API_URL", "http://localhost:3001/api/v1/community/news/ingest")
BACKEND_BOT_POST_URL = os.getenv("BACKEND_BOT_POST_URL", "http://localhost:3001/api/v1/community/bot/posts")

class CommunityNotifier(INewsNotifier):
    async def notify_new_article(self, article: NewsArticle, analysis: str):
        payload = {
            "content": f"🚨 **Tin Tức: {article.symbol}** 🚨\n\n**Tiêu đề**: {article.title}\n\n**Sentiment**: {article.sentiment_label}\n\n**Nhận định AI**:\n{analysis}",
            "taggedSymbols": [article.symbol] if article.symbol != "GENERAL" else ["VNINDEX"]
        }
        await self._post_to_community(payload)

    async def post_report(self, content: str, title: str):
        payload = {
            "content": f"**{title}**\n\n{content}",
            "taggedSymbols": ["VNINDEX"]
        }
        await self._post_to_community(payload)

    async def _post_to_community(self, payload: dict):
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    BACKEND_BOT_POST_URL,
                    json=payload,
                    headers={"Authorization": "Bearer AI_BOT_SECRET_KEY"},
                    timeout=30.0
                )
                if resp.status_code == 201:
                    logger.info("Successfully posted to Community.")
                else:
                    logger.error(f"Failed to post to Community: {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Failed to call Community API: {e}")
