"""Tape Anomaly Detector — Vietnamized VSA & Price/Volume Anomaly Engine.

Mục tiêu:
Phát hiện dấu chân phân phối ngầm và hành vi thoát hàng của dòng tiền lớn (Smart Money/Insiders)
thông qua biến động bất thường của Giá & Khối lượng (Volume Spread Analysis - VSA).
Kích hoạt rút vốn / chặn mua MỚI TRƯỚC KHI các thông tin xấu về doanh nghiệp và thị trường được công bố.
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional
import logging

logger = logging.getLogger(__name__)


class TapeAnomalySeverity(Enum):
    NONE = "NONE"
    INFO = "INFO"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class AnomalyType(Enum):
    NONE = "NONE"
    CHURNING_DISTRIBUTION = "CHURNING_DISTRIBUTION"  # Xả hàng giấu giá (Vol cực lớn, giá đi ngang/tăng nhẹ)
    BEARISH_UPTHRUST = "BEARISH_UPTHRUST"  # Bẫy tăng giá (Râu nến trên dài, xả mạnh cuối phiên)
    STRUCTURAL_BREAKDOWN = "STRUCTURAL_BREAKDOWN"  # Thủng hỗ trợ then chốt (MA20/Swing Low) kèm Vol xả
    SELLING_PRESSURE_CLIMAX = "SELLING_PRESSURE_CLIMAX"  # Áp lực bán tháo chủ động đóng cửa ở đáy nến


@dataclass
class TapeAnomalyResult:
    has_anomaly: bool
    anomaly_type: AnomalyType
    severity: TapeAnomalySeverity
    action_recommended: str  # "PASS", "REDUCE_SIZE", "BLOCK_BUY", "EMERGENCY_EXIT"
    reason: str
    metrics: Dict[str, Any]


class TapeAnomalyDetector:
    """Bộ cảm biến dị thường Giá & Khối lượng (VSA Tape Reading) cho cổ phiếu và thị trường chung."""

    def analyze_candle(
        self,
        candle: Dict[str, Any],
        ma20_volume: float,
        ma20_price: Optional[float] = None,
        swing_low_price: Optional[float] = None,
    ) -> TapeAnomalyResult:
        """
        Phân tích một cây nến giao dịch (Daily hoặc Real-time Bar) để phát hiện dị thường.
        - candle: {open, high, low, close, volume}
        - ma20_volume: khối lượng trung bình 20 phiên
        - ma20_price: giá trị trung bình MA20 (tùy chọn)
        - swing_low_price: đáy cấu trúc gần nhất (tùy chọn)
        """
        open_p = float(candle.get("open", 0.0))
        high_p = float(candle.get("high", 0.0))
        low_p = float(candle.get("low", 0.0))
        close_p = float(candle.get("close", 0.0))
        vol = float(candle.get("volume", 0.0))

        if close_p <= 0 or high_p <= 0 or low_p <= 0 or high_p < low_p:
            return TapeAnomalyResult(
                has_anomaly=False,
                anomaly_type=AnomalyType.NONE,
                severity=TapeAnomalySeverity.NONE,
                action_recommended="PASS",
                reason="Dữ liệu nến không hợp lệ.",
                metrics={},
            )

        candle_range = high_p - low_p
        candle_range_pct = candle_range / close_p if close_p > 0 else 0.0
        vol_ratio = (vol / ma20_volume) if ma20_volume > 0 else 1.0
        upper_wick = high_p - max(open_p, close_p)
        lower_wick = min(open_p, close_p) - low_p

        metrics = {
            "volume_ratio_ma20": round(vol_ratio, 2),
            "candle_range_pct": round(candle_range_pct * 100, 2),
            "upper_wick_ratio": round(upper_wick / candle_range, 2) if candle_range > 0 else 0.0,
            "close_location_pct": round((close_p - low_p) / candle_range * 100, 2) if candle_range > 0 else 50.0,
        }

        # 1. Kiểm tra Churning Distribution (Phân phối ngầm):
        # Vol bùng nổ >= 2.2x MA20 nhưng biên độ giá hẹp (< 1.5%) -> Tay to xả hàng ngầm không cho giá tăng
        if vol_ratio >= 2.2 and candle_range_pct < 0.018:
            reason = (
                f"Phát hiện PHÂN PHỐI NGẦM (Churning): Volume gấp {vol_ratio:.1f}x MA20 "
                f"nhưng biên độ giá dao động chỉ {candle_range_pct*100:.1f}%. Dấu hiệu tay to xả hàng giấu giá."
            )
            logger.warning(f"[TapeAnomalyDetector] {reason}")
            return TapeAnomalyResult(
                has_anomaly=True,
                anomaly_type=AnomalyType.CHURNING_DISTRIBUTION,
                severity=TapeAnomalySeverity.CRITICAL,
                action_recommended="BLOCK_BUY",
                reason=reason,
                metrics=metrics,
            )

        # 2. Kiểm tra Bearish Upthrust / Bull Trap:
        # Râu nến trên chiếm > 50% chiều dài nến kèm Volume bùng nổ >= 1.8x MA20 -> Kéo xả trong phiên
        if candle_range > 0 and (upper_wick / candle_range >= 0.50) and vol_ratio >= 1.8:
            reason = (
                f"Phát hiện BẪY TĂNG GIÁ (Bearish Upthrust): Râu nến trên dài chiếm {upper_wick/candle_range*100:.1f}% "
                f"kèm Volume đột biến {vol_ratio:.1f}x MA20. Áp lực bán dội ngược cực mạnh từ phe nội bộ."
            )
            logger.warning(f"[TapeAnomalyDetector] {reason}")
            return TapeAnomalyResult(
                has_anomaly=True,
                anomaly_type=AnomalyType.BEARISH_UPTHRUST,
                severity=TapeAnomalySeverity.CRITICAL,
                action_recommended="BLOCK_BUY",
                reason=reason,
                metrics=metrics,
            )

        # 3. Kiểm tra Structural Breakdown (Gãy cấu trúc kỹ thuật):
        # Giá đóng cửa thủng MA20 hoặc Swing Low với Volume >= 1.5x MA20
        if ma20_price and close_p < ma20_price and vol_ratio >= 1.5:
            reason = (
                f"Phát hiện GÃY CẤU TRÚC (Breakdown MA20): Giá đóng cửa ({close_p:,.0f}) xuyên thủng MA20 ({ma20_price:,.0f}) "
                f"kèm Volume xả lớn ({vol_ratio:.1f}x MA20). Kích hoạt rút vốn bảo toàn tài sản."
            )
            logger.warning(f"[TapeAnomalyDetector] {reason}")
            return TapeAnomalyResult(
                has_anomaly=True,
                anomaly_type=AnomalyType.STRUCTURAL_BREAKDOWN,
                severity=TapeAnomalySeverity.CRITICAL,
                action_recommended="BLOCK_BUY",
                reason=reason,
                metrics=metrics,
            )

        if swing_low_price and close_p < swing_low_price and vol_ratio >= 1.2:
            reason = (
                f"Phát hiện THỦNG ĐÁY CẤU TRÚC (Breakdown Swing Low): Giá đóng cửa ({close_p:,.0f}) thủng đáy hỗ trợ ({swing_low_price:,.0f}) "
                f"kèm Volume {vol_ratio:.1f}x MA20."
            )
            logger.warning(f"[TapeAnomalyDetector] {reason}")
            return TapeAnomalyResult(
                has_anomaly=True,
                anomaly_type=AnomalyType.STRUCTURAL_BREAKDOWN,
                severity=TapeAnomalySeverity.CRITICAL,
                action_recommended="BLOCK_BUY",
                reason=reason,
                metrics=metrics,
            )

        # 4. Kiểm tra Áp lực bán tháo chủ động (Selling Pressure Climax):
        # Nến đỏ giảm mạnh, đóng cửa ở 20% đáy nến kèm Volume >= 2.0x MA20
        if close_p < open_p and metrics["close_location_pct"] <= 20.0 and vol_ratio >= 2.0:
            reason = (
                f"Phát hiện ÁP LỰC BÁN THÁO CHỦ ĐỘNG: Đóng cửa thấp nhất phiên kèm Volume bùng nổ {vol_ratio:.1f}x MA20."
            )
            return TapeAnomalyResult(
                has_anomaly=True,
                anomaly_type=AnomalyType.SELLING_PRESSURE_CLIMAX,
                severity=TapeAnomalySeverity.WARNING,
                action_recommended="REDUCE_SIZE",
                reason=reason,
                metrics=metrics,
            )

        return TapeAnomalyResult(
            has_anomaly=False,
            anomaly_type=AnomalyType.NONE,
            severity=TapeAnomalySeverity.NONE,
            action_recommended="PASS",
            reason="Hành động giá và khối lượng bình thường, không phát hiện dị thường VSA.",
            metrics=metrics,
        )


tape_anomaly_detector = TapeAnomalyDetector()
