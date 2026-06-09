import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

LAYER_KEYS = [
    "layer1_quant", "layer2_fundamental", "layer3_market_vn",
    "layer4_macro_vn", "layer5_global", "layer6_regulatory", "layer7_behavioral",
]

DEFAULT_WEIGHTS = {
    "layer1_quant": 0.20,
    "layer2_fundamental": 0.20,
    "layer3_market_vn": 0.20,
    "layer4_macro_vn": 0.15,
    "layer5_global": 0.10,
    "layer6_regulatory": 0.10,
    "layer7_behavioral": 0.05,
}

SECTOR_OVERRIDES = {
    "BANKS": {"layer2_fundamental": 0.25, "layer6_regulatory": 0.15},
    "FINANCIAL_SERVICES": {"layer2_fundamental": 0.25},
    "REAL_ESTATE": {"layer6_regulatory": 0.20, "layer3_market_vn": 0.25},
    "CONSTRUCTION": {"layer6_regulatory": 0.15, "layer3_market_vn": 0.22},
    "BASIC_RESOURCES": {"layer5_global": 0.18, "layer4_macro_vn": 0.12},
    "EXPORT": {"layer5_global": 0.18, "layer4_macro_vn": 0.12},
}

HARD_BLOCK_FLAGS = {
    "CRITICAL_REGULATORY_ACTION",
    "UNDER_INVESTIGATION",
    "ADVERSE_AUDIT_OPINION",
    "DELIST_CONFIRMED",
    "TRADING_SUSPENDED",
}

SOFT_BLOCK_FLAGS = {
    "QUALIFIED_AUDIT_OPINION",
    "EXTREME_PLEDGE_RATIO",
    "PUMP_PATTERN_DETECTED",
    "NEAR_MARGIN_CALL",
    "FLOOR_TRAP",
}


class VNCompositeRiskScorer:
    def __init__(self):
        logger.info("VNCompositeRiskScorer initialized")

    def compute(
        self,
        symbol: str,
        sector: str,
        layer_scores: dict[str, dict],
    ) -> dict[str, Any]:
        weights = self._get_weights(sector)

        weighted_sum = 0.0
        total_w = 0.0
        flat_scores: dict[str, float] = {}
        all_flags: list[str] = []

        for lk in LAYER_KEYS:
            ls = layer_scores.get(lk, {})
            score = ls.get("risk_score", 0.0)
            flags = ls.get("flags", [])
            w = weights.get(lk, 0.1)

            flat_scores[lk] = score
            weighted_sum += w * score
            total_w += w
            all_flags.extend(flags)

        crs = weighted_sum / total_w if total_w > 0 else 0.0
        crs = min(max(crs, 0.0), 1.0)

        hard_flags = [f for f in all_flags if f in HARD_BLOCK_FLAGS]
        soft_flags = [f for f in all_flags if f in SOFT_BLOCK_FLAGS]
        hard_blocked = len(hard_flags) > 0

        recommendation = self._recommendation(crs, hard_blocked, soft_flags)

        return {
            "symbol": symbol,
            "sector": sector,
            "crs_score": round(crs, 4),
            "risk_level": self._risk_level(crs, hard_blocked),
            "hard_blocked": hard_blocked,
            "soft_blocked": len(soft_flags) > 0,
            "hard_flags": hard_flags,
            "soft_flags": soft_flags,
            "all_flags": all_flags,
            "layer_scores": {k: round(v, 4) for k, v in flat_scores.items()},
            "recommendation": recommendation,
        }

    def _get_weights(self, sector: str) -> dict[str, float]:
        w = dict(DEFAULT_WEIGHTS)
        overrides = SECTOR_OVERRIDES.get(sector, {})
        for k, v in overrides.items():
            if k in w:
                w[k] = v
        total = sum(w.values())
        if total > 0:
            w = {k: v / total for k, v in w.items()}
        return w

    @staticmethod
    def _recommendation(crs: float, hard_blocked: bool, soft_flags: list) -> str:
        if hard_blocked:
            return "DO_NOT_TRADE"
        if soft_flags:
            return "REQUIRE_HUMAN_REVIEW"
        if crs > 0.80:
            return "DO_NOT_TRADE"
        if crs > 0.55:
            return "REQUIRE_HUMAN_REVIEW"
        if crs > 0.40:
            return "REDUCE_SIZE_50PCT"
        if crs > 0.25:
            return "REDUCE_SIZE_25PCT"
        return "NORMAL_SIZING"

    @staticmethod
    def _risk_level(crs: float, hard_blocked: bool) -> str:
        if hard_blocked:
            return "BLOCKED"
        if crs > 0.55:
            return "HIGH"
        if crs > 0.40:
            return "MEDIUM_HIGH"
        if crs > 0.25:
            return "MEDIUM"
        if crs > 0.15:
            return "LOW"
        return "VERY_LOW"
