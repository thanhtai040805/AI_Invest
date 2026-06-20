import logging
from datetime import date
from typing import Dict, Any, Optional
from app.domain.rules.market.macro_service import get_latest_macro, get_macro_snapshot
from app.domain.services.regime_service import RegimeService

logger = logging.getLogger(__name__)

class MacroRiskEngine:
    def __init__(self, regime_service: Optional[RegimeService] = None):
        self.regime_service = regime_service or RegimeService()

    def calculate_risk_score(self, target_date: Optional[date] = None) -> Dict[str, Any]:
        """
        Calculate a comprehensive risk score (0-100) based on Macro and Regime data.
        0: Safe, 100: Extreme Danger.
        """
        if target_date:
            macro = get_macro_snapshot(target_date)
            regime = self.regime_service.get_regime_for_date(target_date)
        else:
            macro = get_latest_macro()
            regime = self.regime_service.get_latest_regime()

        score = 50.0 # Base neutral score
        reasons = []

        # 1. Macro Factors (SBV & FX)
        usd_vnd = macro.get("usd_vnd_exchange", 25450)
        if usd_vnd > 25500:
            score += 10
            reasons.append(f"High FX rate: {usd_vnd:,.0f}")
        
        interbank_on = macro.get("interbank_on", 4.0)
        if interbank_on > 5.0:
            score += 15
            reasons.append(f"Tight liquidity: Interbank ON {interbank_on}%")
        
        # 2. Market Regime Factors (Breadth)
        breadth = regime.get("breadth_ma50", 50.0)
        if breadth < 40:
            score += 15
            reasons.append(f"Poor breadth: only {breadth:.1f}% stocks > MA50")
        elif breadth > 80:
            score -= 5 # Euphoria? Maybe just strong trend, but let's be cautious
            reasons.append(f"Strong breadth: {breadth:.1f}%")

        regime_label = regime.get("regime_label", "SIDEWAYS")
        if regime_label == "BEAR":
            score += 20
            reasons.append("Regime: BEAR market")
        elif regime_label == "BULL":
            score -= 10
            reasons.append("Regime: BULL market")

        # Cap score at 0-100
        score = max(0.0, min(100.0, score))
        
        # Calculate risk_multiplier (1.0 = full size, 0.0 = cash only)
        # Simple linear mapping: 0-40 (1.0), 40-80 (linear reduction), 80-100 (0.1)
        if score <= 40:
            multiplier = 1.0
        elif score >= 90:
            multiplier = 0.1
        else:
            multiplier = 1.0 - (score - 40) / (90 - 40) * 0.9

        return {
            "risk_score": round(score, 1),
            "risk_multiplier": round(multiplier, 2),
            "reasons": reasons,
            "regime": regime_label
        }
