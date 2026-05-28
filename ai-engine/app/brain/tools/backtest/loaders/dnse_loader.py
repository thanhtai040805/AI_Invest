"""DNSE loader for Vietnam market data.

Integrates with VN adapters (ohlcv_tool, indicators_tool, fundamentals_tool)
to provide market data for backtesting on Vietnam stock market.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List, Optional

import pandas as pd

from .base import DataLoaderProtocol, NoAvailableSourceError, validate_date_range
from .registry import register

logger = logging.getLogger(__name__)


@register
class DNSELoader:
    """DNSE data loader for Vietnam market.

    Uses VN adapters to fetch OHLCV data, indicators, and fundamentals.
    """

    name = "dnse"
    markets = ["vn_equity"]

    def __init__(self):
        """Initialize DNSE loader."""
        # Import VN adapters
        from app.brain.dataflows.vendors.vn.ohlcv_tool import OHLCVTool
        from app.brain.dataflows.vendors.vn.indicators_tool import IndicatorsTool
        from app.brain.dataflows.vendors.vn.fundamentals_tool import FundamentalsTool

        self.ohlcv_tool = OHLCVTool()
        self.indicators_tool = IndicatorsTool()
        self.fundamentals_tool = FundamentalsTool()

    def is_available(self) -> bool:
        """Check if DNSE data source is available.

        Returns:
            bool: True if DNSE is configured and available
        """
        try:
            from app.config.settings import get_settings
            settings = get_settings()
            return settings.dnse_enabled and settings.dnse_configured
        except Exception:
            return False

    def fetch(
        self,
        codes: list[str],
        start_date: str,
        end_date: str,
        *,
        interval: str = "1D",
        fields: list[str] | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Fetch OHLCV data for multiple symbols.

        Args:
            codes: List of stock symbols.
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            interval: Bar interval (1D/1W/1M).
            fields: Ignored; OHLCV always returned.

        Returns:
            Mapping {symbol: DataFrame}.
        """
        validate_date_range(start_date, end_date)
        freq_map = {"1D": "daily", "1W": "weekly", "1M": "monthly"}
        frequency = freq_map.get(interval, "daily")

        result: dict[str, pd.DataFrame] = {}
        for symbol in codes:
            try:
                df = self.fetch_ohlcv(symbol, start_date, end_date, frequency=frequency)
                if not df.empty:
                    result[symbol] = df
            except Exception as exc:
                logger.error("DNSE fetch failed for %s: %s", symbol, exc)
                continue

        if not result:
            raise NoAvailableSourceError(
                f"DNSE returned no data for any symbol in {codes}"
            )
        return result

    def fetch_ohlcv(
        self,
        symbol: str,
        start_date: str,
        end_date: str,
        frequency: str = "daily",
    ) -> pd.DataFrame:
        """Fetch OHLCV data for a symbol.

        Args:
            symbol: Stock symbol (e.g., "VCB")
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            frequency: Data frequency (daily, weekly, monthly)

        Returns:
            DataFrame with OHLCV data
        """
        try:
            # Calculate days from date range
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d")
            days = (end - start).days

            # Fetch OHLCV data using VN adapter
            ohlcv_data = self.ohlcv_tool.get_ohlcv(
                symbol=symbol,
                days=days,
            )

            if not ohlcv_data:
                logger.warning(f"No OHLCV data found for {symbol}")
                return pd.DataFrame()

            # Convert to DataFrame
            df = pd.DataFrame(ohlcv_data)

            # Filter by date range
            df["date"] = pd.to_datetime(df["date"])
            df = df[(df["date"] >= start) & (df["date"] <= end)]

            # Standardize column names
            df = df.rename(columns={
                "date": "timestamp",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            })

            # Set timestamp as index
            df = df.set_index("timestamp").sort_index()

            return df

        except Exception as e:
            logger.error(f"Failed to fetch OHLCV data for {symbol}: {str(e)}")
            return pd.DataFrame()

    def fetch_fundamentals(
        self,
        symbol: str,
    ) -> dict:
        """Fetch fundamental data for a symbol.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with fundamental data
        """
        try:
            fundamentals = self.fundamentals_tool.get_fundamentals(symbol)
            return fundamentals or {}

        except Exception as e:
            logger.error(f"Failed to fetch fundamentals for {symbol}: {str(e)}")
            return {}

    def get_universe(self) -> list[str]:
        """Get list of available symbols.

        Returns:
            List of stock symbols
        """
        # For now, return a placeholder list
        # In production, this should query DNSE for available symbols
        return [
            "VCB", "VIC", "VHM", "HPG", "MSN", "VNM", "GVR", "MWG", "FPT", "STB",
            # Add more VN symbols as needed
        ]

    def get_trading_calendar(self) -> list[str]:
        """Get trading calendar dates.

        Returns:
            List of trading dates in YYYY-MM-DD format
        """
        # For now, return a placeholder
        # In production, this should use VNCalendar
        from app.brain.dataflows.vendors.vn.calendar import VNCalendar

        calendar = VNCalendar()
        dates = []

        # Generate trading dates for the last year
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)

        current_date = start_date
        while current_date <= end_date:
            if calendar.is_trading_day(current_date):
                dates.append(current_date.strftime("%Y-%m-%d"))
            current_date += timedelta(days=1)

        return dates
