"""Conservative paper fill from recent displayed order-book depth."""

from datetime import datetime, timezone


def shadow_fill(book: dict, side: str, shares: int, limit_price: float, now: datetime | None = None) -> float:
    if not isinstance(book, dict) or book.get("marketState") not in ("continuous_morning", "continuous_afternoon"):
        raise ValueError("Shadow fills require a continuous trading session")
    timestamp = book.get("lastUpdate") if isinstance(book, dict) else None
    try:
        updated = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        raise ValueError("Live order book timestamp is unavailable") from None
    if updated.tzinfo is None:
        updated = updated.astimezone()
    now = now or datetime.now(timezone.utc)
    age = (now - updated).total_seconds()
    if not 0 <= age <= 10:
        raise ValueError("Live order book is stale")
    if side not in ("BUY", "SELL") or shares <= 0 or limit_price <= 0:
        raise ValueError("Invalid Shadow order")

    levels = book.get("asks" if side == "BUY" else "bids") or []
    if not isinstance(levels, list) or not levels:
        raise ValueError("Live order book has no executable depth")
    prices = []
    for level in levels:
        try:
            raw = float(level["price"])
            volume = int(level["volume"])
        except (KeyError, TypeError, ValueError):
            continue
        price = round(raw * 1000) if 0 < raw < 500 else raw
        if price > 0 and volume > 0:
            prices.append((price, volume))
    prices.sort(reverse=side == "SELL")
    remaining = shares
    notional = 0.0
    for price, volume in prices:
        if side == "BUY" and price > limit_price or side == "SELL" and price < limit_price:
            break
        filled = min(remaining, volume)
        notional += price * filled
        remaining -= filled
        if remaining == 0:
            return notional / shares
    raise ValueError("Insufficient executable depth at the requested price")
