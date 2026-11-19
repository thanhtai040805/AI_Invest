"""Big-Picture Structure-Confirmed Exit & Stop-Loss Engine (IOS v5.3 - Phương án A).

Nguyên tắc cốt lõi:
- Tuyệt đối KHÔNG bán theo nến 1 phút rút râu hay 1 cây nến giảm trong phiên (09:15-14:14).
- Bỏ hoàn toàn Trailing Stop cơ học theo % tụt đỉnh (Tầng 2 cũ); thay bằng Hỗ Trợ Động theo
  cấu trúc xu hướng (MA20 / MA10 / Higher-Low) ngay trong Trụ cột Gãy Cấu Trúc.
- Mọi quyết định bán chỉ đánh giá ở cửa sổ xác nhận cuối phiên (>= 14:15 ICT) trên
  nến tổng hợp cả ngày + Bức Tranh Lớn đa phiên (Mô hình giá, Kháng cự/Hỗ trợ, Khối lượng/Dòng tiền):
    - Tầng 0: Kiểm tra T+2.5 & Floor Lock (Múa bên trăng).
    - Tầng 1 (Phương án A - Confirmed Capital Backstop): Lỗ sâu >= -2.5% NAV hoặc >= -10% cổ phiếu
      KÈM nến ngày xác nhận đóng cửa yếu (không rút chân hồi mạnh).
    - Tầng 3 (Structural Breakdown & Dynamic Trend Support): Giá đóng cửa thủng Hỗ trợ cấu trúc
      (Swing Low nền giá, hoặc nâng lên MA20/Higher-Low khi đã lãi >= 10%) KÈM xác nhận dòng tiền/Vol
      hoặc gãy sâu > 2%.
    - Tầng 4 (Confirmed Distribution at Resistance/Climax): Hội tụ đủ 3 trụ cột (ở vùng kháng cự/tăng rướn
      + dòng tiền đa phiên phân phối + nến ngày biên độ >= 2.5% đóng cửa đỏ ở 30% đáy kèm Vol >= 1.5x MA20).
    - Tầng 5 (Time Stop có xác nhận dòng tiền): Cầm quá 50% timeline Playbook, giá tù đọng (-3%..+2%)
      và dòng tiền không tích lũy / RS âm.
- Auto-Sweep chống tồn kho zombie (100 cp chiếm slot): Mọi lệnh hạ 50% mà phần dư < 4% NAV tự động
  đóng dứt điểm 100% vị thế để trả slot ngay cho danh mục cô đọng.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from app.domain.rules.technical_structure import (
    TechnicalStructureAnalyzer,
    TechnicalStructureSnapshot,
)

logger = logging.getLogger(__name__)


@dataclass
class StopLossOrder:
    ticker: str
    quantity: int
    urgency: str  # "EMERGENCY", "HIGH", "MEDIUM", "LOW", "CRITICAL_T25_LOCKED"
    reason: str
    suggested_action: str  # "SELL_ALL", "REDUCE_50_PCT", "WAIT_T25_SETTLEMENT", "FLOOR_LOCK_RESET"
    rule_level: str = "CONFIRMED_CAPITAL_STOP"  # "CONFIRMED_CAPITAL_STOP", "STRUCTURAL_BREAKDOWN", "CONFIRMED_DISTRIBUTION", "TIME_STOP", "T25_LOCKED", "FLOOR_LOCK"


class StopLossEngine:
    """
    IOS v5.3 Big-Picture Structure-Confirmed Exit Engine (Phương án A - Chuẩn HOSE).
    """

    @staticmethod
    def round_hose_lot(shares: int) -> int:
        """Quy chuẩn lô giao dịch khớp lệnh liên tục trên HOSE (bội số của 100)."""
        if shares < 100:
            return shares
        return int(shares // 100) * 100

    @staticmethod
    def _coerce_structure(raw: Any) -> Optional[TechnicalStructureSnapshot]:
        if isinstance(raw, TechnicalStructureSnapshot):
            return raw
        if isinstance(raw, dict) and raw:
            try:
                return TechnicalStructureSnapshot(
                    close=float(raw.get("close", 0.0)),
                    ma10=float(raw.get("ma10", 0.0)),
                    ma20=float(raw.get("ma20", 0.0)),
                    ma50=float(raw.get("ma50", 0.0)),
                    ma200=float(raw.get("ma200", 0.0)),
                    ma20_slope_pct=float(raw.get("ma20_slope_pct", 0.0)),
                    ma50_slope_pct=float(raw.get("ma50_slope_pct", 0.0)),
                    ma20_volume=float(raw.get("ma20_volume", 0.0)),
                    swing_low_20d=float(raw.get("swing_low_20d", 0.0)),
                    swing_low_60d=float(raw.get("swing_low_60d", 0.0)),
                    recent_higher_low=float(raw["recent_higher_low"]) if raw.get("recent_higher_low") is not None else None,
                    resistance_20d=float(raw.get("resistance_20d", 0.0)),
                    resistance_60d=float(raw.get("resistance_60d", 0.0)),
                    distance_to_resistance_pct=float(raw.get("distance_to_resistance_pct", 99.0)),
                    extension_vs_ma20_pct=float(raw.get("extension_vs_ma20_pct", 0.0)),
                    higher_high_higher_low=bool(raw.get("higher_high_higher_low", False)),
                    price_structure=str(raw.get("price_structure", "NEUTRAL")),
                    up_down_vol_ratio_10d=float(raw.get("up_down_vol_ratio_10d", 1.0)),
                    cmf_20d=float(raw.get("cmf_20d", 0.0)),
                    obv_slope_10d=float(raw.get("obv_slope_10d", 0.0)),
                    stock_dist_days_10d=int(raw.get("stock_dist_days_10d", 0)),
                    foreign_net_10d=float(raw.get("foreign_net_10d", 0.0)),
                    money_flow_state=str(raw.get("money_flow_state", "NEUTRAL")),
                    rs_20d=float(raw.get("rs_20d", 0.0)),
                    big_picture_score=float(raw.get("big_picture_score", 50.0)),
                )
            except Exception:
                return None
        return None

    def _apply_partial_or_sweep(
        self,
        total_shares: int,
        avail_shares: int,
        current_price: float,
        nav: float,
        min_remaining_weight: float,
    ) -> tuple[int, str, bool]:
        """Compute 50% HOSE lot reduction; auto-sweep 100% if residual falls below minimum impact weight."""
        half_shares = self.round_hose_lot(int(avail_shares * 0.5))
        if half_shares <= 0:
            return self.round_hose_lot(avail_shares), "SELL_ALL", True

        remaining_shares = max(0, total_shares - half_shares)
        remaining_weight = ((remaining_shares * current_price) / nav) if (nav > 0 and current_price > 0) else 0.0
        if remaining_shares < 100 or (min_remaining_weight > 0 and remaining_weight < min_remaining_weight):
            return self.round_hose_lot(avail_shares), "SELL_ALL", True
        return half_shares, "REDUCE_50_PCT", False

    def check_confirmed_distribution(
        self,
        session_candle: Optional[Dict[str, Any]],
        daily_ma20_volume: float,
        structure: Optional[TechnicalStructureSnapshot],
        current_price: float,
    ) -> Optional[str]:
        """Trụ cột 2 (Tầng 4 mới): Chỉ xác nhận phân phối khi hội tụ đủ 3 yếu tố Bức Tranh Lớn.

        1. Vị trí giá: Đang ở vùng kháng cự mạnh (<= 2.5% dưới đỉnh 20p/60p) hoặc tăng rướn (>= +10% trên MA20).
        2. Dòng tiền đa phiên suy yếu: >= 2 phiên phân phối trong 10p gần nhất, hoặc CMF < 0 / Up-Down Vol < 0.90.
        3. Nến ngày xác nhận đảo chiều thực: biên độ cả ngày >= 2.5%, đóng cửa đỏ ở 30% đáy nến và Vol ngày >= 1.5x MA20.
        """
        if not session_candle or structure is None:
            return None

        ma20_vol = daily_ma20_volume if daily_ma20_volume > 0 else structure.ma20_volume
        if ma20_vol <= 0:
            return None

        candle_eval = TechnicalStructureAnalyzer.evaluate_session_candle(session_candle, ma20_vol)
        if not candle_eval["valid"]:
            return None

        # 1. Kiểm tra nến ngày xác nhận đảo chiều rõ (loại hoàn toàn nhiễu 1 bước giá / rút râu lẻ)
        if (
            candle_eval["session_range_pct"] < 0.025
            or not candle_eval["is_red"]
            or candle_eval["close_location_pct"] > 0.30
            or candle_eval["daily_vol_ratio"] < 1.50
        ):
            return None

        # 2. Kiểm tra vị trí giá so với Bức Tranh Lớn (Kháng cự hoặc Tăng rướn)
        ext_ma20 = (
            ((current_price - structure.ma20) / structure.ma20 * 100.0)
            if structure.ma20 > 0 else structure.extension_vs_ma20_pct
        )
        at_resistance = structure.distance_to_resistance_pct <= 2.5
        is_extended = ext_ma20 >= 10.0 or structure.price_structure in ("EXTENDED_CLIMAX", "DISTRIBUTION_TOP")
        if not (at_resistance or is_extended):
            return None

        # 3. Kiểm tra dòng tiền đa phiên đã chuyển suy yếu / phân phối
        flow_weakening = (
            structure.stock_dist_days_10d >= 2
            or structure.money_flow_state == "DISTRIBUTING"
            or (structure.cmf_20d < 0.0 and structure.up_down_vol_ratio_10d < 0.95)
        )
        if not flow_weakening:
            return None

        loc_tag = f"tăng rướn +{ext_ma20:.1f}% trên MA20" if is_extended else f"vùng kháng cự (cách đỉnh {structure.distance_to_resistance_pct:.1f}%)"
        return (
            f"Confirmed Distribution (Trụ cột 2 - Bức Tranh Lớn): Hội tụ đủ 3 yếu tố cuối phiên — "
            f"(1) Giá tại {loc_tag} [{structure.price_structure}]; "
            f"(2) Dòng tiền đa phiên suy yếu ({structure.money_flow_state}, {structure.stock_dist_days_10d} phiên phân phối/10p, "
            f"CMF={structure.cmf_20d:+.2f}, Up/Down Vol={structure.up_down_vol_ratio_10d:.2f}); "
            f"(3) Nến ngày xác nhận xả (biên độ {candle_eval['session_range_pct']*100:.1f}%, "
            f"đóng cửa ở {candle_eval['close_location_pct']*100:.0f}% đáy, Vol ngày {candle_eval['daily_vol_ratio']:.2f}x MA20)."
        )

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
        Kiểm tra vị thế theo Phương án A (Big-Picture + Near-Close Confirmation >= 14:15 ICT).
        """
        if quantity <= 0:
            return None

        if market_data is None:
            market_data = {}

        ticker_upper = str(ticker).upper().strip()
        total_shares = int(quantity)
        avail_shares = (
            int(available_shares)
            if available_shares is not None
            else int(market_data.get("available_shares", total_shares))
        )

        unrealized_pnl = (current_price - entry_price) * total_shares
        pnl_pct = ((current_price - entry_price) / entry_price) if entry_price > 0 else 0.0
        pnl_pct_nav = (unrealized_pnl / nav) if nav > 0 else 0.0

        peak_price = float(market_data.get("peak_price") or market_data.get("highest_price") or entry_price)
        peak_pnl_pct = ((peak_price - entry_price) / entry_price) if entry_price > 0 else 0.0
        swing_low_override = market_data.get("swing_low_price") or market_data.get("swing_low")
        days_held = int(market_data.get("days_held") or market_data.get("holding_days", 0))
        expected_timeline = int(market_data.get("expected_timeline_days", 30))
        is_floor_locked = bool(market_data.get("is_floor_locked", False))
        last_order_unfilled = bool(market_data.get("last_order_unfilled", False))
        min_remaining_weight = float(market_data.get("min_remaining_weight", 0.04))
        is_near_close = bool(market_data.get("is_near_close", True))

        structure = self._coerce_structure(market_data.get("technical_structure"))
        session_candle = market_data.get("session_candle") or market_data.get("current_candle")
        daily_ma20_vol = float(
            market_data.get("daily_ma20_volume")
            or (structure.ma20_volume if structure is not None else 0.0)
            or market_data.get("ma20_volume", 0.0)
        )
        candle_eval = TechnicalStructureAnalyzer.evaluate_session_candle(session_candle, daily_ma20_vol)

        # =====================================================================
        # TẦNG 0.1: XỬ LÝ THANH KHOẢN KẸT SÀN (FLOOR LOCK / MÚA BÊN TRĂNG)
        # =====================================================================
        if is_floor_locked and last_order_unfilled:
            reason = (
                f"Thanh khoản nghẽn (Floor Lock / Múa bên trăng): {ticker_upper} trắng bên mua. "
                "Lệnh bán trước đó chưa khớp. Tạm hủy lệnh cũ để đánh giá lại ở chu kỳ tới."
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
        # CỔNG XÁC NHẬN CUỐI PHIÊN (PHƯƠNG ÁN A):
        # Tuyệt đối KHÔNG bán trên nhiễu trong phiên (09:15-14:14 ICT)!
        # =====================================================================
        if not is_near_close:
            return None

        # =====================================================================
        # TẦNG 0.2: BẪY T+2.5 (SETTLEMENT CHECK)
        # =====================================================================
        if avail_shares <= 0:
            if pnl_pct_nav <= -0.025 or pnl_pct <= -0.10:
                reason = (
                    f"Báo động T+2.5 cuối phiên: Lỗ {pnl_pct*100:.1f}% cổ phiếu ({pnl_pct_nav*100:.2f}% NAV), "
                    f"nhưng cổ phiếu chưa về khả dụng (Khả dụng: 0, Đang kẹt T+2.5: {total_shares} cp)."
                )
                logger.critical(f"!!! T+2.5 LOCKED CAPITAL STOP for {ticker_upper}: {reason} !!!")
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
        # TẦNG 1 (PHƯƠNG ÁN A): VAN BẢO HIỂM VỐN CUỐI PHIÊN CÓ XÁC NHẬN NẾN
        # Không cắt máy móc trong phiên; chỉ thoát tại >= 14:15 khi lỗ nặng
        # (>= -2.5% NAV hoặc >= -10% giá cổ phiếu) KÈM nến ngày đóng cửa yếu (không rút chân mạnh).
        # =====================================================================
        extreme_loss = (pnl_pct_nav <= -0.04) or (pnl_pct <= -0.14)
        heavy_loss = (pnl_pct_nav <= -0.025) or (pnl_pct <= -0.10)
        if extreme_loss or (heavy_loss and (not candle_eval["valid"] or not candle_eval["is_hammer_recovery"])):
            if extreme_loss or not candle_eval["valid"] or candle_eval["is_weak_close"] or candle_eval["is_red"]:
                sell_qty = self.round_hose_lot(avail_shares)
                close_info = (
                    f"nến ngày đóng cửa ở {candle_eval['close_location_pct']*100:.0f}% biên độ "
                    f"(Vol {candle_eval['daily_vol_ratio']:.2f}x MA20)"
                    if candle_eval["valid"] else "xác nhận đóng cửa cuối phiên"
                )
                reason = (
                    f"Confirmed Capital Stop (Phương án A - cuối phiên >= 14:15): "
                    f"{ticker_upper} lỗ {pnl_pct*100:.2f}% cổ phiếu ({pnl_pct_nav*100:.2f}% NAV), "
                    f"{close_info} (không có lực rút chân). Đóng 100% hàng khả dụng ({sell_qty}/{total_shares} cp)."
                )
                logger.critical(f"!!! CONFIRMED CAPITAL STOP for {ticker_upper}: {reason} !!!")
                return StopLossOrder(
                    ticker=ticker_upper,
                    quantity=sell_qty,
                    urgency="EMERGENCY",
                    reason=reason,
                    suggested_action="SELL_ALL",
                    rule_level="CONFIRMED_CAPITAL_STOP",
                )

        # =====================================================================
        # TRỤ CỘT 1 (TẦNG 3): GÃY CẤU TRÚC & HỖ TRỢ ĐỘNG (THAY LUÔN TẦNG 2 TRAILING CỨNG)
        # - Giai đoạn nền: Hỗ trợ là Swing Low 20 phiên.
        # - Khi đã có sóng lãi >= +10%: Hỗ trợ tự động nâng lên MA20 / Đáy Higher-Low (hoặc MA10/MA20 khi >= +20%).
        # - Bắt buộc có xác nhận cuối phiên (gãy sâu > 2%, hoặc Vol xả >= 1.25x MA20, hoặc dòng tiền đa phiên phân phối).
        # =====================================================================
        if structure is not None:
            active_support, support_label = structure.compute_dynamic_support(entry_price, peak_pnl_pct)
            if swing_low_override and peak_pnl_pct < 0.10:
                active_support = min(active_support, float(swing_low_override)) if active_support > 0 else float(swing_low_override)
        else:
            active_support = float(swing_low_override) if swing_low_override else 0.0
            support_label = "BASE_SWING_LOW_SUPPORT"

        if active_support > 0 and current_price < active_support:
            break_depth = (active_support - current_price) / active_support
            deep_break = break_depth >= 0.02
            vol_confirmed = candle_eval["valid"] and candle_eval["daily_vol_ratio"] >= 1.25
            flow_distributing = (
                structure is not None
                and (structure.money_flow_state == "DISTRIBUTING" or structure.stock_dist_days_10d >= 2)
            )
            weak_close_confirmed = (
                candle_eval["valid"]
                and candle_eval["is_weak_close"]
                and candle_eval["daily_vol_ratio"] >= 1.0
            )
            no_candle_fallback = (not candle_eval["valid"]) and (structure is None)

            # Nếu chỉ chớm thủng nhẹ (< 2%) với Vol cạn hoặc rút chân hồi mạnh -> KHÔNG BÁN (shakeout)
            is_dry_shakeout = (
                not deep_break
                and candle_eval["valid"]
                and (
                    candle_eval["is_hammer_recovery"]
                    or (candle_eval["daily_vol_ratio"] < 0.85 and not flow_distributing)
                )
            )
            confirmed_break = (
                deep_break
                or (not is_dry_shakeout and (vol_confirmed or flow_distributing or weak_close_confirmed or no_candle_fallback))
            )

            if confirmed_break:
                sell_qty = self.round_hose_lot(avail_shares)
                confirm_parts = []
                if deep_break:
                    confirm_parts.append(f"gãy sâu -{break_depth*100:.1f}% dưới hỗ trợ")
                if vol_confirmed:
                    confirm_parts.append(f"Vol ngày {candle_eval['daily_vol_ratio']:.2f}x MA20")
                if flow_distributing and structure is not None:
                    confirm_parts.append(
                        f"dòng tiền {structure.money_flow_state} (CMF={structure.cmf_20d:+.2f}, "
                        f"Up/Down Vol={structure.up_down_vol_ratio_10d:.2f}, {structure.stock_dist_days_10d} phiên PP)"
                    )
                if not confirm_parts:
                    confirm_parts.append("nến ngày đóng cửa yếu xác nhận phá vỡ")

                wave_note = (
                    f" [Khóa sóng lãi từ đỉnh +{peak_pnl_pct*100:.1f}%, hiện {pnl_pct*100:+.1f}%]"
                    if peak_pnl_pct >= 0.10 else f" [PnL hiện tại {pnl_pct*100:+.1f}%]"
                )
                reason = (
                    f"Structural Breakdown ({support_label} - cuối phiên >= 14:15): "
                    f"Giá đóng cửa ({current_price:,.0f}) thủng hỗ trợ cấu trúc ({active_support:,.0f}) "
                    f"kèm xác nhận: {', '.join(confirm_parts)}.{wave_note}"
                )
                logger.warning(f"!!! STRUCTURAL BREAKDOWN for {ticker_upper}: {reason} !!!")
                return StopLossOrder(
                    ticker=ticker_upper,
                    quantity=sell_qty,
                    urgency="HIGH",
                    reason=reason,
                    suggested_action="SELL_ALL",
                    rule_level="STRUCTURAL_BREAKDOWN",
                )

        # =====================================================================
        # TRỤ CỘT 2 (TẦNG 4 MỚI): PHÂN PHỐI XÁC NHẬN TẠI KHÁNG CỰ / TĂNG RƯỚN
        # Không bao giờ bán vì 1 nến 1 phút rút râu. Cần hội tụ đủ 3 trụ cột bức tranh lớn.
        # =====================================================================
        dist_reason = self.check_confirmed_distribution(
            session_candle=session_candle,
            daily_ma20_volume=daily_ma20_vol,
            structure=structure,
            current_price=current_price,
        )
        if dist_reason:
            sell_qty, action_str, swept = self._apply_partial_or_sweep(
                total_shares=total_shares,
                avail_shares=avail_shares,
                current_price=current_price,
                nav=nav,
                min_remaining_weight=min_remaining_weight,
            )
            if swept:
                dist_reason += f" Auto-Sweep đóng 100% ({sell_qty:,} cp) tránh tồn kho vụn dưới {min_remaining_weight*100:.0f}% NAV chiếm slot."
            logger.warning(f"!!! CONFIRMED DISTRIBUTION EXIT for {ticker_upper}: {dist_reason} !!!")
            return StopLossOrder(
                ticker=ticker_upper,
                quantity=sell_qty,
                urgency="HIGH",
                reason=dist_reason,
                suggested_action=action_str,
                rule_level="CONFIRMED_DISTRIBUTION",
            )

        # =====================================================================
        # TRỤ CỘT 3 (TẦNG 5): TIME STOP CÓ XÁC NHẬN DÒNG TIỀN
        # Cầm quá 50% timeline Playbook, giá tù đọng (-3%..+2%) VÀ dòng tiền không tích lũy.
        # =====================================================================
        flow_stagnant = True
        if structure is not None:
            if structure.money_flow_state == "ACCUMULATING" or (
                structure.up_down_vol_ratio_10d >= 1.05 and structure.rs_20d > 0.01
            ):
                flow_stagnant = False

        if days_held > (expected_timeline * 0.5) and -0.03 <= pnl_pct < 0.02 and flow_stagnant:
            sell_qty, action_str, swept = self._apply_partial_or_sweep(
                total_shares=total_shares,
                avail_shares=avail_shares,
                current_price=current_price,
                nav=nav,
                min_remaining_weight=min_remaining_weight,
            )
            mf_info = (
                f" (dòng tiền {structure.money_flow_state}, Up/Down Vol={structure.up_down_vol_ratio_10d:.2f})"
                if structure is not None else ""
            )
            reason = (
                f"Time Stop có xác nhận dòng tiền (Trụ cột 3): Nắm giữ {days_held} ngày "
                f"(> 50% timeline {expected_timeline} ngày) nhưng hiệu suất chỉ {pnl_pct*100:+.2f}%{mf_info}. "
                + (
                    f"Auto-Sweep đóng 100% ({sell_qty:,} cp) để giải phóng slot cho mã dẫn dắt mới."
                    if swept else f"Hạ 50% ({sell_qty:,} cp) cơ cấu vốn."
                )
            )
            logger.warning(f"TIME STOP for {ticker_upper}: {reason}")
            return StopLossOrder(
                ticker=ticker_upper,
                quantity=sell_qty,
                urgency="MEDIUM",
                reason=reason,
                suggested_action=action_str,
                rule_level="TIME_STOP",
            )

        return None


stop_loss_engine = StopLossEngine()
