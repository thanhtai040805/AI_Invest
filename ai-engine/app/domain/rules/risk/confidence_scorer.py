"""Confidence Scorer — Risk Gate & Confidence scoring for multi-factor portfolios."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

HARD_FLAGS: List[str] = [
    "GIL_CATASTROPHIC",
    "AUDIT_DENIAL",
    "AUDIT_ADVERSE",
    "SPECIAL_CONTROL",
    "SUSPENDED_TRADING",
    "INSOLVENCY_RISK",
    "CRITICAL_FRAUD",
    "HARD_BLOCK",
]


class ConfidenceScorer:
    """Evaluates factor percentile and market indicators to assign confidence multiplier (0.0 - 1.0)
    and conviction decision.
    """

    def score(
        self,
        factor_percentile: float = 50.0,
        technical_aligned: bool = False,
        foreign_flow_net: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Scorer based on factor strength, technical alignment, and institutional flow."""
        if factor_percentile >= 75:
            confidence = 0.95
            decision = "BUY"
            rating = "HIGH_FACTOR"
        elif factor_percentile >= 50:
            confidence = 0.80
            decision = "WATCH"
            rating = "MODERATE_FACTOR"
        else:
            confidence = 0.50
            decision = "WATCH"
            rating = "LOW_FACTOR"

        if technical_aligned:
            confidence = min(1.0, confidence * 1.05)

        if foreign_flow_net is not None and foreign_flow_net < -10_000_000_000:  # Sell > 10B VND
            confidence *= 0.85

        return {
            "confidence": round(confidence, 2),
            "decision": decision,
            "rating": rating,
            "hard_flags": [],
            "soft_flags": [],
            "rationale": f"Base factor percentile {factor_percentile:.1f} score.",
        }

    def score_crs(
        self,
        crs_result: Optional[Dict[str, Any]] = None,
        factor_percentile: float = 50.0,
        technical_aligned: bool = False,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Backward-compatible delegate to score()."""
        return self.score(factor_percentile=factor_percentile, technical_aligned=technical_aligned)
