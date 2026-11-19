"""Portfolio Optimizer & Cross-Sectional Finalist Ranker (IOS v5.2).

Provides:
1. Backward-compatible `optimize_selection()` (Greedy CSS + Correlation + Sector filter).
2. Two-Stage Tournament Finalist Ranker (`rank_and_select_finalists()`):
   - Ranks ALL post-CIO/Counter-Thesis survivors head-to-head on a unified 0-100 scale
     combining Playbook Score, Counter-Thesis safety (100 - CTS), Fundamental Upside,
     and Sector Leadership.
   - Enforces dynamic AUM/Regime `max_positions`, pairwise correlation cap, and sector cap.
"""

from __future__ import annotations

import logging
import pandas as pd
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class PortfolioOptimizer:
    def __init__(self, min_positions: int = 12, max_positions: int = 18):
        self.min_positions = min_positions
        self.max_positions = max_positions
        self.correlation_limit = 0.50
        self.sector_limit_pct = 0.35

    def optimize_selection(
        self,
        candidates: pd.DataFrame,
        corr_matrix: pd.DataFrame,
        current_nav: float,
    ) -> List[str]:
        """Chọn danh sách mã tối ưu bằng thuật toán Greedy + Correlation Filter (legacy API)."""
        candidates = candidates.sort_values("css", ascending=False)

        selected_tickers: List[str] = []
        sector_exposure: Dict[str, float] = {}

        for _, row in candidates.iterrows():
            ticker = row["symbol"]
            sector = row.get("sector", "Unknown")

            if len(selected_tickers) >= self.max_positions:
                break

            is_correlated = False
            for existing in selected_tickers:
                if existing in corr_matrix.index and ticker in corr_matrix.columns:
                    if corr_matrix.loc[existing, ticker] > self.correlation_limit:
                        is_correlated = True
                        break

            if is_correlated:
                logger.info(f"Skipping {ticker}: Correlated with existing selection.")
                continue

            potential_pct = 1.0 / self.max_positions
            current_sector_pct = sector_exposure.get(sector, 0.0)

            if (current_sector_pct + potential_pct) > self.sector_limit_pct:
                logger.info(f"Skipping {ticker}: Sector {sector} limit reached.")
                continue

            selected_tickers.append(ticker)
            sector_exposure[sector] = current_sector_pct + potential_pct

        if len(selected_tickers) < self.min_positions:
            logger.warning(f"Only selected {len(selected_tickers)} tickers, below min {self.min_positions}")

        return selected_tickers

    def score_finalist(self, candidate: Dict[str, Any]) -> Dict[str, Any]:
        """Compute cross-sectional final rank score (0-100) for a CIO-approved finalist."""
        playbook_score = float(candidate.get("playbook_score", candidate.get("css", 60.0)))
        cts_score = float(candidate.get("cts_score", 30.0))
        cts_safety = max(0.0, min(100.0, 100.0 - cts_score))
        sector_rank = float(candidate.get("sector_leadership_score", 50.0))

        current_price = float(candidate.get("current_price", 0.0))
        base_case_price = float(candidate.get("base_case_target_price", 0.0))
        if current_price > 0 and base_case_price > 0:
            upside_pct = (base_case_price / current_price) - 1.0
        else:
            upside_pct = 0.15
        # Map upside [0% .. 40%] -> [20 .. 100]
        upside_score = max(10.0, min(100.0, 20.0 + (upside_pct / 0.40) * 80.0))

        counter_verdict = str(candidate.get("counter_verdict", "PROCEED")).upper().strip()
        cio_bonus = 3.0 if counter_verdict == "PROCEED" else (-5.0 if counter_verdict == "CONDITIONAL" else 0.0)

        final_score = (
            0.45 * playbook_score
            + 0.25 * cts_safety
            + 0.15 * upside_score
            + 0.15 * sector_rank
            + cio_bonus
        )
        final_score = round(max(0.0, min(100.0, final_score)), 2)

        return {
            "final_rank_score": final_score,
            "playbook_score": round(playbook_score, 2),
            "cts_safety_score": round(cts_safety, 2),
            "upside_pct": round(upside_pct * 100.0, 2),
            "upside_score": round(upside_score, 2),
            "sector_leadership_score": round(sector_rank, 2),
            "cio_bonus": cio_bonus,
        }

    def rank_and_select_finalists(
        self,
        finalists: List[Dict[str, Any]],
        existing_positions: List[Dict[str, Any]],
        total_nav: float,
        max_positions: int,
        max_sector_weight: float = 0.35,
        min_slot_weight: float = 0.06,
        corr_matrix: Optional[pd.DataFrame] = None,
        correlation_limit: float = 0.65,
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Rank all CIO-approved finalists head-to-head and select top winners for remaining slots.

        Returns:
            (selected_finalists, leaderboard) where `leaderboard` includes every evaluated finalist
            with its rank, score breakdown, selected flag, and exact selection/skip rationale.
        """
        if not finalists:
            return [], []

        active_positions = [
            p for p in (existing_positions or [])
            if int(p.get("shares", p.get("quantity", 0)) or 0) > 0
        ]
        held_tickers = {
            str(p.get("ticker") or p.get("symbol") or "").upper().strip()
            for p in active_positions
            if p.get("ticker") or p.get("symbol")
        }
        sector_weights: Dict[str, float] = {}
        for p in active_positions:
            sec = str(p.get("sector") or "General").strip()
            mv = float(p.get("market_value", 0.0))
            if mv <= 0:
                mv = int(p.get("shares", p.get("quantity", 0)) or 0) * float(
                    p.get("current_price", p.get("average_price", 0.0)) or 0.0
                )
            w = (mv / total_nav) if total_nav > 0 else 0.0
            sector_weights[sec] = sector_weights.get(sec, 0.0) + w

        scored_items: List[Dict[str, Any]] = []
        for item in finalists:
            breakdown = self.score_finalist(item)
            enriched = {
                **item,
                **breakdown,
            }
            scored_items.append(enriched)

        # Deterministic tie-breaker: higher final_rank_score -> higher playbook_score -> lower CTS -> ticker A-Z
        scored_items.sort(
            key=lambda x: (
                -float(x["final_rank_score"]),
                -float(x["playbook_score"]),
                float(x.get("cts_score", 50.0)),
                str(x.get("ticker", "")),
            ),
        )

        available_slots = max(0, int(max_positions) - len(held_tickers))
        selected: List[Dict[str, Any]] = []
        selected_tickers: List[str] = []
        leaderboard: List[Dict[str, Any]] = []

        for rank_idx, item in enumerate(scored_items, start=1):
            ticker = str(item.get("ticker", "")).upper().strip()
            sector = str(item.get("sector") or "General").strip()
            reason = "SELECTED"
            is_selected = False

            if ticker in held_tickers:
                reason = "ALREADY_HELD_IN_PORTFOLIO"
            elif len(selected) >= available_slots:
                reason = f"PORTFOLIO_SLOTS_FULL ({len(held_tickers) + len(selected)}/{max_positions})"
            else:
                # Pairwise correlation check against held + already-selected finalists
                high_corr_peer = None
                if corr_matrix is not None and not corr_matrix.empty and ticker in corr_matrix.columns:
                    for peer in list(held_tickers) + selected_tickers:
                        if peer != ticker and peer in corr_matrix.index:
                            rho = float(corr_matrix.loc[peer, ticker])
                            if rho > correlation_limit:
                                high_corr_peer = (peer, rho)
                                break

                if high_corr_peer is not None:
                    reason = f"HIGH_CORRELATION_WITH_{high_corr_peer[0]}(rho={high_corr_peer[1]:.2f})"
                elif sector_weights.get(sector, 0.0) + min_slot_weight > max_sector_weight + 1e-6:
                    reason = (
                        f"SECTOR_CAP_REACHED({sector}: "
                        f"{sector_weights.get(sector, 0.0)*100:.1f}%/{max_sector_weight*100:.0f}%)"
                    )
                else:
                    is_selected = True
                    selected.append({**item, "rank": rank_idx, "selection_reason": reason})
                    selected_tickers.append(ticker)
                    sector_weights[sector] = sector_weights.get(sector, 0.0) + min_slot_weight

            leaderboard.append({
                "rank": rank_idx,
                "ticker": ticker,
                "sector": sector,
                "conviction": item.get("conviction"),
                "css": item.get("css"),
                "playbook_score": item["playbook_score"],
                "cts_score": item.get("cts_score"),
                "upside_pct": item["upside_pct"],
                "sector_leadership_score": item["sector_leadership_score"],
                "final_rank_score": item["final_rank_score"],
                "counter_verdict": item.get("counter_verdict"),
                "selected": is_selected,
                "selection_reason": reason,
            })

        return selected, leaderboard


portfolio_optimizer = PortfolioOptimizer()
