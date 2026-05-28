"""VietFin loader for Vietnam stock market data.

VietFin (https://github.com/vietfin/vietfin) is an open-source Python package
that scrapes publicly available APIs from Vietnamese brokerage firms (TCBS, SSI, DNSE, VND, etc.)
No API token required.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import List

import pandas as pd

from backtest.loaders.base import DataLoaderProtocol, NoAvailableSourceError, validate_date_range
from backtest.loaders.registry import register

logger = logging.getLogger(__name__)

_INTERVAL_MAP = {
    "1D": "1d",
    "1W": "1w",
    "1M": "1mo",
}


@register
class VietFinLoader:
    """VietFin OHLCV + fundamentals loader for Vietnam equity."""

    name = "vietfin"
    markets = {"vn_equity"}
    requires_auth = False

    def __init__(self) -> None:
        self._vf = None

    def _get_vf(self):
        if self._vf is None:
            from vietfin import vf
            self._vf = vf
        return self._vf

    def is_available(self) -> bool:
        try:
            import vietfin  # noqa: F401
            return True
        except ImportError:
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
        """Fetch OHLCV data for Vietnam stocks via VietFin.

        Args:
            codes: List of stock symbols (e.g. ["VCB", "VNM"]).
            start_date: Start date in YYYY-MM-DD format.
            end_date: End date in YYYY-MM-DD format.
            interval: Bar interval — "1D", "1W", "1M".
            fields: Ignored; OHLCV always returned.

        Returns:
            Mapping ``{symbol: DataFrame(trade_date, open, high, low, close, volume)}``.
        """
        validate_date_range(start_date, end_date)
        vf = self._get_vf()
        vn_interval = _INTERVAL_MAP.get(interval, "1d")

        # VietFin uses lowercase symbols
        symbols_lower = [c.lower() for c in codes]

        result: dict[str, pd.DataFrame] = {}
        for sym, sym_lower in zip(codes, symbols_lower):
            try:
                resp = vf.equity.price.historical(
                    symbol=sym_lower,
                    start_date=start_date,
                    end_date=end_date,
                    interval=vn_interval,
                    provider="dnse",
                )
                df = resp.to_df()
                if df.empty:
                    logger.warning("VietFin returned no data for %s", sym)
                    continue

                if "date" in df.columns:
                    df["date"] = pd.to_datetime(df["date"])
                    df = df.set_index("date").sort_index()
                else:
                    df.index = pd.to_datetime(df.index)
                    df = df.sort_index()

                df.index.name = "trade_date"
                df = df[["open", "high", "low", "close", "volume"]]
                df.columns.name = None
                result[sym] = df

            except Exception as exc:
                logger.error("VietFin fetch failed for %s: %s", sym, exc)
                continue

        if not result:
            raise NoAvailableSourceError(
                f"VietFin returned no data for any symbol in {codes}"
            )
        return result

    def fetch_fundamentals(self, symbol: str) -> dict:
        """Fetch fundamental data for a Vietnam stock.

        Args:
            symbol: Stock symbol (e.g. "VCB").

        Returns:
            Dict with profile, ratios, and financial data.
        """
        vf = self._get_vf()
        sym_lower = symbol.lower()
        data = {}

        try:
            profile = vf.equity.profile(symbol=sym_lower)
            data["profile"] = profile.to_dict()
        except Exception as exc:
            logger.warning("VietFin profile failed for %s: %s", symbol, exc)

        try:
            ratios = vf.equity.fundamental.ratios(symbol=sym_lower)
            data["ratios"] = ratios.to_dict()
        except Exception as exc:
            logger.warning("VietFin ratios failed for %s: %s", symbol, exc)

        try:
            income = vf.equity.fundamental.income(symbol=sym_lower)
            data["income"] = income.to_dict()
        except Exception as exc:
            logger.warning("VietFin income failed for %s: %s", symbol, exc)

        try:
            dividends = vf.equity.fundamental.dividends(symbol=sym_lower)
            data["dividends"] = dividends.to_dict()
        except Exception as exc:
            logger.warning("VietFin dividends failed for %s: %s", symbol, exc)

        return data

    def get_universe(self) -> list[str]:
        """Get list of all available Vietnam stock symbols.

        Returns:
            List of uppercase stock symbols.
        """
        vf = self._get_vf()
        try:
            resp = vf.equity.search()
            results = resp.to_dict()
            if isinstance(results, list):
                symbols = []
                for r in results:
                    sym = r.get("symbol", "")
                    if sym:
                        symbols.append(sym.upper())
                return sorted(symbols)
        except Exception as exc:
            logger.warning("VietFin search failed: %s", exc)

        # Fallback: common VN stocks
        return [
            "VCB", "VIC", "VHM", "HPG", "MSN", "VNM", "GVR", "MWG",
            "FPT", "STB", "ACB", "BID", "CTG", "VPB", "MBB", "TCB",
            "LPB", "SHB", "SSI", "VCI", "HCM", "VND", "VRE", "BVH",
            "POW", "PLX", "SAB", "BHN", "GAS", "PVD", "PVS", "NT2",
            "HDG", "REE", "DPM", "DCM", "QNS", "NKG", "HSG", "HBC",
        ]

    def get_trading_calendar(self) -> list[str]:
        """Generate approximated trading calendar for VN market.

        Returns:
            List of trading dates in YYYY-MM-DD format (last 365 days).
        """
        end_date = datetime.now()
        start_date = end_date - timedelta(days=365)
        dates: list[str] = []

        current = start_date
        while current <= end_date:
            if current.weekday() < 5:
                dates.append(current.strftime("%Y-%m-%d"))
            current += timedelta(days=1)

        return dates
