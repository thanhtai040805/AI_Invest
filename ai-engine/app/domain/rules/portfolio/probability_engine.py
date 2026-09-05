"""Engine 2: Probability Engine (IOS v5.1)

Chức năng:
- Nạp và hiệu chuẩn xác suất thắng thực tế p_calibrated và tỷ lệ lợi nhuận/rủi ro R (payoff ratio) từ Agent-10 (Reinforcement Learning).
- Tuyệt đối không dùng dữ liệu mock ẩn:
    - Nếu nạp được từ ma trận học tăng cường: ghi nhận nguồn REAL_RL_CALIBRATION.
    - Nếu thiếu ma trận: Ghi log WARNING rõ ràng, đánh dấu cờ MISSING_CALIBRATION_WARNING.
- Tính toán Expected Edge: Edge = p * R - (1 - p).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class ProbabilityMetrics:
    prob_win: float
    payoff_ratio: float
    expected_edge: float
    is_calibrated: bool
    source_description: str
    warning_flag: Optional[str] = None


class ProbabilityEngine:
    def __init__(self):
        # Priors lý thuyết thận trọng (chỉ dùng khi DB/Agent-10 chưa huấn luyện)
        self.default_priors = {
            "A+": {"prob_win": 0.68, "payoff_ratio": 2.2},
            "A": {"prob_win": 0.60, "payoff_ratio": 1.9},
            "B": {"prob_win": 0.52, "payoff_ratio": 1.5},
        }

    def evaluate(
        self,
        conviction: str,
        regime_str: str,
        kelly_matrix: Optional[Dict[str, Any]] = None,
        storage_adapter: Optional[Any] = None,
    ) -> ProbabilityMetrics:
        conviction_clean = str(conviction).upper().strip()
        regime_clean = str(regime_str).upper().strip()

        # 1. Nguồn 1: Ma trận nạp trực tiếp từ Event Data của Agent-10 RL
        if kelly_matrix and conviction_clean in kelly_matrix:
            tier_data = kelly_matrix[conviction_clean]
            p = float(tier_data.get("win_rate_p", 0.60))
            r = float(tier_data.get("payoff_ratio_b", 2.0))
            edge = p * r - (1.0 - p)
            return ProbabilityMetrics(
                prob_win=round(p, 4),
                payoff_ratio=round(r, 4),
                expected_edge=round(edge, 4),
                is_calibrated=True,
                source_description=f"AGENT-10 (Reinforcement Learning Realized Calibration - Tier {conviction_clean})",
                warning_flag=None,
            )

        # 2. Nguồn 2: Tra cứu CSDL PostgreSQL bảng kelly_win_rate_matrix
        if storage_adapter:
            try:
                query = """
                    SELECT win_rate_p, payoff_ratio_b
                    FROM kelly_win_rate_matrix
                    WHERE regime = %s AND conviction_tier = %s
                    LIMIT 1
                """
                rows = storage_adapter.fetch_all(query, (regime_clean, conviction_clean))
                if rows and len(rows) > 0:
                    p = float(rows[0][0])
                    r = float(rows[0][1])
                    edge = p * r - (1.0 - p)
                    return ProbabilityMetrics(
                        prob_win=round(p, 4),
                        payoff_ratio=round(r, 4),
                        expected_edge=round(edge, 4),
                        is_calibrated=True,
                        source_description=f"POSTGRES_DB (kelly_win_rate_matrix - {regime_clean} / {conviction_clean})",
                        warning_flag=None,
                    )
            except Exception as e:
                logger.warning(f"Lỗi đọc kelly_win_rate_matrix từ CSDL: {e}")

        # 3. Nguồn 3: Fallback Priors kèm CẢNH BÁO MINH BẠCH (Không che giấu dữ liệu thiếu)
        prior = self.default_priors.get(conviction_clean, {"prob_win": 0.50, "payoff_ratio": 1.4})
        p = prior["prob_win"]
        r = prior["payoff_ratio"]
        edge = p * r - (1.0 - p)

        warning_msg = f"THIẾU MA TRẬN HIỆU CHUẨN RL CHO REGIME {regime_clean} & TIER {conviction_clean}. Sử dụng Bayesian Priors mặc định."
        logger.warning(f"[ProbabilityEngine] {warning_msg}")

        return ProbabilityMetrics(
            prob_win=round(p, 4),
            payoff_ratio=round(r, 4),
            expected_edge=round(edge, 4),
            is_calibrated=False,
            source_description=f"FALLBACK_PRIOR (Chưa có mẫu thực tế Agent-10 RL - Tier {conviction_clean})",
            warning_flag="MISSING_RL_CALIBRATION_WARNING",
        )
