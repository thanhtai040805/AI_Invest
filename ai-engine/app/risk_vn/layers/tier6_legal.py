import logging

logger = logging.getLogger(__name__)

SECTOR_REG_RISK = {
    "REAL_ESTATE": 0.40, "BANKS": 0.30, "FINANCIAL_SERVICES": 0.15,
    "PHARMA": 0.35, "EDUCATION": 0.40, "CONSTRUCTION": 0.20,
    "OIL_GAS": 0.25, "BASIC_RESOURCES": 0.20, "OTHERS": 0.10,
}

LEGAL_FLAG_MAP = {
    "bị khởi tố": "UNDER_INVESTIGATION",
    "tạm giam": "UNDER_INVESTIGATION",
    "hủy niêm yết": "CRITICAL_REGULATORY_ACTION",
    "đình chỉ giao dịch": "CRITICAL_REGULATORY_ACTION",
    "truy thu thuế": "TAX_DISPUTE",
    "thao túng thị trường": "UNDER_INVESTIGATION",
    "vi phạm công bố thông tin": "DISCLOSURE_VIOLATION",
    "thanh tra ủy ban": "REGULATORY_PROBE",
    "xử phạt": "REGULATORY_FINE",
}


def compute_regulatory_risk(
    symbols: list[str],
    sector_map: dict[str, str],
    symbol_news: dict[str, list[dict]],
    news_events: dict[str, list[dict]],
) -> dict[str, dict]:
    results: dict[str, dict] = {}
    for sym in symbols:
        flags: list[str] = []
        risk_score = 0.0
        detail = {}
        sector = sector_map.get(sym, "OTHERS")

        # 1. Sector static regulatory risk
        sector_base = SECTOR_REG_RISK.get(sector, SECTOR_REG_RISK["OTHERS"])
        risk_score += sector_base * 0.3
        detail["sector_base_risk"] = sector_base

        # 2. CafeF keyword tagger (legal)
        news = symbol_news.get(sym, [])
        legal_matches = set()
        for n in news:
            kw = (n.get("matched_keyword", "") or "").lower()
            title = (n.get("title", "") or "").lower()
            for search_kw, flag in LEGAL_FLAG_MAP.items():
                if search_kw in kw or search_kw in title:
                    legal_matches.add(flag)

        if legal_matches:
            detail["legal_flags"] = sorted(legal_matches)
            for lf in legal_matches:
                flags.append(lf)
                if lf == "CRITICAL_REGULATORY_ACTION":
                    risk_score += 0.50
                elif lf == "UNDER_INVESTIGATION":
                    risk_score += 0.35
                elif lf == "TAX_DISPUTE":
                    risk_score += 0.25
                elif lf == "REGULATORY_FINE":
                    risk_score += 0.15
                else:
                    risk_score += 0.10

        # 3. Governance shock from news_events
        gov_events = news_events.get(sym, [])
        gov_count = len([
            e for e in gov_events
            if any(kw in (e.get("title", "") or "") for kw in
                   ["từ nhiệm", "miễn nhiệm", "thay ceo", "thay chủ tịch"])
        ])
        detail["governance_shock_count"] = gov_count
        if gov_count >= 3:
            risk_score += 0.25
            flags.append("GOVERNANCE_SHOCK")
        elif gov_count >= 1:
            risk_score += 0.10

        risk_score = min(risk_score, 1.0)
        results[sym] = {"risk_score": round(risk_score, 3), "flags": flags, "detail": detail}
    return results
