"""Market Breadth & Distribution Day Counter — Vietnamized Institutional Risk Engine.

Mục tiêu:
1. Đếm số ngày phân phối (Distribution Days) trên chỉ số VN-Index trong cửa sổ trượt 20 phiên.
   - Định nghĩa phiên phân phối: VN-Index giảm >= 0.2% với Khối lượng cao hơn phiên liền trước.
   - Khi có 4-5 phiên phân phối xuất hiện trong 20 phiên -> Xác suất gãy đổ toàn sàn là rất cao.
2. Đo lường Độ rộng thị trường (Market Breadth) và phát hiện hiện tượng "Xanh vỏ đỏ lòng" (Kéo trụ xả Midcap).
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class BreadthHealthTier(Enum):
    HEALTHY = "HEALTHY"
    NEUTRAL = "NEUTRAL"
    DETERIORATING = "DETERIORATING"
    CRITICAL_DISTRIBUTION = "CRITICAL_DISTRIBUTION"


@dataclass
class BreadthRiskEvaluation:
    health_tier: BreadthHealthTier
    distribution_days_count: int
    breadth_ma20_pct: float
    is_divergence_green_index_red_breadth: bool
    recommended_min_cash_pct: float
    action_recommended: str  # "PASS", "REDUCE_SIZE", "BLOCK_BUY"
    reason: str


class BreadthRiskEngine:
    """Bộ phân tích độ rộng thị trường toàn cảnh và đếm phiên phân phối sàn HOSE."""

    def count_distribution_days(self, daily_candles: List[Dict[str, Any]]) -> int:
        """
        Đếm số phiên phân phối của VN-Index trong tối đa 20 phiên gần nhất.
        daily_candles: danh sách nến theo thứ tự thời gian tăng dần [cũ -> mới nhất].
        """
        if len(daily_candles) < 2:
            return 0

        # Lấy tối đa 21 nến gần nhất để tính 20 phiên biến động
        recent_candles = daily_candles[-21:]
        dist_count = 0

        for i in range(1, len(recent_candles)):
            prev = recent_candles[i - 1]
            curr = recent_candles[i]

            prev_close = float(prev.get("close", 0))
            curr_close = float(curr.get("close", 0))
            prev_vol = float(prev.get("volume", 0))
            curr_vol = float(curr.get("volume", 0))

            if prev_close <= 0 or curr_close <= 0:
                continue

            pct_change = (curr_close - prev_close) / prev_close

            # Phiên phân phối: Giảm >= 0.2% kèm Volume cao hơn phiên trước
            if pct_change <= -0.002 and curr_vol > prev_vol:
                dist_count += 1

        return dist_count

    def evaluate_market_breadth(
        self,
        distribution_days: int,
        breadth_ma20_pct: float,
        vnindex_change_pct: float = 0.0,
    ) -> BreadthRiskEvaluation:
        """
        Đánh giá bức tranh kỹ thuật toàn sàn và xác định mức đệm tiền mặt an toàn.
        - distribution_days: số ngày phân phối trong 20 phiên
        - breadth_ma20_pct: tỷ lệ % cổ phiếu trên sàn HOSE đang giao dịch trên MA20 (0 - 100)
        - vnindex_change_pct: biến động % của chỉ số VN-Index phiên gần nhất
        """
        # Kiểm tra hiện tượng "Xanh vỏ đỏ lòng" (Kéo trụ xả hàng):
        # VN-Index xanh hoặc đi ngang (>= -0.1%) nhưng độ rộng thị trường cực thấp (< 35%)
        is_divergence = (vnindex_change_pct >= -0.001) and (breadth_ma20_pct < 35.0)

        # 1. Kịch bản phân phối nguy hiểm: >= 5 phiên phân phối
        if distribution_days >= 5:
            reason = (
                f"CẢNH BÁO PHÂN PHỐI NGUY HIỂM: Xuất hiện {distribution_days} phiên phân phối trong 20 phiên gần nhất. "
                f"Xác suất thị trường gãy sóng là > 80%. Ép tỷ trọng tiền mặt tối thiểu 60% và khóa mua mới."
            )
            logger.critical(f"[BreadthRiskEngine] {reason}")
            return BreadthRiskEvaluation(
                health_tier=BreadthHealthTier.CRITICAL_DISTRIBUTION,
                distribution_days_count=distribution_days,
                breadth_ma20_pct=round(breadth_ma20_pct, 1),
                is_divergence_green_index_red_breadth=is_divergence,
                recommended_min_cash_pct=60.0,
                action_recommended="BLOCK_BUY",
                reason=reason,
            )

        # 2. Kịch bản cảnh giác: 4 phiên phân phối hoặc Xanh vỏ đỏ lòng rõ rệt
        if distribution_days >= 4 or (is_divergence and breadth_ma20_pct < 25.0):
            reason = (
                f"CẢNH BÁO BỨC TRANH TOÀN CẢNH SUY YẾU: {distribution_days} phiên phân phối, "
                f"Độ rộng thị trường (Mã > MA20): {breadth_ma20_pct:.1f}%"
                + (" (Phát hiện Xanh vỏ đỏ lòng)." if is_divergence else ".")
                + " Nâng tiền mặt lên tối thiểu 40% và giảm 50% size vị thế mới."
            )
            logger.warning(f"[BreadthRiskEngine] {reason}")
            return BreadthRiskEvaluation(
                health_tier=BreadthHealthTier.DETERIORATING,
                distribution_days_count=distribution_days,
                breadth_ma20_pct=round(breadth_ma20_pct, 1),
                is_divergence_green_index_red_breadth=is_divergence,
                recommended_min_cash_pct=40.0,
                action_recommended="REDUCE_SIZE",
                reason=reason,
            )

        # 3. Kịch bản độ rộng thu hẹp nhẹ:
        if breadth_ma20_pct < 40.0 or distribution_days == 3:
            reason = (
                f"Độ rộng thị trường thu hẹp ({breadth_ma20_pct:.1f}% > MA20, {distribution_days} phiên phân phối). "
                f"Yêu cầu thận trọng, duy trì tối thiểu 25% tiền mặt."
            )
            return BreadthRiskEvaluation(
                health_tier=BreadthHealthTier.NEUTRAL,
                distribution_days_count=distribution_days,
                breadth_ma20_pct=round(breadth_ma20_pct, 1),
                is_divergence_green_index_red_breadth=is_divergence,
                recommended_min_cash_pct=25.0,
                action_recommended="PASS",
                reason=reason,
            )

        # 4. Kịch bản thị trường lành mạnh
        return BreadthRiskEvaluation(
            health_tier=BreadthHealthTier.HEALTHY,
            distribution_days_count=distribution_days,
            breadth_ma20_pct=round(breadth_ma20_pct, 1),
            is_divergence_green_index_red_breadth=False,
            recommended_min_cash_pct=10.0,
            action_recommended="PASS",
            reason=f"Độ rộng thị trường tốt ({breadth_ma20_pct:.1f}% > MA20) và chỉ có {distribution_days} phiên phân phối.",
        )


breadth_risk_engine = BreadthRiskEngine()
