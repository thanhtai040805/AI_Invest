import logging

logger = logging.getLogger(__name__)

GLOBAL_SECTOR_MAP = {
    "EXPORT": 0.8, "BASIC_RESOURCES": 0.7, "BANKS": 0.3,
    "TECHNOLOGY": 0.4, "OIL_GAS": 0.6, "FOOD_BEVERAGE": 0.2,
}


def compute_global_risk(
    symbols: list[str],
    sector_map: dict[str, str],
    macro_data: dict[str, float],
) -> dict[str, dict]:
    results: dict[str, dict] = {}

    vix = macro_data.get("vix", 15)
    dxy = macro_data.get("usd_index", 104)
    oil = macro_data.get("oil_price", 75)
    china_csi = macro_data.get("china_csi_300_change_1m", 0)

    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}
        sector = sector_map.get(sym, "OTHERS")

        mag = GLOBAL_SECTOR_MAP.get(sector, 0.2)

        # 1. VIX risk-off
        vix_score = max(0, (vix - 15) / 30)
        vix_component = vix_score * 0.6 * mag
        detail["vix"] = vix
        detail["vix_score"] = round(vix_score, 3)
        if vix > 25:
            risk_score += vix_component
            flags.append("VIX_ELEVATED")
        elif vix > 18:
            risk_score += vix_component * 0.5

        # 2. DXY strength
        dxy_score = max(0, (dxy - 100) / 15)
        dxy_component = dxy_score * 0.4 * mag
        detail["dxy"] = dxy
        detail["dxy_score"] = round(dxy_score, 3)
        if dxy > 108:
            risk_score += dxy_component
            flags.append("DXY_STRONG")
        elif dxy > 104:
            risk_score += dxy_component * 0.5

        # 3. Commodity risk
        detail["oil_price"] = oil
        if oil > 90:
            risk_score += 0.10 * mag
            flags.append("OIL_HIGH")
        elif oil < 50:
            risk_score += 0.05 * mag

        # 4. China spillover
        detail["china_csi_1m"] = china_csi
        if china_csi < -0.05:
            risk_score += 0.15 * mag
            flags.append("CHINA_SELLOFF")

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results
