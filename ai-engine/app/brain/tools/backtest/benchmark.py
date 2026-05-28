"""Benchmark ticker resolution - VN market placeholder.

Vietnam market has no universal auto-benchmark from public APIs.
Set ``benchmark`` explicitly in config (PNC for VNINDEX via VietFin).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class BenchmarkResult:
    ticker: str
    ret_series: pd.Series
    total_ret: float


_INDEX_TICKERS = {"VNINDEX", "VN30", "HNX30", "HNXINDEX", "UPCOMINDEX"}


def _fetch_index_benchmark(
    ticker: str, start_date: str, end_date: str, interval: str
) -> Optional[pd.DataFrame]:
    try:
        from vietfin import vf

        resp = vf.index.price.historical(
            symbol=ticker,
            start_date=start_date,
            end_date=end_date,
            interval=interval,
            provider="dnse",
        )
        df = resp.to_df()
        if df.empty:
            return None
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date").sort_index()
        else:
            df.index = pd.to_datetime(df.index)
            df = df.sort_index()
        df.index.name = "trade_date"
        return df
    except Exception:
        return None


def resolve_benchmark(
    strategy_codes: list[str],
    source: str,
    start_date: str,
    end_date: str,
    interval: str = "1D",
    explicit: Optional[str] = None,
) -> Optional[BenchmarkResult]:
    """Resolve benchmark ticker and fetch return series.

    For VN market, only explicit benchmarks are supported (e.g. VNINDEX).
    Set ``benchmark`` in config.json to use one.

    Args:
        strategy_codes: Instruments being backtested.
        source: Data source (vietfin/dnse).
        start_date: Backtest start date.
        end_date: Backtest end date.
        interval: Bar interval.
        explicit: Override ticker (e.g. "VNINDEX" passed via config).

    Returns:
        BenchmarkResult or None if no benchmark applies.
    """
    if not explicit:
        return None

    ticker_upper = explicit.upper()
    df = None

    if ticker_upper in _INDEX_TICKERS:
        df = _fetch_index_benchmark(ticker_upper, start_date, end_date, interval)
    else:
        try:
            from backtest.loaders.vietfin_loader import VietFinLoader

            loader = VietFinLoader()
            result = loader.fetch([explicit], start_date, end_date, interval=interval)
            df = result.get(explicit) if isinstance(result, dict) else None
        except Exception:
            pass

    if df is None or df.empty or "close" not in df.columns:
        return None

    close = df["close"].dropna()
    if len(close) < 2:
        return None

    ret_series = close.pct_change().fillna(0.0)
    total_ret = float((1 + ret_series).prod() - 1)
    return BenchmarkResult(ticker=explicit, ret_series=ret_series, total_ret=total_ret)
