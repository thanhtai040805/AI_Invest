"""Vietnam fund analysis tools — search + NAV history via VietFin (Fmarket)."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from app.brain.agents.core.tools import BaseTool

logger = logging.getLogger(__name__)


def _search_funds() -> list[dict[str, Any]]:
    from vietfin import vf

    r = vf.funds.search()
    df = r.to_df()
    if df.empty:
        return []
    return df.to_dict(orient="records")


def _fund_history(symbol: str, days: int = 90) -> dict[str, Any]:
    from vietfin import vf

    end = datetime.now()
    start = end - timedelta(days=days)

    r = vf.funds.historical(
        symbol=symbol.lower(),
        start_date=start.strftime("%Y-%m-%d"),
        end_date=end.strftime("%Y-%m-%d"),
    )
    df = r.to_df()
    if df.empty:
        return {"symbol": symbol.upper(), "data": []}

    df = df.sort_values("date_nav").reset_index(drop=True)
    records = df.to_dict(orient="records")

    # Convert dates to strings for JSON serialization
    for r in records:
        for k, v in r.items():
            if hasattr(v, "isoformat"):
                r[k] = v.isoformat()

    navs = [r["nav_per_share"] for r in records if r.get("nav_per_share")]
    return {
        "symbol": symbol.upper(),
        "rows": len(records),
        "start_date": str(records[0].get("date_nav", "")),
        "end_date": str(records[-1].get("date_nav", "")),
        "nav_first": navs[0] if navs else None,
        "nav_last": navs[-1] if navs else None,
        "nav_high": max(navs) if navs else None,
        "nav_low": min(navs) if navs else None,
        "change_pct": round((navs[-1] - navs[0]) / navs[0] * 100, 2) if navs and navs[0] else 0,
        "data": records,
    }


class VNFundSearchTool(BaseTool):
    """Search and list all available Vietnam mutual funds (data from Fmarket)."""

    name = "vn_fund_search"
    description = (
        "List all available Vietnam mutual funds with their fund_id, short_name, "
        "full name, NAV, fund_type, and management_fee. "
        "Use this when the user asks about available funds or investment funds in Vietnam."
    )
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }
    repeatable = False
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        try:
            import vietfin  # noqa: F401
            return True
        except ImportError:
            return False

    def execute(self, **kwargs: Any) -> str:
        try:
            funds = _search_funds()
            if not funds:
                return json.dumps({"status": "ok", "data": [], "message": "No funds found"}, ensure_ascii=False)
            summary = [
                {
                    "fund_id": f.get("fund_id"),
                    "short_name": f.get("short_name"),
                    "name": f.get("name"),
                    "fund_type": f.get("fund_type"),
                    "nav": f.get("nav"),
                    "management_fee": f.get("management_fee"),
                    "inception_date": str(f.get("inception_date", "")) if f.get("inception_date") else "",
                }
                for f in funds
            ]
            return json.dumps({"status": "ok", "total": len(summary), "data": summary}, ensure_ascii=False)
        except ImportError:
            return json.dumps({"status": "error", "error": "vietfin package not installed"}, ensure_ascii=False)
        except Exception as exc:
            logger.warning("vn_fund_search failed: %s", exc)
            return json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False)


class VNFundHistoryTool(BaseTool):
    """Fetch NAV history for a Vietnam mutual fund (data from Fmarket)."""

    name = "vn_fund_history"
    description = (
        "Fetch NAV (Net Asset Value) history for a Vietnam mutual fund. "
        "Use the short_name from vn_fund_search as the symbol (e.g. VNDAF, VESAF, VCBF-FIF). "
        "Use this when the user asks about a specific fund's performance or NAV trend."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Fund short_name (e.g. VNDAF, VESAF, VCBF-FIF). Get from vn_fund_search first.",
            },
            "days": {
                "anyOf": [{"type": "integer"}, {"type": "string"}],
                "description": "Number of trailing days of NAV data (default 90, max 1095)",
                "default": 90,
            },
        },
        "required": ["symbol"],
    }
    repeatable = True
    is_readonly = True

    @classmethod
    def check_available(cls) -> bool:
        return VNFundSearchTool.check_available()

    def execute(self, **kwargs: Any) -> str:
        raw_symbol = kwargs.get("symbol")
        if not raw_symbol:
            return json.dumps({"status": "error", "error": "symbol is required"}, ensure_ascii=False)
        symbol = str(raw_symbol).strip().upper()

        raw_days = kwargs.get("days")
        if raw_days is None:
            days = 90
        else:
            days = min(int(raw_days), 1095)

        try:
            data = _fund_history(symbol, days)
            return json.dumps({"status": "ok", "data": data}, ensure_ascii=False)
        except ImportError:
            return json.dumps({"status": "error", "error": "vietfin package not installed"}, ensure_ascii=False)
        except Exception as exc:
            logger.warning("vn_fund_history failed for %s: %s", symbol, exc)
            return json.dumps({"status": "error", "error": f"Failed: {exc}"}, ensure_ascii=False)
