"""Advanced Risk Metrics — TASK-303

Tính toán Expected Shortfall (ES) và Max Drawdown.
Hỗ trợ đo lường rủi ro đuôi (Tail Risk) của danh mục.
"""

import logging
import numpy as np
import pandas as pd
from typing import List, Dict, Any, Optional, Tuple

logger = logging.getLogger(__name__)

class RiskMetricsEngine:
    def __init__(self, confidence_level: float = 0.95):
        self.confidence_level = confidence_level

    def calculate_var_es(self, returns: pd.Series) -> Tuple[float, float]:
        """Tính Value at Risk (VaR) và Expected Shortfall (ES) bằng phương pháp Historical Simulation."""
        if len(returns) < 30:
            return 0.0, 0.0
            
        # Sắp xếp lợi nhuận từ thấp đến cao
        sorted_rets = np.sort(returns.values)
        
        # VaR index
        alpha = 1 - self.confidence_level
        var_idx = int(alpha * len(sorted_rets))
        
        var = abs(sorted_rets[var_idx])
        
        # ES là trung bình của các khoản lỗ vượt quá VaR
        es = abs(np.mean(sorted_rets[:var_idx])) if var_idx > 0 else var
        
        return var, es

    def calculate_max_drawdown(self, equity_curve: pd.Series) -> float:
        """Tính Max Drawdown của đường cong vốn."""
        if equity_curve.empty:
            return 0.0
            
        running_max = equity_curve.cummax()
        drawdown = (equity_curve - running_max) / running_max
        return abs(drawdown.min())

    def calculate_current_drawdown(self, equity_curve: pd.Series) -> float:
        """Tính Drawdown hiện tại."""
        if equity_curve.empty:
            return 0.0
            
        peak = equity_curve.max()
        current = equity_curve.iloc[-1]
        return abs((current - peak) / peak) if peak > 0 else 0.0

risk_metrics_engine = RiskMetricsEngine()
