"""Vietnam factor data tool — fetch multi-stock OHLCV + ratios and output CSVs for factor_analysis.

Produces two CSVs matching factor_analysis input contract:
  - factor_csv:  index=date, columns=codes (daily forward-filled ratio values)
  - return_csv:  index=date, columns=codes (daily forward returns)
"""

from __future__ import annotations

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)

# How many symbols to fetch ratios for concurrently.
_RATIO_FETCH_WORKERS = 10

# Maximum symbols per vnstock batch OHLCV call.
_VN_BATCH_SIZE = 50

# English → Vietnamese keyword map for finding the right ratio row.
_FACTOR_KEYWORDS: dict[str, list[str]] = {
    "pe": ["P/E", "Chỉ số P/E"],
    "pb": ["P/B", "Chỉ số P/B"],
    "eps": ["EPS"],
    "beta": ["Beta"],
    "roe": ["ROE"],
    "roa": ["ROA"],
    "dividend_yield": ["Tỷ suất cổ tức"],
    "price_to_book": ["Giá trị sổ sách của cổ phiếu (BVPS)"],
}


# ---------------------------------------------------------------------------
# Data fetching helpers
# ---------------------------------------------------------------------------


def _fetch_symbols(universe: str | None, symbols: list[str] | None) -> list[str]:
    """Resolve symbol list from universe name or explicit list."""
    if symbols:
        return [s.upper().strip() for s in symbols if s and s.strip()]
    if universe == "vn-index":
        try:
            from vnstock.api.listing import Listing
            lst = Listing()
            df = lst.symbols_by_exchange("HOSE")
            if df is not None and not df.empty and "symbol" in df.columns:
                out = df["symbol"].astype(str).str.strip().tolist()
                logger.info("vn-index: %d symbols from Listing HOSE", len(out))
                return out
        except Exception as exc:
            logger.warning("vn-index Listing failed: %s", exc)
    raise ValueError(
        "Provide either a non-empty 'symbols' list or universe='vn-index'"
    )


def _fetch_ohlcv_panel(
    codes: list[str], start: str, end: str
) -> dict[str, pd.DataFrame]:
    """Fetch daily OHLCV for multiple symbols via vnstock, return {code: DataFrame}."""
    from vnstock import Vnstock

    stock = Vnstock().stock(symbol=codes[0], source="KBS")
    fetched: dict[str, pd.DataFrame] = {}

    for i in range(0, len(codes), _VN_BATCH_SIZE):
        batch = codes[i : i + _VN_BATCH_SIZE]
        try:
            raw = stock.quote.history(
                symbol=",".join(batch),
                start_date=start,
                end_date=end,
                type="stock",
            )
        except Exception as exc:
            logger.warning("OHLCV batch [%d:%d] failed: %s", i, i + _VN_BATCH_SIZE, exc)
            continue
        if raw is None or raw.empty:
            continue
        raw = raw.copy()
        raw["time"] = pd.to_datetime(raw["time"])
        for code in batch:
            mask = raw["ticker"] == code
            if not mask.any():
                continue
            df = raw[mask].sort_values("time").set_index("time")
            df.index.name = "date"
            for col in ("open", "high", "low", "close", "volume"):
                df[col] = pd.to_numeric(df[col], errors="coerce")
            keep = [c for c in ("open", "high", "low", "close", "volume") if c in df.columns]
            df = df[keep].dropna(subset=["open", "high", "low", "close"])
            if not df.empty:
                fetched[code] = df
    return fetched


def _fetch_one_ratio_series(
    symbol: str, factor_key: str
) -> tuple[str, pd.Series | None]:
    """Fetch a single ratio time series for one symbol.

    Returns (symbol, Series indexed by period-end dates) or (symbol, None).
    """
    keywords = _FACTOR_KEYWORDS.get(factor_key)
    if not keywords:
        logger.warning("Unknown factor key %s", factor_key)
        return symbol, None
    try:
        from vnstock.api.financial import Finance
        f = Finance(symbol=symbol, source="KBS")
        ratios = f.ratio()
        if ratios is None or ratios.empty:
            return symbol, None
        period_cols = [
            c for c in ratios.columns if c not in ("item", "item_en", "item_id")
        ]
        if not period_cols:
            return symbol, None
        for _, row in ratios.iterrows():
            item = str(row["item"]).strip()
            if any(kw in item for kw in keywords):
                values = {}
                for col in period_cols:
                    val = row[col]
                    if isinstance(val, (int, float)):
                        values[col] = val
                if values:
                    series = pd.Series(values, name=symbol)
                    series.index = pd.to_datetime(series.index)
                    return symbol, series
        return symbol, None
    except Exception as exc:
        logger.debug("Ratio fetch failed for %s: %s", symbol, exc)
        return symbol, None


