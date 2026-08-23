"""
VN30 Index Distortion Monitor — HOSE
Detects index distortion when 1-3 mega-cap stocks (e.g. VIC, VHM, VCB) drive >70% of total VN30 Index return,
preventing false breadth and regime signals downstream.
"""

import logging
from typing import Dict, Any, List

logger = logging.getLogger(__name__)


class VN30DistortionMonitor:
    """
    Decomposes VN30 Index movement into individual stock contributions
    and flags index concentration distortion.
    """

    def analyze_distortion(
        self,
        stock_returns: Dict[str, float],
        stock_weights: Dict[str, float]
    ) -> Dict[str, Any]:
        """
        Calculates constituent contribution ratios to total VN30 return.

        stock_returns: Dict[ticker, percentage_return]
        stock_weights: Dict[ticker, weight_fraction] (e.g., VIC: 0.11)
        """
        if not stock_returns or not stock_weights:
            return {
                "is_distorted": False,
                "concentration_ratio": 0.0,
                "top_contributors": [],
                "reason": "NO_CONSTITUENT_DATA"
            }

        contributions = {}
        for ticker, ret in stock_returns.items():
            weight = stock_weights.get(ticker, 0.0)
            contributions[ticker] = ret * weight

        total_vn30_return = sum(contributions.values())
        abs_contributions = {t: abs(c) for t, c in contributions.items()}
        sum_abs_contributions = sum(abs_contributions.values())

        if sum_abs_contributions <= 1e-8:
            return {
                "is_distorted": False,
                "concentration_ratio": 0.0,
                "vn30_return": round(total_vn30_return, 4),
                "top_contributors": [],
                "reason": "NEGLIGIBLE_VN30_MOVEMENT"
            }

        # Sort top contributors by absolute contribution
        sorted_contribs = sorted(abs_contributions.items(), key=lambda x: x[1], reverse=True)
        top3 = sorted_contribs[:3]
        top3_abs_sum = sum(val for _, val in top3)

        concentration_ratio = top3_abs_sum / sum_abs_contributions

        # Top 3 stocks drive > 70% of absolute movement -> Distorted Index
        is_distorted = concentration_ratio > 0.70

        top3_details = [
            {
                "ticker": t,
                "return": round(stock_returns[t], 4),
                "weight": round(stock_weights.get(t, 0.0), 4),
                "contribution_pct": round(val / sum_abs_contributions, 4)
            }
            for t, val in top3
        ]

        if is_distorted:
            reason = (
                f"INDEX_DISTORTION_DETECTED: Top 3 stocks ({', '.join([t for t, _ in top3])}) "
                f"account for {concentration_ratio:.1%} of VN30 index movement."
            )
        else:
            reason = "NORMAL_INDEX_DISTRIBUTION"

        return {
            "is_distorted": is_distorted,
            "concentration_ratio": round(concentration_ratio, 4),
            "vn30_return": round(total_vn30_return, 4),
            "top_contributors": top3_details,
            "reason": reason
        }


vn30_distortion_monitor = VN30DistortionMonitor()
