import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional
from app.services.risk_engine import MacroRiskEngine
from app.core.position_sizing.sizing import volatility_targeted_size

logger = logging.getLogger(__name__)

class PortfolioOptimizer:
    def __init__(self, risk_engine: Optional[MacroRiskEngine] = None):
        self.risk_engine = risk_engine or MacroRiskEngine()

    def optimize_allocation(
        self, 
        alpha_scores: List[Dict[str, Any]], 
        portfolio_value: float,
        target_date: Optional[Any] = None
    ) -> List[Dict[str, Any]]:
        """
        Optimizes portfolio allocation based on Alpha scores, Volatility, and Macro Risk.
        
        Args:
            alpha_scores: List of {symbol, composite_score, volatility_20d, price, adv_20d}
            portfolio_value: Total capital to allocate
            target_date: Historical date for risk calculation
            
        Returns:
            List of {symbol, weight, quantity, reason}
        """
        if not alpha_scores:
            return []

        # 1. Get Macro Risk Multiplier
        risk_data = self.risk_engine.calculate_risk_score(target_date)
        multiplier = risk_data["risk_multiplier"]
        
        # 2. Filter for top candidates (Alpha > 0)
        candidates = [s for s in alpha_scores if s.get("composite_score", 0) > 0]
        if not candidates:
            return []

        # 3. Calculate Inverse Volatility weights (Basic Risk Parity proxy)
        # Higher volatility = Lower base weight
        df = pd.DataFrame(candidates)
        df['vol'] = df['volatility_20d'].replace(0, np.nan).fillna(df['volatility_20d'].median())
        df['inv_vol'] = 1.0 / df['vol']
        
        # 4. Combine with Alpha Score
        # Weight ~ Alpha * (1/Vol)
        df['raw_weight'] = df['composite_score'] * df['inv_vol']
        df['target_weight'] = df['raw_weight'] / df['raw_weight'].sum()
        
        # 5. Apply Macro Multiplier to TOTAL exposure
        # If multiplier is 0.5, total weight sum will be 0.5 (50% cash)
        df['final_weight'] = df['target_weight'] * multiplier
        
        results = []
        for _, row in df.iterrows():
            # 6. Apply individual stock constraints via sizing module
            # (ATR, ADV Cap, Max Pct)
            qty, method = volatility_targeted_size(
                symbol=row['symbol'],
                price=row['price'],
                portfolio_value=portfolio_value,
                target_vol_pct=0.02 * multiplier, # Scale risk budget by macro
                atr=row.get('atr_14'), # If available
                adv_20d_volume=row.get('adv_20d'),
                max_adv_pct=0.05,
                max_pct_per_position=0.20 # Cap any single stock at 20%
            )
            
            # Recalculate final weight based on rounded quantity
            actual_weight = (qty * row['price']) / portfolio_value if portfolio_value > 0 else 0
            
            results.append({
                "symbol": row['symbol'],
                "alpha_score": round(row['composite_score'], 2),
                "suggested_weight": round(actual_weight, 4),
                "quantity": qty,
                "sizing_method": method
            })
            
        return sorted(results, key=lambda x: x['suggested_weight'], reverse=True)
