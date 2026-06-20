"""
OHLCV Tool - Vietnam OHLCV data adapter
Wraps market_data_service for VN-specific OHLCV data
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class OHLCVTool:
    """
    OHLCV data tool for Vietnam market
    Wraps existing DNSE market_data_service
    """
    
    def __init__(self):
        """Initialize OHLCV Tool"""
        # Import here to avoid circular imports
        from app.infrastructure.external_api.market_data_service import MarketDataService
        self.market_service = MarketDataService()
        logger.info("OHLCV Tool initialized")
    
    async def get_ohlcv(
        self,
        symbol: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        timeframe: str = "1D",
    ) -> List[Dict[str, Any]]:
        """
        Get OHLCV data for a symbol
        
        Args:
            symbol: Stock symbol (e.g., "VIC", "FPT")
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            timeframe: Timeframe (1D, 1H, 15M, etc.)
            
        Returns:
            List of OHLCV data dictionaries
        """
        try:
            # Default to last 30 days if no dates provided
            if not end_date:
                end_date = datetime.now().strftime("%Y-%m-%d")
            if not start_date:
                start_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
            
            # Call DNSE service with timeframe
            data = await self.market_service.get_ohlcv(
                symbol=symbol,
                interval=timeframe,
                start=start_date,
                end=end_date,
            )
            if isinstance(data, dict):
                data = data.get("data", [])
            
            # Format data
            formatted_data = []
            for item in data:
                formatted_data.append({
                    "symbol": symbol,
                    "timestamp": item.get("timestamp"),
                    "open": item.get("open"),
                    "high": item.get("high"),
                    "low": item.get("low"),
                    "close": item.get("close"),
                    "volume": item.get("volume"),
                    "timeframe": timeframe,
                })
            
            logger.info(f"Retrieved {len(formatted_data)} OHLCV records for {symbol}")
            return formatted_data
            
        except Exception as e:
            logger.error(f"Failed to get OHLCV data for {symbol}: {str(e)}")
            raise
    
    async def get_latest_price(self, symbol: str) -> Dict[str, Any]:
        """
        Get latest price for a symbol
        
        Args:
            symbol: Stock symbol
            
        Returns:
            Dict containing latest price data
        """
        try:
            data = await self.market_service.get_realtime_price(symbol)
            
            return {
                "symbol": symbol,
                "price": data.get("price"),
                "change": data.get("change"),
                "change_percent": data.get("change_percent"),
                "volume": data.get("volume"),
                "timestamp": data.get("timestamp"),
            }
            
        except Exception as e:
            logger.error(f"Failed to get latest price for {symbol}: {str(e)}")
            raise
    
    async def get_price_range(
        self,
        symbol: str,
        days: int = 30
    ) -> Dict[str, Any]:
        """
        Get price range statistics
        
        Args:
            symbol: Stock symbol
            days: Number of days to analyze
            
        Returns:
            Dict containing price range statistics
        """
        try:
            end_date = datetime.now().strftime("%Y-%m-%d")
            start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
            
            data = await self.get_ohlcv(symbol, start_date, end_date)
            
            if not data:
                return {"symbol": symbol, "error": "No data available"}
            
            prices = [item["close"] for item in data]
            
            return {
                "symbol": symbol,
                "period_days": days,
                "high": max(prices),
                "low": min(prices),
                "avg": sum(prices) / len(prices),
                "current": prices[-1],
                "change_from_avg": ((prices[-1] - sum(prices) / len(prices)) / (sum(prices) / len(prices))) * 100,
            }
            
        except Exception as e:
            logger.error(f"Failed to get price range for {symbol}: {str(e)}")
            raise
