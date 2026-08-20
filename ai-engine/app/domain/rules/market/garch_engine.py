"""GARCH Cash Engine — TASK-302

Dự báo biến động (Volatility Forecasting) và xác định tỷ lệ Cash.
Sử dụng mô hình GARCH(1,1) để tính toán "VIX VN Analog".
"""

import logging
import numpy as np
import pandas as pd
from datetime import date
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

class GARCHCashEngine:
    def __init__(self, omega: float = 0.00001, alpha: float = 0.05, beta: float = 0.80, gamma: float = 0.10):
        # Parameters mặc định cho GJR-GARCH(1,1) trên thị trường VN (fallback nếu MLE fail)
        self.omega = omega
        self.alpha = alpha
        self.beta = beta
        self.gamma = gamma

    def fit_from_data(self, returns: pd.Series) -> bool:
        """Ước lượng tham số GJR-GARCH từ dữ liệu lịch sử bằng MLE với Variance Targeting."""
        import scipy.optimize as opt
        
        n = len(returns)
        if n < 50:
            return False
            
        r = returns.values - np.mean(returns.values)
        var_init = np.var(r, ddof=1)
        if var_init < 1e-12:
            return False
            
        def nll(params):
            alpha, beta, gamma = params
            omega = var_init * (1.0 - alpha - beta - 0.5 * gamma)
            if omega <= 1e-12:
                return 1e10
            sigma2 = np.full(n, var_init)
            for t in range(1, n):
                i_t = 1.0 if r[t-1] < 0.0 else 0.0
                sigma2[t] = omega + (alpha + gamma * i_t) * (r[t-1] ** 2) + beta * sigma2[t-1]
            if np.any(sigma2 <= 1e-12):
                return 1e10
            return 0.5 * float(np.sum(np.log(sigma2) + (r ** 2) / sigma2))
            
        bounds = ((0.0, 1.0), (0.0, 1.0), (0.0, 1.0))
        x0 = np.array([0.05, 0.80, 0.10])
        
        try:
            res = opt.minimize(
                nll, x0, bounds=bounds,
                constraints={"type": "ineq", "fun": lambda p: 0.999 - (p[0] + p[1] + 0.5 * p[2])},
                method="SLSQP", options={"maxiter": 200},
            )
            if res.success:
                self.alpha, self.beta, self.gamma = res.x
                self.omega = var_init * (1.0 - self.alpha - self.beta - 0.5 * self.gamma)
                logger.info(f"GJR-GARCH MLE success: omega={self.omega:.6e}, alpha={self.alpha:.4f}, beta={self.beta:.4f}, gamma={self.gamma:.4f}")
                return True
        except Exception as e:
            logger.warning(f"GJR-GARCH MLE failed: {e}")
            
        return False

    def forecast_volatility(self, returns: pd.Series) -> float:
        """Dự báo biến động cho phiên tiếp theo (hàng ngày) sử dụng GJR-GARCH(1,1)."""
        if len(returns) < 20:
            return returns.std() if not returns.empty else 0.02 # Fallback 2%
            
        # Fit model parameters dynamically on historical returns
        self.fit_from_data(returns)
        
        r = returns.values - np.mean(returns.values)
        n = len(r)
        
        # Tính toán sigma chuỗi lịch sử
        sigmasq = np.zeros(n)
        sigmasq[0] = returns.var() # Initial seed
        
        for i in range(1, n):
            i_t = 1.0 if r[i-1] < 0.0 else 0.0
            sigmasq[i] = self.omega + (self.alpha + self.gamma * i_t) * (r[i-1] ** 2) + self.beta * sigmasq[i-1]
            
        # Sigma dự báo cho t+1
        i_n = 1.0 if r[-1] < 0.0 else 0.0
        next_sigmasq = self.omega + (self.alpha + self.gamma * i_n) * (r[-1] ** 2) + self.beta * sigmasq[-1]
        
        # Annualized volatility (assuming 252 trading days)
        ann_vol = np.sqrt(next_sigmasq) * np.sqrt(252)
        return ann_vol

    def calculate_cash_allocation(self, forecasted_vol: float) -> float:
        """Xác định tỷ lệ Cash dựa trên biến động dự báo.
        
        Nguyên tắc: Vol càng cao -> Cash càng cao.
        Thresholds (Ví dụ):
            Vol < 15% (Low): 5-10% Cash
            Vol 15-25% (Normal): 10-30% Cash
            Vol > 25% (High): > 30% Cash
        """
        if forecasted_vol < 0.15:
            return 0.10
        elif forecasted_vol < 0.25:
            # Tuyến tính từ 10% đến 30%
            return 0.10 + (forecasted_vol - 0.15) * 2.0
        else:
            # Vol > 25%, tăng mạnh Cash để phòng thủ
            return min(0.80, 0.30 + (forecasted_vol - 0.25) * 3.0)

    def get_index_returns(self, target_date: date, window: int = 504) -> pd.Series:
        """Lấy chuỗi lợi nhuận VNINDEX từ DB (mặc định 504 phiên = 2 năm cho GARCH)."""
        import psycopg2
        from app.infrastructure.database.pg_pool import DB_URL
        
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        cur.execute("""
            SELECT close_adj FROM market_data_daily
            WHERE ticker = 'VNINDEX' AND date <= %s
            ORDER BY date DESC LIMIT %s
        """, (target_date, window + 1))
        
        rows = cur.fetchall()
        conn.close()
        
        if len(rows) < 2:
            return pd.Series()
            
        prices = [r[0] for r in rows][::-1] # Đảo ngược về trình tự thời gian
        return pd.Series(prices).pct_change().dropna()

garch_engine = GARCHCashEngine()
