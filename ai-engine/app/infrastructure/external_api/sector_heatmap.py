from math import isfinite
from typing import Any


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return result if isfinite(result) else 0.0


def build_sector_heatmap(stocks: list[dict[str, Any]], include_live_count: bool = False) -> list[dict[str, Any]]:
    sectors: dict[str, dict[str, Any]] = {}

    for stock in stocks:
        price = _number(stock.get("price"))
        reference = 0.0
        for key in ("ref", "refPrice", "prevClose"):
            reference = _number(stock.get(key))
            if reference > 0:
                break
        floor = _number(stock.get("floor"))
        ceiling = _number(stock.get("ceiling"))
        if price <= 0 or reference <= 0:
            continue
        if (floor > 0 and price < floor) or (ceiling > 0 and price > ceiling):
            continue

        change_raw = stock.get("changePercent")
        if change_raw is None:
            change_raw = stock.get("change_pct")
        if change_raw is None:
            change = (price - reference) / reference * 100
        else:
            change = _number(change_raw)

        name = str(stock.get("sector") or "Khác")
        sector = sectors.setdefault(name, {
            "name": name,
            "changeValue": 0.0,
            "changeSum": 0.0,
            "count": 0,
            "totalVolume": 0,
            "marketCap": 0.0,
            "foreignFlow": 0.0,
            "foreignCount": 0,
            "liveCount": 0,
        })
        market_cap = max(_number(stock.get("marketCap") or stock.get("market_cap")), 0.0)
        foreign_flow = stock.get("foreign_flow")
        sector["changeValue"] += change * market_cap
        sector["changeSum"] += change
        sector["count"] += 1
        sector["totalVolume"] += max(_number(stock.get("volume")), 0.0)
        sector["marketCap"] += market_cap
        if foreign_flow is not None:
            sector["foreignFlow"] += _number(foreign_flow)
            sector["foreignCount"] += 1
        if stock.get("source") == "dnse-ws":
            sector["liveCount"] += 1

    result = []
    for sector in sectors.values():
        weight = sector["marketCap"]
        change = sector["changeValue"] / weight if weight > 0 else sector["changeSum"] / sector["count"]
        item = {
            "name": sector["name"],
            "change": round(change, 2),
            "weight": weight,
            "count": sector["count"],
            "foreign_flow": sector["foreignFlow"] if sector["foreignCount"] else None,
            "totalVolume": sector["totalVolume"],
            "color": "bg-secondary" if change >= 0 else "bg-error",
        }
        if include_live_count:
            item["liveCount"] = sector["liveCount"]
        result.append(item)

    return sorted(result, key=lambda sector: sector["weight"], reverse=True)
