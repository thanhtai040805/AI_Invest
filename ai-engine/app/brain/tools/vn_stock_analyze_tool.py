"""Vietnam stock analysis tool — fetches OHLCV + fundamentals via VietFin.

No API key needed. Falls back to text response if vietfin is not installed.
"""

from __future__ import annotations

import csv
import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


def _analyze_vn_stock(symbol: str, days: int = 30) -> dict[str, Any]:
    """Fetch OHLCV + fundamentals for a Vietnam stock via VietFin.

    Args:
        symbol: Stock symbol (e.g. "VIX", "VCB", "VNM").
        days: Number of trailing days of OHLCV data.

    Returns:
        Dict with price data, fundamentals, and summary stats.
    """
    from vietfin import vf

    sym_lower = symbol.lower()
    end = datetime.now()
    start = end - timedelta(days=days)
    start_str = start.strftime("%Y-%m-%d")
    end_str = end.strftime("%Y-%m-%d")

    result: dict[str, Any] = {
        "symbol": symbol.upper(),
        "days": days,
        "start_date": start_str,
        "end_date": end_str,
    }

    ohlcv = vf.equity.price.historical(
        symbol=sym_lower,
        start_date=start_str,
        end_date=end_str,
        interval="1d",
        provider="dnse",
    )
    df = ohlcv.to_df()
    if not df.empty:
        df_sorted = df.sort_values("date").reset_index()
        records = df_sorted.to_dict(orient="records")
        for r in records:
            for k, v in r.items():
                if hasattr(v, "isoformat"):
                    r[k] = v.isoformat()
        result["price_data"] = records

        closes = [r["close"] for r in result["price_data"] if "close" in r]
        volumes = [r["volume"] for r in result["price_data"] if "volume" in r]
        if closes:
            result["summary"] = {
                "first_close": closes[0],
                "last_close": closes[-1],
                "high": max(closes),
                "low": min(closes),
                "avg_volume": round(sum(volumes) / len(volumes)) if volumes else 0,
                "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes[0] else 0,
                "trading_days": len(closes),
            }

    try:
        profile = vf.equity.profile(symbol=sym_lower)
        profile_data = profile.to_dict()
        if profile_data:
            if isinstance(profile_data, list):
                profile_data = profile_data[0]
            result["profile"] = {
                "name": profile_data.get("name", ""),
                "legal_name": profile_data.get("legal_name", ""),
                "exchange": profile_data.get("stock_exchange", ""),
                "industry": profile_data.get("industry_category", ""),
                "employees": profile_data.get("employees"),
                "website": str(profile_data.get("company_url", "")),
            }
    except Exception as exc:
        logger.warning("Profile fetch failed for %s: %s", symbol, exc)
        result["profile"] = {"error": "profile data unavailable"}

    try:
        ratios = vf.equity.fundamental.ratios(symbol=sym_lower)
        ratios_df = ratios.to_df()
        if not ratios_df.empty:
            latest = ratios_df.iloc[-1].to_dict() if len(ratios_df) > 0 else {}
            result["ratios"] = {
                k: v for k, v in latest.items()
                if k.lower() in ("pe", "pb", "roe", "roa", "eps", "beta", "price_to_book", "dividend_yield")
            }
    except Exception as exc:
        logger.warning("Ratios fetch failed for %s: %s", symbol, exc)
        result["ratios"] = {"error": "fundamental ratios unavailable"}

    return result


def _check_vietfin() -> bool:
    try:
        import vietfin  # noqa: F401
        return True
    except ImportError:
        return False


class VNStockAnalyzeTool(BaseTool):
    """Analyze a Vietnam stock: fetch OHLCV, profile, and fundamental ratios via VietFin."""

    name = "vn_stock_analyze"
    description = (
        "Fetch OHLCV price history + company profile + fundamental ratios "
        "for a Vietnam stock (HOSE/HNX/UPCOM). No API key needed. "
        "Use this INSTEAD of web_search when the user asks about a VN stock."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Stock symbol (e.g. VIX, VCB, VNM, FPT, HPG)",
            },
            "days": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "Number of trailing days of data (default 30, max 365)",
                "default": 30,
            },
        },
        "required": ["symbol"],
    }
    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        return _check_vietfin()

    def execute(self, **kwargs: Any) -> str:
        raw_symbol = kwargs.get("symbol")
        if not raw_symbol:
            return json.dumps({"status": "error", "error": "symbol is required"}, ensure_ascii=False)
        symbol = str(raw_symbol).strip().upper()

        raw_days = kwargs.get("days")
        if raw_days is None:
            days = 30
        else:
            days = min(int(raw_days), 365)
        run_dir = kwargs.get("run_dir")

        try:
            data = _analyze_vn_stock(symbol, days)
            price_data = data.get("price_data", [])

            # Write OHLCV CSV to run_dir/artifacts/ when run_dir is available
            if run_dir and price_data:
                try:
                    artifacts_dir = Path(str(run_dir)) / "artifacts"
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    csv_path = artifacts_dir / f"ohlcv_{symbol.lower()}.csv"
                    ohlcv_cols = ["date", "open", "high", "low", "close", "volume"]
                    with csv_path.open("w", newline="", encoding="utf-8") as f:
                        writer = csv.DictWriter(f, fieldnames=ohlcv_cols, extrasaction="ignore")
                        writer.writeheader()
                        writer.writerows(price_data)
                    logger.info("Wrote OHLCV CSV to %s (%d rows)", csv_path, len(price_data))
                except Exception as csv_err:
                    logger.warning("Failed to write OHLCV CSV for %s: %s", symbol, csv_err)

            return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)
        except ImportError:
            return json.dumps({
                "status": "error",
                "error": "vietfin package not installed. Run: pip install vietfin",
            }, ensure_ascii=False)
        except Exception as exc:
            logger.warning("vn_stock_analyze failed for %s: %s", symbol, exc)
            return json.dumps({
                "status": "error",
                "error": f"Failed to analyze {symbol}: {exc}",
            }, ensure_ascii=False)
