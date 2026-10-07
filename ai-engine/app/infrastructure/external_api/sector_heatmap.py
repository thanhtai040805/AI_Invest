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
        name = str(stock.get("sector") or "Khác")
        sector = sectors.setdefault(name, {
            "changeValue": 0.0, "changeSum": 0.0, "changeCount": 0,
            "count": 0, "totalVolume": 0, "marketCap": 0.0, "marketCapCount": 0,
            "changeMarketCap": 0.0, "foreignFlow": 0.0, "foreignCount": 0, "liveCount": 0,
        })
        market_cap = max(_number(stock.get("marketCap") or stock.get("market_cap")), 0.0)
        sector["count"] += 1
        sector["marketCap"] += market_cap
        sector["marketCapCount"] += int(market_cap > 0)
        sector["totalVolume"] += max(_number(stock.get("volume")), 0.0)
        foreign_flow = stock.get("foreign_flow")
        if foreign_flow is not None:
            sector["foreignFlow"] += _number(foreign_flow)
            sector["foreignCount"] += 1
        if stock.get("source") == "dnse-ws":
            sector["liveCount"] += 1

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

        sector["changeValue"] += change * market_cap
        sector["changeSum"] += change
        sector["changeCount"] += 1
        sector["changeMarketCap"] += market_cap

    result = []
    for name, sector in sectors.items():
        weight = sector["marketCap"]
        change = sector["changeValue"] / sector["changeMarketCap"] if sector["changeMarketCap"] > 0 else (
            sector["changeSum"] / sector["changeCount"] if sector["changeCount"] else None
        )
        item = {
            "name": name,
            "change": round(change, 2) if change is not None else None,
            "weight": weight,
            "count": sector["count"],
            "marketCapCount": sector["marketCapCount"],
            "foreign_flow": sector["foreignFlow"] if sector["foreignCount"] else None,
            "totalVolume": sector["totalVolume"],
            "color": "bg-secondary" if change is None or change >= 0 else "bg-error",
        }
        if include_live_count:
            item["liveCount"] = sector["liveCount"]
        result.append(item)

    complete_caps = all(sector["marketCapCount"] == sector["count"] for sector in result)
    return sorted(result, key=lambda sector: sector["weight"] if complete_caps else sector["count"], reverse=True)
