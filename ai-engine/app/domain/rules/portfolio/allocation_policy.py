"""Portfolio Allocation Policy — Playbook & AUM-Tiered Elastic Guardrails (IOS v5.2).

Single source of truth mapping (Market Regime, Session Context, Total NAV) into:
1. InvestmentPlaybook:
   - CRISIS_LOCKDOWN: 0% new buys (100% cash preservation).
   - TACTICAL_SWING: Unstable / sideways / mild-bear rebound trading (10-20% total NAV,
     1-3 stocks, 6%-12% per stock; anything below floor drops to 0%).
   - MARKET_LEADER: Bull growth concentrated leader portfolio (up to 100% NAV,
     3-5 stocks for NAV <= 1B VND, expanding smoothly for larger NAV tiers).
2. AUM-Tiered scaling so small/medium portfolios stay concentrated while larger
   portfolios expand stock count and liquidity floors naturally.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict


class InvestmentPlaybook(str, Enum):
    CRISIS_LOCKDOWN = "CRISIS_LOCKDOWN"
    TACTICAL_SWING = "TACTICAL_SWING"
    MARKET_LEADER = "MARKET_LEADER"


class AUMTier(str, Enum):
    NAV_LE_1B = "NAV_LE_1B"
    NAV_1B_TO_5B = "NAV_1B_TO_5B"
    NAV_GT_5B = "NAV_GT_5B"


@dataclass(frozen=True)
class PortfolioPolicy:
    playbook: InvestmentPlaybook
    aum_tier: AUMTier
    max_equity_exposure: float
    min_cash_target: float
    min_positions: int
    max_positions: int
    min_entry_weight: float
    max_single_stock_weight: float
    max_a_plus_stock_weight: float
    max_sector_weight: float
    hard_stop_loss_nav_pct: float
    max_locked_t25_pct: float
    min_adtv20_vnd: float
    timeline_months: int
    shortlist_size: int = 10
    allow_zero_cash_when_healthy: bool = False
    breadth_deteriorating_scale: float = 0.50

    def single_stock_cap_for_conviction(self, conviction: str) -> float:
        conv = str(conviction or "").upper().strip()
        if conv == "A+":
            return self.max_a_plus_stock_weight
        return self.max_single_stock_weight

    def to_risk_limits(self, conviction: str = "A") -> Dict[str, float]:
        single_cap = self.single_stock_cap_for_conviction(conviction)
        return {
            "limit_type": f"HOSE_EQUITY_{self.playbook.value}_{self.aum_tier.value}",
            "max_single_stock_pct": round(single_cap * 100.0, 2),
            "max_sector_pct": round(self.max_sector_weight * 100.0, 2),
            "hard_stop_loss_pct": round(self.hard_stop_loss_nav_pct * 100.0, 2),
            "max_locked_t25_pct": round(self.max_locked_t25_pct, 2),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "playbook": self.playbook.value,
            "aum_tier": self.aum_tier.value,
            "max_equity_exposure": self.max_equity_exposure,
            "min_cash_target": self.min_cash_target,
            "min_positions": self.min_positions,
            "max_positions": self.max_positions,
            "min_entry_weight": self.min_entry_weight,
            "max_single_stock_weight": self.max_single_stock_weight,
            "max_a_plus_stock_weight": self.max_a_plus_stock_weight,
            "max_sector_weight": self.max_sector_weight,
            "hard_stop_loss_nav_pct": self.hard_stop_loss_nav_pct,
            "max_locked_t25_pct": self.max_locked_t25_pct,
            "min_adtv20_vnd": self.min_adtv20_vnd,
            "timeline_months": self.timeline_months,
            "shortlist_size": self.shortlist_size,
            "allow_zero_cash_when_healthy": self.allow_zero_cash_when_healthy,
            "breadth_deteriorating_scale": self.breadth_deteriorating_scale,
        }


def resolve_aum_tier(total_nav: float) -> AUMTier:
    nav = float(total_nav or 0.0)
    # 5% hysteresis buffer above 1B VND so a 1B account doesn't flip tier on minor daily P&L
    if nav <= 1_050_000_000.0:
        return AUMTier.NAV_LE_1B
    if nav <= 5_000_000_000.0:
        return AUMTier.NAV_1B_TO_5B
    return AUMTier.NAV_GT_5B


def resolve_playbook(
    regime_str: str,
    session_context: str = "Normal",
) -> InvestmentPlaybook:
    regime_upper = str(regime_str or "BULL_MARKET").upper().strip()
    ctx_upper = str(session_context or "Normal").upper().strip()

    if (
        ctx_upper in {"CRISIS", "STRESS_SEVERE"}
        or "CRISIS" in regime_upper
        or "CRASH" in regime_upper
    ):
        return InvestmentPlaybook.CRISIS_LOCKDOWN

    if (
        any(k in regime_upper for k in ("BEAR", "RANGE", "CHOPPY", "SIDEWAYS", "DEFENS"))
        or ctx_upper == "STRESS"
    ):
        return InvestmentPlaybook.TACTICAL_SWING

    return InvestmentPlaybook.MARKET_LEADER


def resolve_portfolio_policy(
    regime_str: str,
    total_nav: float = 1_000_000_000.0,
    session_context: str = "Normal",
) -> PortfolioPolicy:
    playbook = resolve_playbook(regime_str, session_context=session_context)
    aum_tier = resolve_aum_tier(total_nav)

    if playbook == InvestmentPlaybook.CRISIS_LOCKDOWN:
        return PortfolioPolicy(
            playbook=playbook,
            aum_tier=aum_tier,
            max_equity_exposure=0.0,
            min_cash_target=1.0,
            min_positions=0,
            max_positions=0,
            min_entry_weight=0.0,
            max_single_stock_weight=0.0,
            max_a_plus_stock_weight=0.0,
            max_sector_weight=0.0,
            hard_stop_loss_nav_pct=0.02,
            max_locked_t25_pct=0.0,
            min_adtv20_vnd=50_000_000_000.0,
            timeline_months=1,
            shortlist_size=0,
            allow_zero_cash_when_healthy=False,
            breadth_deteriorating_scale=0.0,
        )

    if playbook == InvestmentPlaybook.TACTICAL_SWING:
        # Unstable / sideways / mild-bear tactical deployment:
        # 10-20% total NAV ceiling (80% cash cushion), concentrated into 1-3 (or 4 for >5B) liquid setups.
        if aum_tier == AUMTier.NAV_LE_1B:
            return PortfolioPolicy(
                playbook=playbook,
                aum_tier=aum_tier,
                max_equity_exposure=0.20,
                min_cash_target=0.80,
                min_positions=1,
                max_positions=3,
                min_entry_weight=0.06,
                max_single_stock_weight=0.10,
                max_a_plus_stock_weight=0.12,
                max_sector_weight=0.14,
                hard_stop_loss_nav_pct=0.02,
                max_locked_t25_pct=35.0,
                min_adtv20_vnd=15_000_000_000.0,
                timeline_months=1,
                shortlist_size=10,
                allow_zero_cash_when_healthy=False,
                breadth_deteriorating_scale=0.75,
            )
        if aum_tier == AUMTier.NAV_1B_TO_5B:
            return PortfolioPolicy(
                playbook=playbook,
                aum_tier=aum_tier,
                max_equity_exposure=0.20,
                min_cash_target=0.80,
                min_positions=1,
                max_positions=3,
                min_entry_weight=0.06,
                max_single_stock_weight=0.10,
                max_a_plus_stock_weight=0.10,
                max_sector_weight=0.14,
                hard_stop_loss_nav_pct=0.02,
                max_locked_t25_pct=35.0,
                min_adtv20_vnd=25_000_000_000.0,
                timeline_months=1,
                shortlist_size=10,
                allow_zero_cash_when_healthy=False,
                breadth_deteriorating_scale=0.75,
            )
        return PortfolioPolicy(
            playbook=playbook,
            aum_tier=aum_tier,
            max_equity_exposure=0.20,
            min_cash_target=0.80,
            min_positions=2,
            max_positions=4,
            min_entry_weight=0.05,
            max_single_stock_weight=0.08,
            max_a_plus_stock_weight=0.08,
            max_sector_weight=0.20,
            hard_stop_loss_nav_pct=0.02,
            max_locked_t25_pct=35.0,
            min_adtv20_vnd=50_000_000_000.0,
            timeline_months=1,
            shortlist_size=12,
            allow_zero_cash_when_healthy=False,
            breadth_deteriorating_scale=0.75,
        )

    # MARKET_LEADER (BULL_MARKET): concentrated growth leader portfolio scaled by AUM tier
    if aum_tier == AUMTier.NAV_LE_1B:
        return PortfolioPolicy(
            playbook=playbook,
            aum_tier=aum_tier,
            max_equity_exposure=1.00,
            min_cash_target=0.00,
            min_positions=3,
            max_positions=5,
            min_entry_weight=0.10,
            max_single_stock_weight=0.25,
            max_a_plus_stock_weight=0.30,
            max_sector_weight=0.50,
            hard_stop_loss_nav_pct=0.042,
            max_locked_t25_pct=50.0,
            min_adtv20_vnd=15_000_000_000.0,
            timeline_months=3,
            shortlist_size=12,
            allow_zero_cash_when_healthy=True,
            breadth_deteriorating_scale=0.50,
        )
    if aum_tier == AUMTier.NAV_1B_TO_5B:
        return PortfolioPolicy(
            playbook=playbook,
            aum_tier=aum_tier,
            max_equity_exposure=0.95,
            min_cash_target=0.05,
            min_positions=5,
            max_positions=7,
            min_entry_weight=0.08,
            max_single_stock_weight=0.20,
            max_a_plus_stock_weight=0.22,
            max_sector_weight=0.40,
            hard_stop_loss_nav_pct=0.032,
            max_locked_t25_pct=45.0,
            min_adtv20_vnd=25_000_000_000.0,
            timeline_months=3,
            shortlist_size=14,
            allow_zero_cash_when_healthy=False,
            breadth_deteriorating_scale=0.50,
        )
    return PortfolioPolicy(
        playbook=playbook,
        aum_tier=aum_tier,
        max_equity_exposure=0.90,
        min_cash_target=0.10,
        min_positions=7,
        max_positions=12,
        min_entry_weight=0.05,
        max_single_stock_weight=0.15,
        max_a_plus_stock_weight=0.15,
        max_sector_weight=0.35,
        hard_stop_loss_nav_pct=0.02,
        max_locked_t25_pct=35.0,
        min_adtv20_vnd=50_000_000_000.0,
        timeline_months=3,
        shortlist_size=16,
        allow_zero_cash_when_healthy=False,
        breadth_deteriorating_scale=0.50,
    )
