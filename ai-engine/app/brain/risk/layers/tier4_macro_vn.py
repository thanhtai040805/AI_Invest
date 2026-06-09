import logging

logger = logging.getLogger(__name__)

SECTOR_RATE_SENSITIVITY = {
    "BANKS": 0.8, "FINANCIAL_SERVICES": 0.3, "REAL_ESTATE": 0.9,
    "CONSTRUCTION": 0.7, "UTILITIES": 0.7, "CONSUMER": 0.5,
    "EXPORT": 0.2,
}

SECTOR_FX_SENSITIVITY = {
    "BASIC_RESOURCES": 0.7, "RETAIL_TRADE": 0.8, "BANKS": 0.4,
    "REAL_ESTATE": 0.5, "CONSTRUCTION": 0.3, "FOOD_BEVERAGE": 0.3,
    "EXPORT": 0.6,
}

SECTOR_CREDIT_SENSITIVITY = {"REAL_ESTATE": 1.3, "BANKS": 1.3, "CONSTRUCTION": 1.3}

MAJOR_GROUP_MAP = {
    "BANKS": "BANKS", "FINANCIAL_SERVICES": "FIN_SERVICES",
    "REAL_ESTATE": "REAL_ESTATE", "CONSTRUCTION": "CONSTRUCTION",
    "UTILITIES": "UTILITIES", "OTHERS": "OTHERS",
}


def compute_macro_vn_risk(
    symbols: list[str],
    sector_map: dict[str, str],
    macro_data: dict[str, float],
) -> dict[str, dict]:
    results: dict[str, dict] = {}

    rate_trend = _trend(macro_data, "rate_refinancing", ["rate_discount", "rate_lending"])
    vnd_trend = _trend(macro_data, "usd_vnd", [])
    credit_growth = macro_data.get("credit_growth_yoy", 0.12)
    npl_system = macro_data.get("system_npl_ratio", 0.02)

    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}
        sector = sector_map.get(sym, "OTHERS")

        detail["rate_trend"] = rate_trend
        detail["vnd_trend"] = vnd_trend
        detail["credit_growth"] = round(credit_growth, 3)

        # 1. Rate sensitivity
        rate_mag = SECTOR_RATE_SENSITIVITY.get(sector, 0.3)
        if rate_trend == "RISING" and sector in ("REAL_ESTATE", "UTILITIES"):
            risk_score += 0.6 * rate_mag
            flags.append("RATE_RISING")
        elif rate_trend == "RISING" and sector == "BANKS":
            risk_score += 0.3 * rate_mag
        elif rate_trend == "RISING":
            risk_score += 0.15 * rate_mag

        # 2. FX risk
        fx_mag = SECTOR_FX_SENSITIVITY.get(sector, 0.1)
        if vnd_trend == "WEAKENING":
            risk_score += 0.5 * fx_mag
            flags.append("VND_WEAKENING")
        elif vnd_trend == "STRENGTHENING":
            risk_score += 0.1 * fx_mag

        # 3. Credit cycle risk
        credit_mult = SECTOR_CREDIT_SENSITIVITY.get(sector, 1.0)
        if credit_growth > 0.20:
            risk_score += 0.30 * credit_mult
            flags.append("CREDIT_OVERHEAT")
        elif credit_growth > 0.15:
            risk_score += 0.15 * credit_mult
        if npl_system > 0.04:
            risk_score += 0.35 * credit_mult
            flags.append("SYSTEM_NPL_HIGH")

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results


def _trend(data: dict, key: str, fallback_keys: list[str]) -> str:
    v = data.get(key)
    if v is not None:
        return str(v)
    for fk in fallback_keys:
        v = data.get(fk)
        if v is not None:
            return str(v)
    return "STABLE"
