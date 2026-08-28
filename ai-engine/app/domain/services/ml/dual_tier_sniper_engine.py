"""
Dual-Tier Sniper Engine & Dynamic Portfolio Harmonizer for HOSE (Spot Equity).
Harmonizes High-Conviction (Tier A+, Z >= 3.80) and Flexible Scalping (Tier A, 2.85 <= Z < 3.80)
with Macro Regime Switching and Asymmetric Trailing Stop Execution.
"""

import logging
import numpy as np
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

@dataclass
class TradeInstruction:
    ticker: str
    tier: str # 'TIER_A_PLUS', 'TIER_A', 'NO_TRADE'
    z_score: float
    pred_score: float
    target_weight_pct: float # e.g. 0.12 (12%) or 0.05 (5%)
    execution_mode: str # 'RUNNER_MODE' vs 'SWING_LOCK_MODE'
    breakeven_trigger_pct: float # e.g. +0.025 (+2.5%)
    hard_stop_pct: float # e.g. -0.035 (-3.5%)
    take_profit_pct: Optional[float] # e.g. +0.06 or None for runner
    rationale: str

class DualTierSniperEngine:
    """
    Production-grade Dual-Tier Sniper Engine:
    Harmonizes Tier A+ (Ultra Sniper) and Tier A (Flexible Sniper) setups.
    """
    
    def __init__(
        self,
        tier_a_plus_z_threshold: float = 3.80,
        tier_a_z_threshold: float = 2.85,
        tier_a_plus_weight: float = 0.12, # 12% NAV per position
        tier_a_weight: float = 0.05, # 5% NAV per position
        max_portfolio_equity: float = 1.00 # Max 100% equity (No leverage)
    ):
        self.tier_a_plus_z_threshold = tier_a_plus_z_threshold
        self.tier_a_z_threshold = tier_a_z_threshold
        self.tier_a_plus_weight = tier_a_plus_weight
        self.tier_a_weight = tier_a_weight
        self.max_portfolio_equity = max_portfolio_equity

    def evaluate_macro_regime(self, vnindex_df: pd.DataFrame, current_date: pd.Timestamp) -> str:
        """
        Determines the Macro Regime of HOSE:
        - BULL_EXPANSION: VN-Index >= MA50 (Both Tier A+ and Tier A active)
        - SIDEWAY_CHOPPY: VN-Index < MA50 but >= MA200 (Only Tier A+ active, Tier A paused)
        - BEAR_DEFENSE: VN-Index < MA50 and < MA200 (100% Cash, All tiers paused)
        """
        if vnindex_df is None or vnindex_df.empty:
            return "BULL_EXPANSION"
            
        ts_date = pd.to_datetime(current_date)
        sub = vnindex_df.loc[:ts_date]
        if len(sub) < 50:
            return "BULL_EXPANSION"
            
        close_series = sub['close']
        current_close = close_series.iloc[-1]
        ma50 = close_series.rolling(50).mean().iloc[-1]
        ma200 = close_series.rolling(200, min_periods=50).mean().iloc[-1]
        
        if current_close >= ma50:
            return "BULL_EXPANSION"
        elif current_close >= ma200:
            return "SIDEWAY_CHOPPY"
        else:
            return "BEAR_DEFENSE"

    def generate_trade_allocations(
        self,
        candidate_scores: pd.DataFrame, # Columns: ['ticker', 'pred_score', 'adtv20_bil']
        regime: str = "BULL_EXPANSION",
        top_k: int = 5
    ) -> List[TradeInstruction]:
        """
        Generates harmonized dual-tier trade instructions for the current session.
        """
        instructions = []
        
        if regime == "BEAR_DEFENSE":
            logger.info("Macro Regime is BEAR_DEFENSE. 100% Cash preservation mode.")
            return instructions
            
        # Filter Liquid Candidates (ADTV20 >= 10 Billion VND)
        liquid_df = candidate_scores[candidate_scores['adtv20_bil'] >= 10.0].copy()
        if len(liquid_df) < 5:
            logger.warning("Insufficient liquid candidates (< 5 stocks with ADTV20 >= 10B).")
            return instructions
            
        # Cross-sectional Z-score
        mean_score = liquid_df['pred_score'].mean()
        std_score = liquid_df['pred_score'].std()
        if std_score == 0 or np.isnan(std_score):
            std_score = 1.0
            
        liquid_df['z_score'] = (liquid_df['pred_score'] - mean_score) / std_score
        liquid_df = liquid_df.sort_values('pred_score', ascending=False)
        
        top_candidates = liquid_df.head(top_k)
        
        for rank_idx, row in enumerate(top_candidates.itertuples(), start=1):
            ticker = row.ticker
            z_val = row.z_score
            p_score = row.pred_score
            
            if z_val >= self.tier_a_plus_z_threshold:
                # TIER A+ (Ultra Sniper Setup)
                inst = TradeInstruction(
                    ticker=ticker,
                    tier="TIER_A_PLUS",
                    z_score=float(z_val),
                    pred_score=float(p_score),
                    target_weight_pct=self.tier_a_plus_weight,
                    execution_mode="RUNNER_MODE",
                    breakeven_trigger_pct=0.025, # Move stop to +0.2% when profit >= +2.5%
                    hard_stop_pct=-0.035, # Hard stop at -3.5%
                    take_profit_pct=None, # Runner mode with dynamic trailing
                    rationale=f"Rank #{rank_idx} | High Conviction Z={z_val:.2f} >= {self.tier_a_plus_z_threshold}σ (Full Size, Runner Mode)"
                )
                instructions.append(inst)
                
            elif z_val >= self.tier_a_z_threshold and regime == "BULL_EXPANSION":
                # TIER A (Flexible Sniper Setup - Active only in Bull Expansion)
                inst = TradeInstruction(
                    ticker=ticker,
                    tier="TIER_A",
                    z_score=float(z_val),
                    pred_score=float(p_score),
                    target_weight_pct=self.tier_a_weight,
                    execution_mode="SWING_LOCK_MODE",
                    breakeven_trigger_pct=0.025,
                    hard_stop_pct=-0.030, # Tighter stop at -3.0%
                    take_profit_pct=0.060, # Lock profit at +6.0%
                    rationale=f"Rank #{rank_idx} | Moderate Conviction Z={z_val:.2f} >= {self.tier_a_z_threshold}σ (Half Size, Swing Lock Mode)"
                )
                instructions.append(inst)
                
        return instructions

dual_tier_engine = DualTierSniperEngine()
