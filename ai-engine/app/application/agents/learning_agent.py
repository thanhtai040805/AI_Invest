"""Learning Agent — TASK-501, 502

Theo dõi IC (Information Coefficient) và quản lý cơ chế CDC (Contingency Decision Control).
Tự động kích hoạt phòng vệ và giảm size khi mô hình bắt đầu sai lệch.
"""

import logging
import pandas as pd
import numpy as np
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class LearningAgent:
    def __init__(self):
        self.baseline_ic = 0.15 # Baseline IC sau 100 trades
        self.cdc_active = False

    def track_ic(self, factor_scores: pd.Series, realized_returns: pd.Series) -> float:
        """Tính IC bằng Spearman correlation."""
        if len(factor_scores) < 30:
            return 0.0
            
        ic = factor_scores.corr(realized_returns, method='spearman')
        return ic

    def diagnose_decay(self, rolling_ic_20: float) -> str:
        """Chẩn đoán nguyên nhân sụt giảm IC."""
        if rolling_ic_20 < self.baseline_ic * 0.5:
            # Nếu sụt giảm quá 50%
            return "STRUCTURAL_DECAY" # Kích hoạt CDC
        return "NORMAL_VARIANCE"

    def update_cdc_status(self, current_ic: float, slippage_ratio: float):
        """Quản lý trạng thái CDC (Contingency Decision Control)."""
        diagnosis = self.diagnose_decay(current_ic)
        
        # IOS Trigger 1: IC Decay > 50%
        # IOS Trigger 2: Slippage > 2x baseline (giả định ratio = actual/baseline)
        if diagnosis == "STRUCTURAL_DECAY" or slippage_ratio > 2.0:
            if not self.cdc_active:
                logger.critical("!!! CDC ACTIVATED: High model misalignment detected !!!")
                self.cdc_active = True
        else:
            if self.cdc_active:
                logger.info("CDC Deactivated: Model back in sync.")
                self.cdc_active = False

    def get_kelly_multiplier(self) -> float:
        """Cơ chế CDC: giảm size từ 1/4 xuống 1/8 Kelly."""
        return 0.5 if self.cdc_active else 1.0

learning_agent = LearningAgent()
