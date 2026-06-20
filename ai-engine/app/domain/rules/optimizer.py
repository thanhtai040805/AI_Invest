"""Portfolio Optimizer — TASK-313

Lựa chọn danh mục tối ưu 12-18 mã từ danh sách các ứng viên (candidates).
Áp dụng:
1. Greeding selection based on CSS Score.
2. Pairwise Correlation constraint (< 0.5).
3. Sector limit (<= 35% NAV).
"""

import logging
import pandas as pd
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class PortfolioOptimizer:
    def __init__(self, min_positions: int = 12, max_positions: int = 18):
        self.min_positions = min_positions
        self.max_positions = max_positions
        self.correlation_limit = 0.5
        self.sector_limit_pct = 0.35

    def optimize_selection(
        self, 
        candidates: pd.DataFrame, 
        corr_matrix: pd.DataFrame,
        current_nav: float
    ) -> List[str]:
        """Chọn danh sách mã tối ưu bằng thuật toán Greedy + Correlation Filter."""
        
        # 1. Sắp xếp candidates theo CSS giảm dần
        candidates = candidates.sort_values('css', ascending=False)
        
        selected_tickers = []
        sector_exposure = {} # sector -> current_pct
        
        for _, row in candidates.iterrows():
            ticker = row['symbol']
            sector = row.get('sector', 'Unknown')
            
            if len(selected_tickers) >= self.max_positions:
                break
                
            # --- Constraint 1: Correlation ---
            is_correlated = False
            for existing in selected_tickers:
                if existing in corr_matrix.index and ticker in corr_matrix.columns:
                    if corr_matrix.loc[existing, ticker] > self.correlation_limit:
                        is_correlated = True
                        break
            
            if is_correlated:
                logger.info(f"Skipping {ticker}: Correlated with existing selection.")
                continue
                
            # --- Constraint 2: Sector Limit ---
            # Giả định phân bổ đều nếu chưa biết size chính xác
            potential_pct = 1.0 / self.max_positions 
            current_sector_pct = sector_exposure.get(sector, 0)
            
            if (current_sector_pct + potential_pct) > self.sector_limit_pct:
                logger.info(f"Skipping {ticker}: Sector {sector} limit reached.")
                continue
                
            # --- Selection ---
            selected_tickers.append(ticker)
            sector_exposure[sector] = current_sector_pct + potential_pct
            
        if len(selected_tickers) < self.min_positions:
            logger.warning(f"Only selected {len(selected_tickers)} tickers, below min {self.min_positions}")
            
        return selected_tickers

portfolio_optimizer = PortfolioOptimizer()
