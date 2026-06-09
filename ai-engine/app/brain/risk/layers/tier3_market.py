import logging

logger = logging.getLogger(__name__)


def compute_market_structure_risk(
    symbols: list[str],
    tech_data: dict[str, dict],
    symbol_news: dict[str, list[dict]],
    sector_map: dict[str, str],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}
        sector = sector_map.get(sym, "OTHERS")

        tech = tech_data.get(sym, {})

        # 1. Price limit proximity (near floor)
        mom_1d = _val(tech, ["momentum_1d"])
        if mom_1d is not None:
            pct = float(mom_1d)
            detail["momentum_1d"] = round(pct, 2)
            if pct <= -0.069:
                risk_score += 0.35
                flags.append("PRICE_LIMIT_HIT")
            elif pct <= -0.05:
                risk_score += 0.20
                flags.append("NEAR_FLOOR")
            elif pct <= -0.03:
                risk_score += 0.10

        # 2. Volume anomaly
        vol_ratio = _val(tech, ["volume_ratio"])
        if vol_ratio is not None:
            vr = float(vol_ratio)
            detail["volume_ratio"] = round(vr, 2)
            if vr > 5.0:
                risk_score += 0.25
                flags.append("VOLUME_SPIKE_EXTREME")
            elif vr > 3.0:
                risk_score += 0.15
                flags.append("VOLUME_SPIKE_HIGH")

        # 3. Margin cascade proxy: price drop > 6% + volume 3x
        if mom_1d is not None and vol_ratio is not None:
            if float(mom_1d) < -0.06 and float(vol_ratio) > 3.0:
                risk_score += 0.30
                flags.append("MARGIN_CASCADE_PROXY")
                detail["margin_cascade"] = True

        # 4. Floor trap: 2+ consecutive days near floor
        floor_streak = 0
        for i in range(1, 6):
            key = f"momentum_1d_t{i}"
            m = _val(tech, [key])
            if m is not None and float(m) <= -0.069:
                floor_streak += 1
            else:
                break
        detail["floor_streak"] = floor_streak
        if floor_streak >= 2:
            risk_score += 0.20
            flags.append("FLOOR_TRAP")

        # 5. CafeF news proxy (cầm cố, giải chấp)
        news = symbol_news.get(sym, [])
        pledge_keywords = {"cầm cố", "giải chấp", "call margin", "bán giải chấp"}
        pledge_mentions = [
            n for n in news
            if any(kw in (n.get("matched_keyword", "") or "").lower() for kw in pledge_keywords)
        ]
        if pledge_mentions:
            detail["pledge_news_count"] = len(pledge_mentions)
            detail["pledge_news_titles"] = [n["title"] for n in pledge_mentions[:3]]
            risk_score += 0.25
            flags.append("PLEDGE_NEWS_PROXY")

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results


def _val(data: dict, keys: list[str]):
    for k in keys:
        v = data.get(k)
        if v is not None:
            return v
    return None
