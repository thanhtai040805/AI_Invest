import logging
from datetime import date

logger = logging.getLogger(__name__)

TET_DATES: dict[int, tuple] = {
    2024: (date(2024, 2, 10), date(2024, 2, 16)),
    2025: (date(2025, 1, 29), date(2025, 2, 2)),
    2026: (date(2026, 2, 17), date(2026, 2, 23)),
    2027: (date(2027, 2, 6), date(2027, 2, 12)),
}


def compute_behavioral_risk(
    symbols: list[str],
    tech_data: dict[str, dict],
    news_events: dict[str, list[dict]],
    calc_date: date,
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}

        tech = tech_data.get(sym, {})

        # 1. FOMO detection: price up > 15% in 5d + volume 3x
        ret_5d = _val(tech, ["return_5d", "momentum_5d"])
        vol_ratio = _val(tech, ["volume_ratio"])
        if ret_5d is not None and vol_ratio is not None:
            r5 = float(ret_5d)
            vr = float(vol_ratio)
            detail["return_5d"] = round(r5, 3)
            detail["volume_ratio"] = round(vr, 2)
            if r5 > 0.15 and vr > 3.0:
                risk_score += 0.40
                flags.append("FOMO_PATTERN")
                detail["fomo"] = True
            elif r5 > 0.10 and vr > 2.5:
                risk_score += 0.20

        # 2. FUD detection: price down > 10% + negative news sentiment
        news = news_events.get(sym, [])
        avg_sentiment = 0.0
        if news:
            sentiments = [
                float(n.get("sentiment", 0)) for n in news
                if n.get("sentiment") is not None
            ]
            avg_sentiment = sum(sentiments) / len(sentiments) if sentiments else 0.0

        detail["avg_news_sentiment"] = round(avg_sentiment, 3)
        if ret_5d is not None and float(ret_5d) < -0.10 and avg_sentiment < -0.3:
            risk_score += 0.30
            flags.append("FUD_PATTERN")
            detail["fud"] = True

        # 3. Pump/dump volume + price pattern
        if ret_5d is not None and vol_ratio is not None:
            r5 = float(ret_5d)
            vr = float(vol_ratio)
            if r5 > 0.08 and vr > 4.0:
                risk_score += 0.25
                flags.append("PUMP_PATTERN")
                detail["pump"] = True
            elif r5 < -0.08 and vr > 4.0:
                risk_score += 0.25
                flags.append("DUMP_PATTERN")
                detail["dump"] = True

        # 4. Tết effect
        if calc_date:
            year = calc_date.year
            tet_range = TET_DATES.get(year)
            if tet_range:
                tet_start, tet_end = tet_range
                days_to_tet = (tet_start - calc_date).days
                detail["days_to_tet"] = days_to_tet
                if 0 <= days_to_tet <= 14:
                    risk_score += 0.15
                    flags.append("TET_APPROACHING")
                elif -7 <= days_to_tet < 0:
                    risk_score += 0.10

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results


def _val(data: dict, keys: list[str]):
    for k in keys:
        v = data.get(k)
        if v is not None:
            return v
    return None
