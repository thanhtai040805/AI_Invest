import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class StopLossOrder:
    ticker: str
    quantity: int
    urgency: str  # "EMERGENCY", "HIGH", "MEDIUM", "LOW", "CRITICAL_T25_LOCKED"
    reason: str
    suggested_action: str  # "SELL_ALL", "REDUCE_50_PCT", "LOCK_PROFIT", "SELL_ALL_AVAILABLE", "WAIT_T25_SETTLEMENT", "FLOOR_LOCK_RESET"
    rule_level: str = "HARD_STOP"  # "HARD_STOP", "TRAILING_STOP", "STRUCTURAL_EXIT", "FAST_EXIT", "TIME_STOP", "T25_LOCKED", "FLOOR_LOCK"


class StopLossEngine:
    """
    IOS v5.1 Multi-tier Stop-Loss Rule Engine (Senior Broker Edition - Chuẩn HOSE).
    Thứ tự ưu tiên sinh tử (Priority Guard Hierarchy):
    - Tầng 0: Kiểm tra Hàng khả dụng T+2.5 (Available Shares) & Xử lý Floor Lock kẹt sàn.
    - Tầng 1: Hard Law Điều 1 (Lỗ >= 2% NAV) -> Guard Clause TỐI CAO: Bán toàn bộ hàng khả dụng.
    - Tầng 2: Trailing Stop (Bảo vệ lợi nhuận) -> Lock Profit khi sụt giảm >= 35% từ đỉnh lãi.
    - Tầng 3: Structural Exit -> Thủng Swing Low hỗ trợ kỹ thuật.
    - Tầng 4: Fast Exit (VSA) -> Bearish Rejection kèm Vol > 1.5x MA20.
    - Tầng 5: Time Stop -> Chi phí cơ hội (cầm > 50% timeline mà lãi < 2%).
    """

    @staticmethod
    def round_hose_lot(shares: int) -> int:
        """Quy chuẩn lô giao dịch khớp lệnh liên tục trên HOSE (bội số của 100)."""
        if shares < 100:
            return shares
        return int(shares // 100) * 100

    def check_fast_exit(self, candle: Dict[str, Any], ma20_volume: float) -> Optional[str]:
        """Tầng 4: Phát hiện nến từ chối tăng (Bearish Rejection) với Volume đột biến."""
        open_p = candle.get("open", 0.0)
        close_p = candle.get("close", 0.0)
        high_p = candle.get("high", 0.0)
        low_p = candle.get("low", 0.0)
        volume = candle.get("volume", 0.0)

        if high_p <= low_p or volume <= 0 or ma20_volume <= 0:
            return None

        upper_wick = high_p - max(open_p, close_p)
        candle_range = high_p - low_p

        # Râu trên dài > 50% biên độ nến và Vol > 1.5x MA20
        if (upper_wick / candle_range > 0.5) and (volume > 1.5 * ma20_volume):
            return (
                f"Bearish Rejection (Tầng 4): Râu nến trên chiếm {(upper_wick/candle_range)*100:.1f}% biên độ "
                f"kèm Volume ({volume:,.0f}) đột biến > 1.5x MA20 ({ma20_volume:,.0f})."
            )
        return None

    def check_position(
        self,
        ticker: str,
        quantity: int,
        entry_price: float,
        current_price: float,
        nav: float,
        market_data: Optional[Dict[str, Any]] = None,
        available_shares: Optional[int] = None,
    ) -> Optional[StopLossOrder]:
        """
        Kiểm tra vị thế với hệ thống phòng thủ đa tầng chuẩn Brokerage sàn HOSE.
        - quantity: Tổng số lượng cổ phiếu đang sở hữu
        - available_shares: Số lượng cổ phiếu đã về tài khoản (T+2.5) sẵn sàng bán
        """
        if quantity <= 0:
            return None

        if market_data is None:
            market_data = {}

        ticker_upper = str(ticker).upper().strip()
        total_shares = quantity
        avail_shares = (
            available_shares
            if available_shares is not None
            else int(market_data.get("available_shares", total_shares))
        )

        unrealized_pnl = (current_price - entry_price) * total_shares
        pnl_pct = ((current_price - entry_price) / entry_price) if entry_price > 0 else 0.0
        pnl_pct_nav = (unrealized_pnl / nav) if nav > 0 else 0.0

        # Dữ liệu thị trường mở rộng
        peak_price = float(market_data.get("peak_price") or market_data.get("highest_price", entry_price))
        peak_pnl_pct = ((peak_price - entry_price) / entry_price) if entry_price > 0 else 0.0
        swing_low_price = market_data.get("swing_low_price") or market_data.get("swing_low")
        days_held = int(market_data.get("days_held") or market_data.get("holding_days", 0))
        expected_timeline = int(market_data.get("expected_timeline_days", 90))
        is_floor_locked = bool(market_data.get("is_floor_locked", False))
        last_order_unfilled = bool(market_data.get("last_order_unfilled", False))

        # =====================================================================
        # TẦNG 0.1: XỬ LÝ THANH KHOẢN KẸT SÀN (FLOOR LOCK / MÚA BÊN TRĂNG)
        # Nếu lệnh bán trước đó bị trôi/không khớp do trắng bên mua, hủy lệnh và đánh giá lại từ đầu
        # =====================================================================
        if is_floor_locked and last_order_unfilled:
            reason = (
                f"Thanh khoản nghẽn (Floor Lock / Múa bên trăng): {ticker_upper} trắng bên mua. "
                "Lệnh bán trước đó chưa khớp. Tạm hủy lệnh cũ, bỏ qua lượt phát này để tính toán lại từ đầu chu kỳ tới."
            )
            logger.warning(f"FLOOR LOCK UNFILLED for {ticker_upper}: {reason}")
            return StopLossOrder(
                ticker=ticker_upper,
                quantity=0,
                urgency="LOW",
                reason=reason,
                suggested_action="FLOOR_LOCK_RESET",
                rule_level="FLOOR_LOCK",
            )

        # =====================================================================
        # TẦNG 0.2: BẪY T+2.5 (SETTLEMENT CHECK)
        # Nếu hàng chưa về (avail_shares <= 0), không thể đẩy lệnh bán hợp lệ ra sàn HOSE!
        # =====================================================================
        if avail_shares <= 0:
            if pnl_pct_nav <= -0.02:
                reason = (
                    f"Báo động T+2.5: Vi phạm Hard Stop ({pnl_pct_nav*100:.2f}% NAV <= -2%), "
                    f"nhưng cổ phiếu chưa về tài khoản khả dụng (Khả dụng: 0, Đang kẹt T+2.5: {total_shares} cp)! "
                    "Yêu cầu Risk Desk theo dõi giờ mở khóa thanh toán hoặc kích hoạt Hedge VN30F."
                )
                logger.critical(f"!!! T+2.5 LOCKED HARD STOP for {ticker_upper}: {reason} !!!")
                return StopLossOrder(
                    ticker=ticker_upper,
                    quantity=0,
                    urgency="CRITICAL_T25_LOCKED",
                    reason=reason,
                    suggested_action="WAIT_T25_SETTLEMENT",
                    rule_level="T25_LOCKED",
                )
            return None

        # =====================================================================
        # TẦNG 1: HARD LAW ĐIỀU 1 (GUARD CLAUSE TỐI CAO - BẢO VỆ MẠNG SỐNG TÀI KHOẢN)
        # Bắt buộc đặt ở vị trí số 1. Chạm lỗ -2% NAV là bán toàn bộ hàng khả dụng ngay!
        # =====================================================================
        if pnl_pct_nav <= -0.02:
            sell_qty = self.round_hose_lot(avail_shares)
            reason = (
                f"Vi phạm Hard Law Điều 1: Lỗ {pnl_pct_nav*100:.2f}% NAV (Ngưỡng tử thần -2.00%). "
                f"Kích hoạt bán khẩn cấp toàn bộ hàng khả dụng T+2.5 ({sell_qty}/{total_shares} cp)."
            )
            logger.critical(f"!!! HARD STOP LOSS TRIGGERED for {ticker_upper}: {reason} !!!")
            return StopLossOrder(
                ticker=ticker_upper,
                quantity=sell_qty,
                urgency="EMERGENCY",
                reason=reason,
                suggested_action="SELL_ALL",
                rule_level="HARD_STOP",
            )

        # =====================================================================
        # TẦNG 2: TRAILING STOP (BẢO VỆ THÀNH QUẢ - KHÓA LỢI NHUẬN)
        # Nguyên tắc: Never let a big winner turn into a loser.
        # Khi lãi từng đạt >= 10%, nếu giá tụt giảm đánh mất >= 35% phần lãi đỉnh -> Lock Profit.
        # =====================================================================
        profit_peak = peak_price - entry_price
        if peak_pnl_pct >= 0.10 and profit_peak > 0:
            giveback_ratio = (peak_price - current_price) / profit_peak
            if giveback_ratio >= 0.35:
                sell_qty = self.round_hose_lot(avail_shares)
                reason = (
                    f"Trailing Stop: {ticker_upper} từng đạt đỉnh lãi +{peak_pnl_pct*100:.1f}% (đỉnh {peak_price:,.0f}đ), "
                    f"hiện đã đánh mất {giveback_ratio*100:.1f}% lợi nhuận đỉnh (giá hiện tại {current_price:,.0f}đ). "
                    "Khóa toàn bộ lợi nhuận khả dụng để bảo toàn vốn."
                )
                logger.warning(f"TRAILING STOP TRIGGERED for {ticker_upper}: {reason}")
                return StopLossOrder(
                    ticker=ticker_upper,
                    quantity=sell_qty,
                    urgency="HIGH",
                    reason=reason,
                    suggested_action="LOCK_PROFIT",
                    rule_level="TRAILING_STOP",
                )

        # =====================================================================
        # TẦNG 3: STRUCTURAL EXIT (BẢO VỆ CẤU TRÚC / THỦNG SWING LOW)
        # Giá gãy vùng đáy hỗ trợ gần nhất -> Cấu trúc tăng giá bị phá vỡ.
        # =====================================================================
        if swing_low_price and current_price < float(swing_low_price):
            sell_qty = self.round_hose_lot(avail_shares)
            reason = (
                f"Structural Exit (Tầng 3): Giá đóng cửa ({current_price:,.0f}) thủng Swing Low hỗ trợ "
                f"({float(swing_low_price):,.0f}). Cấu trúc kỹ thuật bị phá vỡ."
            )
            logger.critical(f"!!! STRUCTURAL STOP TRIGGERED for {ticker_upper}: {reason} !!!")
            return StopLossOrder(
                ticker=ticker_upper,
                quantity=sell_qty,
                urgency="HIGH",
                reason=reason,
                suggested_action="SELL_ALL",
                rule_level="STRUCTURAL_EXIT",
            )

        # =====================================================================
        # TẦNG 4: FAST EXIT (CẮT SỚM VSA)
        # Nến Bearish Rejection râu trên dài bất thường kèm Vol > 1.5x MA20.
        # Bán 50% lô chẵn để phòng ngừa phân phối đỉnh ngắn hạn.
        # =====================================================================
        current_candle = market_data.get("current_candle")
        ma20_vol = float(market_data.get("ma20_volume", 0.0))
        if current_candle and ma20_vol > 0:
            fast_exit_reason = self.check_fast_exit(current_candle, ma20_vol)
            if fast_exit_reason:
                half_shares = self.round_hose_lot(int(avail_shares * 0.5))
                sell_qty = half_shares if half_shares > 0 else avail_shares
                logger.critical(f"!!! FAST EXIT TRIGGERED for {ticker_upper}: {fast_exit_reason} !!!")
                return StopLossOrder(
                    ticker=ticker_upper,
                    quantity=sell_qty,
                    urgency="HIGH",
                    reason=fast_exit_reason,
                    suggested_action="REDUCE_50_PCT",
                    rule_level="FAST_EXIT",
                )

        # =====================================================================
        # TẦNG 5: TIME STOP (CHI PHÍ CƠ HỘI)
        # Cầm quá 50% timeline mà lãi < 2% (nhưng không vi phạm Hard Stop).
        # Hạ 50% vị thế lô chẵn để cơ cấu sang cổ phiếu có Catalyst mạnh hơn.
        # =====================================================================
        if days_held > (expected_timeline * 0.5) and 0.0 <= pnl_pct < 0.02:
            half_shares = self.round_hose_lot(int(avail_shares * 0.5))
            sell_qty = half_shares if half_shares > 0 else avail_shares
            reason = (
                f"Time Stop (Tầng 5): Nắm giữ {days_held} ngày (> 50% timeline {expected_timeline} ngày) "
                f"nhưng hiệu suất chỉ đạt +{pnl_pct*100:.2f}% (< 2.0%). Cảnh báo chôn vốn, hạ tỷ trọng cơ cấu."
            )
            logger.warning(f"TIME STOP WARNING for {ticker_upper}: {reason}")
            return StopLossOrder(
                ticker=ticker_upper,
                quantity=sell_qty,
                urgency="MEDIUM",
                reason=reason,
                suggested_action="REDUCE_50_PCT",
                rule_level="TIME_STOP",
            )

        return None


stop_loss_engine = StopLossEngine()

