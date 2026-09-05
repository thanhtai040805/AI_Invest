"""Engine 4: Portfolio Construction Engine (Sector, Macro, Correlation & Marginal Risk)

Chức năng:
- Chuyển hóa preliminary_target thành portfolio_target có xét đến cấu trúc toàn danh mục:
    1. Giới hạn tỷ trọng nhóm ngành: Sector Exposure <= 35% NAV.
    2. Giới hạn phân bổ chiến lược từ Agent-12 (Strategy CIO): Macro Weight Cap (mặc định 15%, có thể bị ép xuống 8%).
    3. Kiểm soát tương quan cặp (Pairwise Correlation < 0.5): Tránh tập trung rủi ro ẩn.
    4. Đánh giá rủi ro biên (Marginal Risk): Tác động của vị thế lên độ biến động danh mục tổng thể.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
import pandas as pd

from app.domain.rules.optimizer import PortfolioOptimizer

logger = logging.getLogger(__name__)


@dataclass
class ConstructionResult:
    portfolio_target: float
    portfolio_target_value_vnd: float
    sector: str
    sector_exposure_before: float
    sector_exposure_after: float
    sector_limit: float
    macro_weight_cap: float
    avg_correlation: float
    stress_correlation_status: str
    marginal_risk_pct: float
    construction_reasons: List[str] = field(default_factory=list)


class PortfolioConstructionEngine:
    def __init__(
        self,
        sector_limit_pct: float = 0.35,
        correlation_limit: float = 0.50,
        max_single_stock_pct: float = 0.15,
    ):
        self.sector_limit_pct = sector_limit_pct
        self.correlation_limit = correlation_limit
        self.max_single_stock_pct = max_single_stock_pct
        self.optimizer = PortfolioOptimizer()

    def construct(
        self,
        ticker: str,
        preliminary_target: float,
        sector: str,
        total_nav: float,
        existing_positions: List[Dict[str, Any]],
        macro_weight_cap: float = 0.15,
        corr_matrix: Optional[pd.DataFrame] = None,
    ) -> ConstructionResult:
        reasons = []
        target = preliminary_target

        # 1. Áp trần vĩ mô từ Strategy CIO (Macro Cap)
        effective_cap = min(self.max_single_stock_pct, macro_weight_cap)
        if target > effective_cap:
            reasons.append(f"Ép trần phân bổ vĩ mô CIO: Giảm từ {target*100:.1f}% xuống {effective_cap*100:.1f}%.")
            target = effective_cap

        # 2. Tính toán tỷ trọng ngành hiện tại (Sector Exposure)
        sector_clean = str(sector).strip() or "General"
        current_sector_value = sum(
            float(p.get("market_value", 0.0))
            for p in existing_positions
            if p.get("sector") == sector_clean
        )
        current_sector_pct = (current_sector_value / total_nav) if total_nav > 0 else 0.0

        # Trừ đi vị thế hiện tại của chính mã này nếu đã có trong ngành để tránh tính trùng
        existing_pos_stock = next((p for p in existing_positions if p["ticker"] == ticker), None)
        current_stock_weight = (
            float(existing_pos_stock.get("market_value", 0.0)) / total_nav
            if existing_pos_stock and total_nav > 0
            else 0.0
        )
        sector_excluding_this_stock = max(0.0, current_sector_pct - current_stock_weight)

        # Kiểm tra trần ngành 35%
        max_allowed_for_sector = max(0.0, self.sector_limit_pct - sector_excluding_this_stock)
        if target > max_allowed_for_sector:
            reasons.append(
                f"Chạm trần ngành {sector_clean} (35% NAV): Giảm từ {target*100:.1f}% xuống {max_allowed_for_sector*100:.1f}%."
            )
            target = max_allowed_for_sector

        # 3. Phân tích tương quan cặp (Correlation Filter)
        high_corr_tickers = []
        avg_corr = 0.35  # Mặc định an toàn
        if corr_matrix is not None and not corr_matrix.empty and ticker in corr_matrix.columns:
            corrs = []
            for p in existing_positions:
                existing_ticker = p["ticker"]
                if existing_ticker != ticker and existing_ticker in corr_matrix.index:
                    c_val = float(corr_matrix.loc[existing_ticker, ticker])
                    corrs.append(c_val)
                    if c_val > self.correlation_limit:
                        high_corr_tickers.append(f"{existing_ticker} (rho={c_val:.2f})")
            if corrs:
                avg_corr = sum(corrs) / len(corrs)

        if high_corr_tickers:
            # Phạt tỷ trọng 25% nếu tương quan cao với danh mục hiện hữu
            penalty_factor = 0.75
            reasons.append(
                f"Tương quan cao (> 0.5) với {', '.join(high_corr_tickers)}: Giảm tỷ trọng 25%."
            )
            target *= penalty_factor
            stress_status = "WARNING_HIGH_CORRELATION"
        else:
            stress_status = "ACCEPTABLE"

        target = round(max(0.0, target), 4)
        sector_after = round(sector_excluding_this_stock + target, 4)
        marginal_risk = round(target * avg_corr * 0.02, 4)

        return ConstructionResult(
            portfolio_target=target,
            portfolio_target_value_vnd=round(target * total_nav, 2),
            sector=sector_clean,
            sector_exposure_before=round(current_sector_pct, 4),
            sector_exposure_after=sector_after,
            sector_limit=self.sector_limit_pct,
            macro_weight_cap=effective_cap,
            avg_correlation=round(avg_corr, 2),
            stress_correlation_status=stress_status,
            marginal_risk_pct=marginal_risk,
            construction_reasons=reasons,
        )
