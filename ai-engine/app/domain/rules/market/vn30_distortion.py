"""
VN30 Index Distortion Monitor — HOSE
Detects index distortion when 1-3 mega-cap stocks (e.g. VIC, VHM, VCB) drive >70% of total VN30 Index return,
preventing false breadth and regime signals downstream.
"""

import logging
from typing import Dict, Any, List, Optional

logger = logging.getLogger(__name__)


class VN30DistortionMonitor:
    """
    Decomposes VN30 Index movement into individual stock contributions
    and flags index concentration distortion.
    """

    def analyze_distortion(
        self,
        stock_returns: Dict[str, float],
        stock_weights: Dict[str, float],
        market_adv_decl_ratio: Optional[float] = None,
        vni_return: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Calculates constituent contribution ratios to total VN30 return,
        and detects market-wide index distortion ("Xanh vỏ đỏ lòng").

        stock_returns: Dict[ticker, percentage_return]
        stock_weights: Dict[ticker, weight_fraction] (e.g., VIC: 0.11)
        market_adv_decl_ratio: Advancing / Declining count ratio across HOSE
        vni_return: VN-Index percentage return
        """
        if not stock_returns or not stock_weights:
            return {
                "is_distorted": False,
                "distortion_type": None,
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
                "distortion_type": None,
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

        # Check 1: Top 3 stocks drive > 70% of absolute movement
        is_conc_distorted = concentration_ratio > 0.70

        # Check 2: "Xanh vỏ đỏ lòng" (Green exterior, red core)
        # Index or VN30 is green (e.g. > 0), but market-wide Advance/Decline ratio < 0.45 (>68% red)
        eff_idx_ret = vni_return if vni_return is not None else total_vn30_return
        is_green_red_distorted = False
        if market_adv_decl_ratio is not None and eff_idx_ret > 0.001 and market_adv_decl_ratio < 0.45:
            is_green_red_distorted = True

        is_distorted = is_conc_distorted or is_green_red_distorted

        top3_details = [
            {
                "ticker": t,
                "return": round(stock_returns[t], 4),
                "weight": round(stock_weights.get(t, 0.0), 4),
                "contribution_pct": round(val / sum_abs_contributions, 4)
            }
            for t, val in top3
        ]

        distortion_types = []
        if is_green_red_distorted:
            distortion_types.append("GREEN_EXTERIOR_RED_CORE")
        if is_conc_distorted:
            distortion_types.append("MEGA_CAP_CONCENTRATION")

        distortion_type = "+".join(distortion_types) if distortion_types else None

        if is_green_red_distorted and is_conc_distorted:
            reason = (
                f"INDEX_DISTORTION_DETECTED: Xanh vỏ đỏ lòng cực đoan! Index tăng ({eff_idx_ret:.2%}) "
                f"nhờ top 3 trụ ({', '.join([t for t, _ in top3])}) chiếm {concentration_ratio:.1%} "
                f"nhưng độ rộng toàn sàn chìm trong sắc đỏ (A/D ratio = {market_adv_decl_ratio:.2f})."
            )
        elif is_green_red_distorted:
            reason = (
                f"INDEX_DISTORTION_DETECTED: Xanh vỏ đỏ lòng! Index dương ({eff_idx_ret:.2%}) "
                f"nhưng độ rộng thị trường suy yếu mạnh (A/D ratio = {market_adv_decl_ratio:.2f})."
            )
        elif is_conc_distorted:
            reason = (
                f"INDEX_DISTORTION_DETECTED: Top 3 stocks ({', '.join([t for t, _ in top3])}) "
                f"account for {concentration_ratio:.1%} of VN30 index movement."
            )
        else:
            reason = "NORMAL_INDEX_DISTRIBUTION"

        return {
            "is_distorted": is_distorted,
            "distortion_type": distortion_type,
            "concentration_ratio": round(concentration_ratio, 4),
            "vn30_return": round(total_vn30_return, 4),
            "market_adv_decl_ratio": market_adv_decl_ratio,
            "top_contributors": top3_details,
            "reason": reason
        }


vn30_distortion_monitor = VN30DistortionMonitor()
