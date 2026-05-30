"""Vietnam market index tool — fetch index OHLCV data via VietFin (DNSE)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)

_INDEX_MAP = {
    "VNINDEX": "vnindex",
    "VN30": "vn30",
    "HNX": "hnxindex",
    "HNX30": "hnx30",
    "UPCOM": "upcomindex",
}


def _index_price(symbol: str, days: int = 30) -> dict[str, Any]:
    from vietfin import vf

    sym_lower = _INDEX_MAP.get(symbol.upper(), symbol.lower())
    end = datetime.now()
    start = end - timedelta(days=days)

    r = vf.index.price.historical(
        symbol=sym_lower,
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
        interval="1d",
        provider="dnse",
    )
    df = r.to_df()
    if df.empty:
        return {"symbol": symbol.upper(), "data": []}

    df = df.sort_values("date").reset_index()
    records = df.to_dict(orient="records")

    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()

    closes = [r["close"] for r in records if r.get("close")]
    return {
        "symbol": symbol.upper(),
        "rows": len(records),
        "start_date": str(records[0].get("date", "")),
        "end_date": str(records[-1].get("date", "")),
        "first_close": closes[0] if closes else None,
        "last_close": closes[-1] if closes else None,
        "high": max(closes) if closes else None,
        "low": min(closes) if closes else None,
        "change_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes and closes[0] else 0,
        "data": records,
    }


class VNIndexTool(BaseTool):
    """Fetch OHLCV price history for Vietnam market indices (VNINDEX, VN30, HNX, etc.)."""

    name = "vn_index"
    description = (
        "Fetch OHLCV price history for a Vietnam stock index. "
        "Supported indices: VNINDEX (HOSE), VN30, HNX, HNX30, UPCOM. "
        "Use this when the user asks about market index performance or benchmark data."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Index symbol: VNINDEX, VN30, HNX, HNX30, or UPCOM",
            },
            "days": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "Number of trailing days (default 30, max 365)",
                "default": 30,
            },
        },
        "required": ["symbol"],
    }
    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        try:
            import vietfin  # noqa: F401
            return True
        except ImportError:
            return False

    def execute(self, **kwargs: Any) -> str:
        raw_symbol = kwargs.get("symbol")
        if not raw_symbol:
            return json.dumps({"status": "error", "error": "symbol is required"}, ensure_ascii=False)
        symbol = str(raw_symbol).strip().upper()

        if symbol not in _INDEX_MAP:
            return json.dumps({
                "status": "error",
                "error": f"Unsupported index: {symbol}. Supported: {', '.join(_INDEX_MAP.keys())}",
            }, ensure_ascii=False)

        raw_days = kwargs.get("days")
        if raw_days is None:
            days = 30
        else:
            days = min(int(raw_days), 365)

        try:
            data = _index_price(symbol, days)
            run_dir = kwargs.get("run_dir")
            if run_dir and data.get("data"):
                from pathlib import Path
                import csv
                try:
                    artifacts_dir = Path(str(run_dir)) / "artifacts"
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    csv_path = artifacts_dir / f"index_{symbol.lower()}.csv"
                    cols = ["date", "open", "high", "low", "close", "volume"]
                    with csv_path.open("w", newline="", encoding="utf-8") as f:
                        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
                        w.writeheader()
                        w.writerows(data["data"])
                    logger.info("Wrote index CSV to %s (%d rows)", csv_path, data["rows"])
                except Exception as csv_err:
                    logger.warning("Failed to write index CSV: %s", csv_err)

            return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)
        except ImportError:
            return json.dumps({"status": "error", "error": "vietfin package not installed"}, ensure_ascii=False)
        except Exception as exc:
            logger.warning("vn_index failed for %s: %s", symbol, exc)
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)
