"""
Indicators Tool - Technical indicators for Vietnam market
Calculates RSI, MACD, and other technical indicators using stockstats
"""
import logging
from typing import Dict, Any, Optional, List
import pandas as pd

logger = logging.getLogger(__name__)


class IndicatorsTool:
    """
    Technical indicators tool for Vietnam market
    """
    
    def __init__(self):
        """Initialize Indicators Tool"""
        logger.info("Indicators Tool initialized")
    
    def calculate_rsi(self, prices: List[float], period: int = 14) -> List[float]:
        """
        Calculate RSI (Relative Strength Index)
        
        Args:
            prices: List of closing prices
            period: RSI period (default 14)
            
        Returns:
            List of RSI values
        """
        try:
            df = pd.DataFrame({"price": prices})
            
            # Calculate price changes
            delta = df["price"].diff()
            
            # Separate gains and losses
            gain = delta.where(delta > 0, 0)
            loss = -delta.where(delta < 0, 0)
            
            # Calculate average gain and loss
            avg_gain = gain.rolling(window=period).mean()
            avg_loss = loss.rolling(window=period).mean()
            
            # Calculate RSI
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
            
            return rsi.fillna(50).tolist()
            
        except Exception as e:
            logger.error(f"Failed to calculate RSI: {str(e)}")
            # Return neutral values on error
            return [50.0] * len(prices)
    
    def calculate_macd(
        self,
        prices: List[float],
        fast_period: int = 12,
        slow_period: int = 26,
        signal_period: int = 9
    ) -> Dict[str, List[float]]:
        """
        Calculate MACD (Moving Average Convergence Divergence)
        
        Args:
            prices: List of closing prices
            fast_period: Fast EMA period
            slow_period: Slow EMA period
            signal_period: Signal line period
            
        Returns:
            Dict containing MACD line, signal line, and histogram
        """
        try:
            df = pd.DataFrame({"price": prices})
            
            # Calculate EMAs
            ema_fast = df["price"].ewm(span=fast_period, adjust=False).mean()
            ema_slow = df["price"].ewm(span=slow_period, adjust=False).mean()
            
            # Calculate MACD line
            macd_line = ema_fast - ema_slow
            
            # Calculate signal line
            signal_line = macd_line.ewm(span=signal_period, adjust=False).mean()
            
            # Calculate histogram
            histogram = macd_line - signal_line
            
            return {
                "macd": macd_line.tolist(),
                "signal": signal_line.tolist(),
                "histogram": histogram.tolist(),
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate MACD: {str(e)}")
            # Return neutral values on error
            n = len(prices)
            return {
                "macd": [0.0] * n,
                "signal": [0.0] * n,
                "histogram": [0.0] * n,
            }
    
    def calculate_sma(self, prices: List[float], period: int = 20) -> List[float]:
        """
        Calculate Simple Moving Average (SMA)
        
        Args:
            prices: List of closing prices
            period: SMA period
            
        Returns:
            List of SMA values
        """
        try:
            df = pd.DataFrame({"price": prices})
            sma = df["price"].rolling(window=period).mean()
            return sma.fillna(prices[0]).tolist()
            
        except Exception as e:
            logger.error(f"Failed to calculate SMA: {str(e)}")
            return prices
    
    def calculate_ema(self, prices: List[float], period: int = 20) -> List[float]:
        """
        Calculate Exponential Moving Average (EMA)
        
        Args:
            prices: List of closing prices
            period: EMA period
            
        Returns:
            List of EMA values
        """
        try:
            df = pd.DataFrame({"price": prices})
            ema = df["price"].ewm(span=period, adjust=False).mean()
            return ema.tolist()
            
        except Exception as e:
            logger.error(f"Failed to calculate EMA: {str(e)}")
            return prices
    
    def calculate_bollinger_bands(
        self,
        prices: List[float],
        period: int = 20,
        std_dev: float = 2.0
    ) -> Dict[str, List[float]]:
        """
        Calculate Bollinger Bands
        
        Args:
            prices: List of closing prices
            period: Period for moving average
            std_dev: Standard deviation multiplier
            
        Returns:
            Dict containing upper, middle, and lower bands
        """
        try:
            df = pd.DataFrame({"price": prices})
            
            # Middle band (SMA)
            middle = df["price"].rolling(window=period).mean()
            
            # Standard deviation
            std = df["price"].rolling(window=period).std()
            
            # Upper and lower bands
            upper = middle + (std * std_dev)
            lower = middle - (std * std_dev)
            
            return {
                "upper": upper.fillna(prices[0]).tolist(),
                "middle": middle.fillna(prices[0]).tolist(),
                "lower": lower.fillna(prices[0]).tolist(),
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate Bollinger Bands: {str(e)}")
            n = len(prices)
            return {
                "upper": prices,
                "middle": prices,
                "lower": prices,
            }
    
    def get_all_indicators(
        self,
        ohlcv_data: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Calculate all technical indicators from OHLCV data
        
        Args:
            ohlcv_data: List of OHLCV data dictionaries
            
        Returns:
            Dict containing all calculated indicators
        """
        try:
            if not ohlcv_data:
                return {}
            
            prices = [item["close"] for item in ohlcv_data]
            
            # Calculate all indicators
            rsi = self.calculate_rsi(prices)
            macd = self.calculate_macd(prices)
            sma_20 = self.calculate_sma(prices, 20)
            sma_50 = self.calculate_sma(prices, 50)
            ema_20 = self.calculate_ema(prices, 20)
            bollinger = self.calculate_bollinger_bands(prices)
            
            return {
                "rsi": rsi[-1] if rsi else 50.0,
                "macd": macd["macd"][-1] if macd["macd"] else 0.0,
                "macd_signal": macd["signal"][-1] if macd["signal"] else 0.0,
                "macd_histogram": macd["histogram"][-1] if macd["histogram"] else 0.0,
                "sma_20": sma_20[-1] if sma_20 else prices[-1],
                "sma_50": sma_50[-1] if sma_50 else prices[-1],
                "ema_20": ema_20[-1] if ema_20 else prices[-1],
                "bollinger_upper": bollinger["upper"][-1] if bollinger["upper"] else prices[-1],
                "bollinger_middle": bollinger["middle"][-1] if bollinger["middle"] else prices[-1],
                "bollinger_lower": bollinger["lower"][-1] if bollinger["lower"] else prices[-1],
                "current_price": prices[-1],
            }
            
        except Exception as e:
            logger.error(f"Failed to calculate all indicators: {str(e)}")
            raise
