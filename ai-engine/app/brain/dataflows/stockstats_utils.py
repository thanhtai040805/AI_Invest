"""Stock statistics helpers wrapping yfinance + stockstats."""

import os
import time
import pandas as pd
import yfinance as yf
from datetime import datetime, timedelta

from .config import get_config


def yf_retry(fn, max_retries=3, delay=1):
    """Retry a yfinance call with exponential backoff."""
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception as e:
            if attempt == max_retries - 1:
                raise
            time.sleep(delay * (2 ** attempt))


def load_ohlcv(symbol: str, curr_date: str, look_back_days: int = 365):
    """Load OHLCV data for a symbol."""
    end = datetime.strptime(curr_date, "%Y-%m-%d")
    start = end - timedelta(days=look_back_days)
    ticker = yf.Ticker(symbol.upper())
    data = ticker.history(start=start.strftime("%Y-%m-%d"), end=curr_date)
    return data


def filter_financials_by_date(data, curr_date):
    """Filter financial dataframe to rows before or on curr_date."""
    if data is None or data.empty:
        return data
    cutoff = datetime.strptime(curr_date, "%Y-%m-%d")
    if isinstance(data.index, pd.DatetimeIndex):
        return data[data.index <= cutoff]
    return data


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    """Clean a dataframe: drop fully null columns, round floats."""
    if df is None or df.empty:
        return df
    df = df.dropna(axis=1, how="all")
    for col in df.select_dtypes(include=["float64", "float32"]):
        df[col] = df[col].round(4)
    return df


class StockstatsUtils:
    """Wrapper around stockstats for technical indicator computation."""

    @staticmethod
    def get_stock_stats(symbol: str, indicator: str, curr_date: str) -> pd.DataFrame:
        """Compute a technical indicator using stockstats."""
        config = get_config()
        cache_dir = config.get("data_cache_dir", "./cache/trading")
        os.makedirs(cache_dir, exist_ok=True)

        data = load_ohlcv(symbol, curr_date)
        if data.empty:
            return pd.DataFrame()

        try:
            from stockstats import wrap
            stock = wrap(data)
            result = stock[indicator]
            if isinstance(result, pd.Series):
                return result.to_frame(name=indicator)
            return result
        except ImportError:
            # Fallback: compute simple indicators without stockstats
            return _compute_fallback(data, indicator, symbol, curr_date)

    @staticmethod
    def get_bulk_stats(symbol: str, indicators: list[str], curr_date: str) -> dict:
        """Compute multiple indicators at once."""
        results = {}
        for ind in indicators:
            try:
                df = StockstatsUtils.get_stock_stats(symbol, ind, curr_date)
                if df is not None and not df.empty:
                    results[ind] = df
            except Exception:
                pass
        return results


def _compute_fallback(data: pd.DataFrame, indicator: str, symbol: str, curr_date: str) -> pd.DataFrame:
    """Fallback computation of basic indicators without stockstats."""
    result = pd.DataFrame(index=data.index)
    ind = indicator.lower()

    if ind == "close_5_sma":
        result[indicator] = data["Close"].rolling(5).mean()
    elif ind == "close_10_sma":
        result[indicator] = data["Close"].rolling(10).mean()
    elif ind == "close_20_sma":
        result[indicator] = data["Close"].rolling(20).mean()
    elif ind == "close_50_sma":
        result[indicator] = data["Close"].rolling(50).mean()
    elif ind == "close_200_sma":
        result[indicator] = data["Close"].rolling(200).mean()
    elif ind == "rsi":
        delta = data["Close"].diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rs = gain / loss.replace(0, float("nan"))
        result[indicator] = 100 - (100 / (1 + rs))
    elif ind.startswith("volume_"):
        days = int(ind.split("_")[1]) if ind.split("_")[1].isdigit() else 20
        result[indicator] = data["Volume"].rolling(days).mean()
    else:
        result[indicator] = data["Close"].pct_change().rolling(20).mean()

    return result
