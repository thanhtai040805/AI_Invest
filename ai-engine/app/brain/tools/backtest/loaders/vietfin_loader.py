"""VietFin + vnstock loader for Vietnam stock market data.

Uses VietFin for OHLCV (DNSE provider still works).
Uses vnstock for fundamentals (profile, ratios, income) since VietFin TCBS APIs are dead.
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
    """VietFin OHLCV + vnstock fundamentals loader for Vietnam equity."""

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
            import vnstock  # noqa: F401
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

        Uses vnstock for profile, ratios, income (VietFin TCBS APIs are dead).
        Dividends have no replacement API — skipped with warning.

        Args:
            symbol: Stock symbol (e.g. "VCB").

        Returns:
            Dict with profile, ratios, and income data.
        """
        from vnstock import Vnstock
        from vnstock.api.financial import Finance

        sym_upper = symbol.upper()
        data = {}

        try:
            stock = Vnstock().stock(symbol=sym_upper, source="KBS")
            profile = stock.company.overview()
            if profile is not None and not profile.empty:
                data["profile"] = profile.iloc[0].to_dict()
        except Exception as exc:
            logger.warning("vnstock profile failed for %s: %s", symbol, exc)

        try:
            f = Finance(symbol=sym_upper, source="KBS")
            ratios = f.ratio()
            if ratios is not None and not ratios.empty:
                period_cols = [c for c in ratios.columns if c not in ("item", "item_en", "item_id")]
                if period_cols:
                    latest = period_cols[-1]
                    # Convert to dict with item names as keys
                    ratio_dict = {}
                    for _, row in ratios.iterrows():
                        val = row[latest]
                        if isinstance(val, (int, float)):
                            ratio_dict[row["item"].strip()] = val
                    data["ratios"] = ratio_dict
        except Exception as exc:
            logger.warning("vnstock ratios failed for %s: %s", symbol, exc)

        try:
            inc = f.income_statement()
            if inc is not None and not inc.empty:
                period_cols = [c for c in inc.columns if c not in ("item", "item_en", "item_id")]
                if period_cols:
                    latest = period_cols[-1]
                    inc_dict = {}
                    for _, row in inc.iterrows():
                        val = row[latest]
                        if isinstance(val, (int, float)):
                            inc_dict[row["item"].strip()] = val
                    data["income"] = inc_dict
        except Exception as exc:
            logger.warning("vnstock income failed for %s: %s", symbol, exc)

        logger.info("Dividends data unavailable — no replacement API for VietFin dividends endpoint")

        return data

    def get_universe(self) -> list[str]:
        """Get list of all available Vietnam stock symbols.

        Uses vnstock Listing API (VietFin equity.search is dead — SSI Cloudflare 403).

        Returns:
            List of uppercase stock symbols.
        """
        try:
            from vnstock.api.listing import Listing
            l = Listing()
            syms = l.all_symbols()
            if syms is not None and not syms.empty and "symbol" in syms.columns:
                return sorted(syms["symbol"].dropna().unique().tolist())
        except Exception as exc:
            logger.warning("vnstock all_symbols failed: %s", exc)

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
