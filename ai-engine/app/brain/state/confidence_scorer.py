"""ConfidenceScorer — Decision Gate for VN equity signals.

Combines factor composite scores, risk flags, and technical confirmation
into a single confidence score (0.0–1.0). Hard risk flags force confidence = 0.
"""
import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

HARD_FLAGS = {
    "CANH_BAO_TC", "CHAM_BAO_TC",
    "DEBT_DANGER", "DEBT_DANGER_FIN",
    "CAR_DANGER",
}

SOFT_FLAGS = {
    "M_SCORE_FLAG", "F_SCORE_FLAG",
    "FLOOR_TRAP", "SHARP_DROP",
    "KHOI_LUONG_BAT_THUONG",
    "FOREIGN_FLOW_ANOMALY",
    "INSIDER_SELLING_ANOMALY",
    "GOVERNANCE_SHOCK",
}


class ConfidenceScorer:
    """Decision Gate: factor composite + risk flags → confidence score.

    VN-specific scoring pipeline:
      1. Hard risk flags → confidence = 0 (DO_NOT_TRADE)
      2. Factor composite → base score (0.0–1.0)
      3. Soft risk flags → multiply by 0.5
      4. Technical confirmation → bonus +0.1 if aligned
    """

    def __init__(self):
        logger.info("ConfidenceScorer initialized")

    def score(
        self,
        factor_percentile: float,
        active_flags: List[str],
        technical_aligned: bool = False,
        foreign_flow_net: Optional[float] = None,
        insider_trade_net: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Compute confidence score for one symbol.

        Args:
            factor_percentile: Factor composite percentile (0–100).
            active_flags: List of active risk flag names.
            technical_aligned: Whether technical indicators confirm the signal.
            foreign_flow_net: Net foreign value (VND bn) over last 5 days.
            insider_trade_net: Net insider shares over last 30 days.

        Returns:
            Dict with: confidence, decision, rating, rationale.
        """
        active_hard = {f for f in active_flags if f in HARD_FLAGS}
        active_soft = {f for f in active_flags if f in SOFT_FLAGS}

        # ── Hard block ──
        if active_hard:
            return {
                "confidence": 0.0,
                "decision": "DO_NOT_TRADE",
                "rating": "Sell",
                "rationale": f"Hard risk flags active: {', '.join(sorted(active_hard))}",
                "hard_flags": sorted(active_hard),
                "soft_flags": sorted(active_soft),
            }

        # ── Base factor score ──
        # percentile 0–100 → score 0.0–1.0, centered at 0.5
        base = (factor_percentile / 100.0 - 0.5) * 2  # -1.0 to +1.0
        score = 0.5 + base * 0.4  # range 0.1–0.9

        # ── Soft flag penalty ──
        if active_soft:
            penalty = 0.5 ** len(active_soft)
            score *= penalty

        # ── Technical confirmation bonus ──
        if technical_aligned:
            if base > 0:
                score = min(score + 0.1, 1.0)
            elif base < 0:
                score = max(score - 0.1, 0.0)

        # ── Foreign flow boost (if aligned) ──
        if foreign_flow_net is not None:
            if (base > 0 and foreign_flow_net > 50) or (base < 0 and foreign_flow_net < -50):
                score = min(score + 0.05, 1.0)

        # ── Insider trade boost (if aligned) ──
        if insider_trade_net is not None:
            if base > 0 and insider_trade_net > 0:
                score = min(score + 0.05, 1.0)
            elif base < 0 and insider_trade_net < 0:
                score = min(score + 0.05, 1.0)
            elif base > 0 and insider_trade_net < -100000:
                score = max(score - 0.1, 0.0)

        # ── Decision mapping ──
        if score >= 0.65:
            decision = "BUY"
            rating = "Buy" if score >= 0.8 else "Overweight"
        elif score <= 0.35:
            decision = "SELL"
            rating = "Sell" if score <= 0.2 else "Underweight"
        else:
            decision = "HOLD"
            rating = "Hold"

        return {
            "confidence": round(score, 4),
            "decision": decision,
            "rating": rating,
            "rationale": self._build_rationale(score, base, active_soft, technical_aligned),
            "hard_flags": [],
            "soft_flags": sorted(active_soft),
        }

    def score_batch(
        self,
        symbols: List[str],
        factor_percentiles: Dict[str, float],
        risk_flags: Dict[str, List[str]],
        technical_alignments: Optional[Dict[str, bool]] = None,
        foreign_flows: Optional[Dict[str, float]] = None,
        insider_trades: Optional[Dict[str, float]] = None,
    ) -> Dict[str, Dict[str, Any]]:
        """Score multiple symbols at once."""
        results = {}
        for sym in symbols:
            results[sym] = self.score(
                factor_percentile=factor_percentiles.get(sym, 50),
                active_flags=risk_flags.get(sym, []),
                technical_aligned=technical_alignments.get(sym, False) if technical_alignments else False,
                foreign_flow_net=foreign_flows.get(sym) if foreign_flows else None,
                insider_trade_net=insider_trades.get(sym) if insider_trades else None,
            )
        return results

    def score_crs(
        self,
        crs_result: dict,
        factor_percentile: float,
        technical_aligned: bool = False,
    ) -> dict:
        """Score using CRS result from risk_assessments.

        CRS-based sizing:
          - DO_NOT_TRADE       → 0.0
          - REQUIRE_HUMAN_REVIEW → 0.0 (flagged for review)
          - REDUCE_SIZE_50PCT  → 0.5
          - REDUCE_SIZE_25PCT  → 0.75
          - NORMAL_SIZING      → 1.0
        """
        crs_score = crs_result.get("crs_score", 0.0)
        recommendation = crs_result.get("recommendation", "NORMAL_SIZING")
        hard_blocked = crs_result.get("hard_blocked", False)
        hard_flags = crs_result.get("hard_flags", [])
        soft_flags = crs_result.get("soft_flags", [])

        if hard_blocked:
            return {
                "confidence": 0.0,
                "decision": "DO_NOT_TRADE",
                "rating": "Sell",
                "rationale": f"Hard block by CRS: {', '.join(hard_flags)}",
                "hard_flags": hard_flags,
                "soft_flags": soft_flags,
            }

        base = (factor_percentile / 100.0 - 0.5) * 2
        score = 0.5 + base * 0.4

        rec_multiplier = {
            "DO_NOT_TRADE": 0.0,
            "REQUIRE_HUMAN_REVIEW": 0.0,
            "REDUCE_SIZE_50PCT": 0.5,
            "REDUCE_SIZE_25PCT": 0.75,
            "NORMAL_SIZING": 1.0,
        }
        score *= rec_multiplier.get(recommendation, 1.0)

        if technical_aligned and base > 0:
            score = min(score + 0.1, 1.0)
        elif technical_aligned and base < 0:
            score = max(score - 0.1, 0.0)

        if score >= 0.65:
            decision = "BUY"
            rating = "Buy" if score >= 0.8 else "Overweight"
        elif score <= 0.35:
            decision = "SELL"
            rating = "Sell" if score <= 0.2 else "Underweight"
        else:
            decision = "HOLD"
            rating = "Hold"

        return {
            "confidence": round(score, 4),
            "decision": decision,
            "rating": rating,
            "rationale": self._build_crs_rationale(score, base, recommendation, crs_score, technical_aligned),
            "hard_flags": hard_flags,
            "soft_flags": soft_flags,
        }

    @staticmethod
    def _build_rationale(score: float, base: float, soft_flags: set, tech_ok: bool) -> str:
        parts = [f"Factor signal: {'bullish' if base > 0 else 'bearish'} ({base:+.2f}z)"]
        if soft_flags:
            parts.append(f"Soft flags active: {', '.join(sorted(soft_flags))} (size reduced)")
        if tech_ok:
            parts.append("Technical confirmation: aligned")
        parts.append(f"Confidence: {score:.2f}")
        return " | ".join(parts)

    @staticmethod
    def _build_crs_rationale(score: float, base: float, rec: str, crs: float, tech_ok: bool) -> str:
        parts = [f"Factor: {'bullish' if base > 0 else 'bearish'} ({base:+.2f}z)"]
        parts.append(f"CRS: {crs:.3f} → {rec}")
        if tech_ok:
            parts.append("Tech: aligned")
        parts.append(f"Confidence: {score:.2f}")
        return " | ".join(parts)
