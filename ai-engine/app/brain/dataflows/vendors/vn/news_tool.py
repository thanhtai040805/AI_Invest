"""
News Tool - Vietnam market news adapter
Wraps news ingestion service for sentiment analysis
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class NewsTool:
    """
    News data tool for Vietnam market
    Wraps news ingestion service
    """
    
    def __init__(self):
        """Initialize News Tool"""
        # Import here to avoid circular imports
        from app.services.news_ingestion import NewsIngestionService
        self.news_service = NewsIngestionService()
        logger.info("News Tool initialized")
    
    async def get_news(
        self,
        symbol: Optional[str] = None,
        days: int = 7,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get news articles
        
        Args:
            symbol: Stock symbol (optional, for symbol-specific news)
            days: Number of days to look back
            limit: Maximum number of articles
            
        Returns:
            List of news article dictionaries
        """
        try:
            end_date = datetime.now()
            start_date = end_date - timedelta(days=days)
            
            # Call news ingestion service
            articles = await self.news_service.get_articles(
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                limit=limit,
            )
            
            # Format articles
            formatted_articles = []
            for article in articles:
                formatted_articles.append({
                    "title": article.get("title"),
                    "summary": article.get("summary"),
                    "url": article.get("url"),
                    "published_at": article.get("published_at"),
                    "source": article.get("source"),
                    "symbol": article.get("symbol"),
                    "sentiment": article.get("sentiment"),
                    "sentiment_score": article.get("sentiment_score"),
                })
            
            logger.info(f"Retrieved {len(formatted_articles)} news articles")
            return formatted_articles
            
        except Exception as e:
            logger.error(f"Failed to get news: {str(e)}")
            return []
    
    async def get_sentiment(
        self,
        symbol: str,
        days: int = 7
    ) -> Dict[str, Any]:
        """
        Get sentiment analysis for a symbol
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dict containing sentiment analysis
        """
        try:
            articles = await self.get_news(symbol=symbol, days=days)
            
            if not articles:
                return {
                    "symbol": symbol,
                    "sentiment": "NEUTRAL",
                    "sentiment_score": 0.0,
                    "article_count": 0,
                }
            
            # Calculate average sentiment
            sentiment_scores = [
                a.get("sentiment_score", 0) 
                for a in articles 
                if a.get("sentiment_score") is not None
            ]
            
            if not sentiment_scores:
                avg_score = 0.0
            else:
                avg_score = sum(sentiment_scores) / len(sentiment_scores)
            
            # Classify sentiment
            if avg_score > 0.3:
                sentiment = "POSITIVE"
            elif avg_score < -0.3:
                sentiment = "NEGATIVE"
            else:
                sentiment = "NEUTRAL"
            
            return {
                "symbol": symbol,
                "sentiment": sentiment,
                "sentiment_score": avg_score,
                "article_count": len(articles),
                "positive_count": len([a for a in articles if a.get("sentiment") == "POSITIVE"]),
                "negative_count": len([a for a in articles if a.get("sentiment") == "NEGATIVE"]),
                "neutral_count": len([a for a in articles if a.get("sentiment") == "NEUTRAL"]),
                "period_days": days,
            }
            
        except Exception as e:
            logger.error(f"Failed to get sentiment for {symbol}: {str(e)}")
            return {
                "symbol": symbol,
                "sentiment": "NEUTRAL",
                "sentiment_score": 0.0,
                "article_count": 0,
                "error": str(e),
            }
    
    async def get_market_news(
        self,
        days: int = 1,
        limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Get general market news (not symbol-specific)
        
        Args:
            days: Number of days to look back
            limit: Maximum number of articles
            
        Returns:
            List of market news articles
        """
        try:
            return await self.get_news(symbol=None, days=days, limit=limit)
            
        except Exception as e:
            logger.error(f"Failed to get market news: {str(e)}")
            return []
    
    def classify_headline_sentiment(self, headline: str) -> Dict[str, Any]:
        """
        Quick sentiment classification for a headline (rule-based)
        For production, use LLM-based classification
        
        Args:
            headline: News headline
            
        Returns:
            Dict containing sentiment classification
        """
        positive_keywords = [
            "tăng", "lên", "khởi sắc", "thăng", "vượt", "thành công",
            "mở rộng", "tăng trưởng", "lợi nhuận", "khởi động", "ký kết",
        ]
        
        negative_keywords = [
            "giảm", "xuống", "đóng cửa", "thu hẹp", "sụt", "thất bại",
            "thua lỗ", "giảm trưởng", "cắt giảm", "hủy bỏ", "rút lui",
        ]
        
        headline_lower = headline.lower()
        
        positive_count = sum(1 for kw in positive_keywords if kw in headline_lower)
        negative_count = sum(1 for kw in negative_keywords if kw in headline_lower)
        
        if positive_count > negative_count:
            sentiment = "POSITIVE"
            score = min(0.5 + (positive_count * 0.1), 1.0)
        elif negative_count > positive_count:
            sentiment = "NEGATIVE"
            score = max(-0.5 - (negative_count * 0.1), -1.0)
        else:
            sentiment = "NEUTRAL"
            score = 0.0
        
        return {
            "headline": headline,
            "sentiment": sentiment,
            "sentiment_score": score,
            "method": "rule_based",
        }