def _build_factor_df(
    codes: list[str], factor_key: str, trading_dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """Build daily factor_df: fetch quarterly ratios, ffill to trading dates."""
    ratio_series: dict[str, pd.Series] = {}
    with ThreadPoolExecutor(max_workers=_RATIO_FETCH_WORKERS) as pool:
        futures = [pool.submit(_fetch_one_ratio_series, c, factor_key) for c in codes]
        for fut in as_completed(futures):
            try:
                sym, series = fut.result()
                if series is not None and not series.empty:
                    ratio_series[sym] = series
            except Exception as exc:
                logger.debug("Ratio worker failed: %s", exc)

    if not ratio_series:
        raise RuntimeError(f"No ratio data found for factor={factor_key}")

    # Merge all ratio series into wide frame (period→symbol), then ffill to daily.
    period_df = pd.DataFrame(ratio_series)
    daily = period_df.reindex(trading_dates, method="ffill")
    return daily


def _build_return_df(
    close_panel: dict[str, pd.Series], trading_dates: pd.DatetimeIndex
) -> pd.DataFrame:
    """Build forward-return DataFrame from close prices."""
    close_df = pd.DataFrame(close_panel)
    close_df = close_df.reindex(trading_dates)
    return_df = close_df.pct_change().shift(-1)
    return return_df


# ---------------------------------------------------------------------------
# Period parsing
# ---------------------------------------------------------------------------


def _parse_period(period: str) -> tuple[str, str]:
    """Return (start, end) as YYYY-MM-DD strings."""
    import re
    m = re.match(r"^(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})$", period)
    if m:
        return m.group(1), m.group(2)
    m = re.match(r"^(\d{4})-(\d{4})$", period)
    if m:
        return f"{m.group(1)}-01-01", f"{m.group(2)}-12-31"
    raise ValueError(f"period {period!r} must be YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_vn_factor_data(**kwargs: Any) -> dict[str, Any]:
    """Fetch VN factor data and write CSVs for factor_analysis.

    Returns envelope with factor_csv and return_csv paths.
    """
    raw_symbols = kwargs.get("symbols")
    universe = kwargs.get("universe")
    factor_key = kwargs.get("factor", "pe")
    period = kwargs.get("period")
    days = kwargs.get("days", 365 * 2)
    output_dir_raw = kwargs.get("output_dir")

    # Resolve symbols
    try:
        codes = _fetch_symbols(universe, raw_symbols)
    except ValueError as exc:
        return {"status": "error", "error": str(exc)}
    if not codes:
        return {"status": "error", "error": "Empty symbol list"}

    # Resolve period
    end = datetime.now()
    if period:
        try:
            start_str, end_str = _parse_period(period)
        except ValueError as exc:
            return {"status": "error", "error": str(exc)}
    else:
        start = end - timedelta(days=int(days))
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

    # Resolve output dir
    run_dir = kwargs.get("run_dir")
    if output_dir_raw:
        out = Path(str(output_dir_raw)).expanduser().resolve()
    elif run_dir:
        out = Path(str(run_dir)) / "artifacts"
    else:
        out = Path.cwd() / "artifacts"
    out.mkdir(parents=True, exist_ok=True)

    # Step 1: fetch OHLCV
    logger.info("Fetching OHLCV for %d symbols [%s..%s]...", len(codes), start_str, end_str)
    ohlcv = _fetch_ohlcv_panel(codes, start_str, end_str)
    active_codes = sorted(ohlcv.keys())
    if not active_codes:
        return {"status": "error", "error": "No OHLCV data returned"}
    logger.info("Got OHLCV for %d/%d symbols", len(active_codes), len(codes))

    # Build trading dates from union of all close indices
    all_dates = pd.DatetimeIndex(sorted(set().union(*(df.index for df in ohlcv.values()))))
    if len(all_dates) < 2:
        return {"status": "error", "error": "Fewer than 2 trading dates"}

    # Close panel for return computation
    close_panel = {
        code: ohlcv[code]["close"] for code in active_codes if "close" in ohlcv[code].columns
    }

    # Step 2: fetch ratios and build factor_df
    logger.info("Fetching ratio=%s for %d symbols...", factor_key, len(active_codes))
    try:
        factor_df = _build_factor_df(active_codes, factor_key, all_dates)
    except RuntimeError as exc:
        return {"status": "error", "error": str(exc)}

    # Step 3: build return_df
    return_df = _build_return_df(close_panel, all_dates)

    # Align: keep only dates and codes present in both
    common_codes = sorted(set(factor_df.columns) & set(return_df.columns))
    common_dates = factor_df.index.intersection(return_df.index)
    if len(common_codes) < 5:
        return {
            "status": "error",
            "error": f"Only {len(common_codes)} common codes — need ≥5 for IC",
        }

    factor_df = factor_df.loc[common_dates, common_codes].sort_index().dropna(how="all")
    return_df = return_df.loc[common_dates, common_codes].sort_index().dropna(how="all")

    if factor_df.empty or return_df.empty:
        return {"status": "error", "error": "Empty aligned factor/return data"}

    # Step 4: write CSVs
    factor_path = out / f"factor_{factor_key}.csv"
    return_path = out / f"forward_return.csv"

    factor_df.to_csv(factor_path)
    return_df.to_csv(return_path)

    logger.info(
        "Wrote factor=%s (%d dates × %d codes) and return (%d × %d) to %s",
        factor_key,
        len(factor_df), len(factor_df.columns),
        len(return_df), len(return_df.columns),
        out,
    )

    return {
        "status": "ok",
        "factor": factor_key,
        "n_symbols": len(common_codes),
        "n_dates": len(common_dates),
        "factor_csv": str(factor_path),
        "return_csv": str(return_path),
        "output_dir": str(out),
        "files": [factor_path.name, return_path.name],
        "next_step": (
            f'Now call factor_analysis('
            f'factor_csv="{factor_path}", '
            f'return_csv="{return_path}", '
            f'output_dir="{out}")'
        ),
    }


# ---------------------------------------------------------------------------
# Tool class
# ---------------------------------------------------------------------------


class VNFactorDataTool(BaseTool):
    """Fetch multi-stock factor values + forward returns for Vietnam stocks, write CSVs for factor_analysis."""

    name = "vn_factor_data"
    description = (
        "Fetch ratio factor values (P/E, P/B, ROE, ROA, EPS, Beta...) "
        "for multiple Vietnam stocks and produce factor_csv + return_csv "
        "ready for factor_analysis. "
        "Usage: vn_factor_data(universe='vn-index', factor='pe', period='2024-2025')"
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbols": {
                "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string"}],
                "description": "List of stock symbols (e.g. VCB,VNM,HPG). Mutually exclusive with universe.",
            },
            "universe": {
                "type": "string",
                "description": 'Pre-built universe: "vn-index" (all HOSE ~400 stocks). Mutually exclusive with symbols.',
            },
            "factor": {
                "type": "string",
                "description": "Factor ratio to fetch: pe | pb | roe | roa | eps | beta | dividend_yield | price_to_book (default pe).",
                "default": "pe",
            },
            "period": {
                "type": "string",
                "description": "YYYY-YYYY or YYYY-MM-DD/YYYY-MM-DD (default trailing days).",
            },
            "days": {
                "type": "integer",
                "description": "Trailing days (default 730, ignored if period is set).",
                "default": 730,
            },
            "output_dir": {
                "type": "string",
                "description": "Output directory for CSVs (default run_dir/artifacts or ./artifacts).",
            },
        },
        "required": [],
    }
    repeatable = True
    is_readonly = True

    def execute(self, **kwargs: Any) -> str:
        envelope = run_vn_factor_data(**kwargs)
        return json.dumps(envelope, ensure_ascii=False)
