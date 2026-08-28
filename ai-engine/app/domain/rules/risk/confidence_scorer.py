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
    """Evaluates risk assessment (CRS) and factor percentile to assign confidence multiplier (0.0 - 1.0)

    and conviction decision.
    """

    def score_crs(
        self,
        crs_result: Optional[Dict[str, Any]],
        factor_percentile: float = 50.0,
        technical_aligned: bool = False,
    ) -> Dict[str, Any]:
        """Score with 7-layer Comprehensive Risk Scoring (CRS) output."""
        if not crs_result:
            return self.score(factor_percentile=factor_percentile, technical_aligned=technical_aligned)

        hard_blocked = crs_result.get("hard_blocked", False)
        hard_flags = crs_result.get("hard_flags", []) or []
        soft_flags = crs_result.get("soft_flags", []) or []
        crs_score = crs_result.get("crs_score", 0.0)

        # 1. Hard Block (Zero tolerance)
        if hard_blocked or any(hf in HARD_FLAGS for hf in hard_flags):
            return {
                "confidence": 0.0,
                "decision": "REJECT",
                "rating": "HARD_BLOCKED",
                "hard_flags": hard_flags or ["HARD_BLOCK"],
                "soft_flags": soft_flags,
                "rationale": f"Blocked by Hard Risk Law. Active hard flags: {hard_flags}",
            }

        # 2. CRS Score & Soft Flags Penalty
        soft_count = len(soft_flags)
        if soft_count >= 4 or crs_score >= 80:
            confidence = 0.4
            decision = "WATCH"
            rating = "HIGH_RISK"
            rationale = f"High risk profile ({soft_count} soft flags, CRS: {crs_score:.1f})"
        elif soft_count >= 2 or crs_score >= 60:
            confidence = 0.7
            decision = "WATCH" if factor_percentile < 80 else "BUY"
            rating = "MODERATE_RISK"
            rationale = f"Moderate risk ({soft_count} soft flags, CRS: {crs_score:.1f})"
        else:
            confidence = 1.0
            decision = "BUY" if factor_percentile >= 60 else "WATCH"
            rating = "PASS"
            rationale = "Risk gate passed with clean profile."

        # Technical alignment boost
        if technical_aligned and confidence > 0.0:
            confidence = min(1.0, confidence * 1.1)

        return {
            "confidence": round(confidence, 2),
            "decision": decision,
            "rating": rating,
            "hard_flags": hard_flags,
            "soft_flags": soft_flags,
            "rationale": rationale,
        }

    def score(
        self,
        factor_percentile: float = 50.0,
        technical_aligned: bool = False,
        foreign_flow_net: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Fallback scorer when CRS data is not available."""
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
