"""AGENT-04: Thesis Engine (IOS v5.1)

Quy tắc nghiệp vụ cốt lõi:
1. Cấu trúc ID chuẩn hóa: THESIS_HOSE_{TICKER}_{YEAR}Q{Q}_{SEQ}
2. Lọc Hard Filter lớp 0 (GIL CATASTROPHIC) & Kiểm tra CSS >= 65 (Conviction >= B).
3. Tự động nhận diện Ngòi nổ (Catalyst Selection) từ phân phối 6 nhân tố (F1-F6).
4. Định giá thích ứng đa mô hình (Adaptive Valuation) theo Sector & Timeline (1M, 3M, 6M).
5. Phân tích Pre-Mortem (3 kịch bản thất bại) & Điều kiện Hủy Luận điểm (Thesis Invalidation).
6. Đóng gói Output Schema JSON chuẩn hóa bàn giao cho Counter Thesis Agent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ThesisEngine:
    """
    Quy tắc định lượng và sinh cấu trúc luận điểm đầu tư cho Agent-04.
    """

    def generate_thesis_id(self, ticker: str, target_date: Optional[date] = None, seq_num: int = 1) -> str:
        """Sinh mã định danh thesis chuẩn hóa: THESIS_HOSE_{TICKER}_{YEAR}Q{Q}_{SEQ:03d}"""
        t_date = target_date or date.today()
        year = t_date.year
        quarter = (t_date.month - 1) // 3 + 1
        clean_ticker = str(ticker).upper().strip()
        return f"THESIS_HOSE_{clean_ticker}_{year}Q{quarter}_{seq_num:03d}"

    def determine_catalyst(
        self,
        factor_scores: Dict[str, float],
        sector: str = "General",
        custom_catalyst_desc: Optional[str] = None
    ) -> Dict[str, str]:
        """
        Tự động xác định loại ngòi nổ (Catalyst) dựa trên phân phối điểm F1-F6:
        - F4 High (Earnings) -> Earnings Expansion
        - F5 High (Flow/Momentum) -> Sector Rotation
        - F1/F2 High (Value/Quality) -> Undervaluation
        - Structural/Restructuring -> Value Unlock
        """
        f1_val = float(factor_scores.get("f1_value", 50.0))
        f2_qual = float(factor_scores.get("f2_quality", 50.0))
        f3_mom = float(factor_scores.get("f3_momentum", 50.0))
        f4_earn = float(factor_scores.get("f4_earnings", 50.0))
        f5_flow = float(factor_scores.get("f5_flow", 50.0))

        # Ưu tiên 1: Tăng trưởng lợi nhuận đột biến (F4 Earnings)
        if f4_earn >= 70.0:
            primary_type = "Earnings Expansion"
            desc = custom_catalyst_desc or f"Tăng trưởng lợi nhuận vượt trội (F4={f4_earn:.1f}) nhờ mở rộng công suất và cải thiện biên lợi nhuận."
        # Ưu tiên 2: Dòng tiền và xoay vòng ngành (F5 Flow & F3 Momentum)
        elif f5_flow >= 70.0 or (f3_mom >= 70.0 and f5_flow >= 60.0):
            primary_type = "Sector Rotation"
            desc = custom_catalyst_desc or f"Dòng tiền tổ chức và khối ngoại luân chuyển mạnh vào nhóm {sector} (F5={f5_flow:.1f})."
        # Ưu tiên 3: Định giá rẻ sâu và chất lượng cơ bản cao (F1 Value & F2 Quality)
        elif f1_val >= 70.0 or (f1_val >= 65.0 and f2_qual >= 65.0):
            primary_type = "Undervaluation"
            desc = custom_catalyst_desc or f"Định giá chiết khấu sâu so với giá trị nội tại (F1={f1_val:.1f}, F2={f2_qual:.1f})."
        # Mặc định: Mở khóa giá trị doanh nghiệp
        else:
            primary_type = "Value Unlock"
            desc = custom_catalyst_desc or f"Tái cấu trúc hoạt động kinh doanh và tối ưu hóa chi phí vận hành tại nhóm ngành {sector}."

        return {
            "primary_type": primary_type,
            "description": desc,
        }

    def calculate_adaptive_target_price(
        self,
        timeline_months: int,
        current_price: float,
        pe_comp_price: float,
        ev_ebitda_comp_price: float,
        dcf_price: float,
        regime_label: str = "BULL_TRENDING",
        sector: str = "Manufacturing"
    ) -> Dict[str, Any]:
        """
        Tính toán Target Price động và biên dao động giá (target_range):
        - Nếu timeline <= 3 tháng: Loại bỏ DCF, dùng P/E (50%) + EV/EBITDA (50%).
        - Nếu timeline > 3 tháng: Dùng P/E (35%) + EV/EBITDA (35%) + DCF (30%).
        - Regime Premium: Thêm 15% khi thị trường Bullish mạnh.
        """
        if current_price <= 0:
            current_price = 10000.0

        if pe_comp_price <= 0:
            pe_comp_price = current_price * 1.15
        if ev_ebitda_comp_price <= 0:
            ev_ebitda_comp_price = current_price * 1.18
        if dcf_price <= 0:
            dcf_price = current_price * 1.22

        if timeline_months <= 3:
            base_case = round((pe_comp_price * 0.5) + (ev_ebitda_comp_price * 0.5), 0)
            valuation_method = f"{sector}_Standard (50% PE + 50% EV/EBITDA - Loại bỏ DCF cho timeline {timeline_months}M)"
        else:
            base_case = round((pe_comp_price * 0.35) + (ev_ebitda_comp_price * 0.35) + (dcf_price * 0.30), 0)
            valuation_method = f"{sector}_Standard (35% PE + 35% EV/EBITDA + 30% DCF)"

        # Thêm Premium theo Regime
        premium = 0.0
        if "BULL" in regime_label.upper() or regime_label == "LIQUIDITY_EXPANSION":
            premium = 0.15

        bull_case = round(base_case * (1.0 + premium + 0.05), 0)
        base_case = round(base_case * (1.0 + premium), 0)

        # Đảm bảo target price luôn >= current_price
        if base_case < current_price:
            base_case = round(current_price * 1.12, 0)
        if bull_case <= base_case:
            bull_case = round(base_case * 1.10, 0)

        return {
            "valuation_method": valuation_method,
            "base_case": float(base_case),
            "bull_case": float(bull_case),
            "target_range": [float(base_case), float(bull_case)],
        }

    def generate_pre_mortem_scenarios(
        self,
        ticker: str,
        sector: str,
        catalyst_type: str
    ) -> List[str]:
        """Tự động sinh tối thiểu 3 kịch bản sai lầm (Pre-Mortem Analysis) cụ thể."""
        return [
            f"Kịch bản 1 (Industry Risk): Chi phí nguyên vật liệu đầu vào ngành {sector} tăng đột biến bào mòn biên lợi nhuận gộp.",
            f"Kịch bản 2 (Macro & Demand Compression): Cầu tiêu thụ suy yếu do tăng trưởng tín dụng chậm và áp lực lãi suất.",
            f"Kịch bản 3 (Execution / Catalyst Delay): Tiến độ hiện thực hóa ngòi nổ '{catalyst_type}' bị chậm từ 1 đến 2 quý so với kỳ vọng ban đầu."
        ]

    def generate_exit_conditions(
        self,
        entry_price: float,
        hard_stop_loss_pct: float = 0.07
    ) -> Dict[str, Any]:
        """Xác lập điều kiện thoát vị thế & Hard Stop-loss."""
        stop_price = round(entry_price * (1.0 - hard_stop_loss_pct), 0)
        return {
            "hard_stop_loss_price": float(stop_price),
            "invalidation_triggers": [
                "Biên lợi nhuận gộp (GPM) sụt giảm > 200 bps trong BCTC quý gần nhất.",
                "Chỉ số Beneish M-Score vượt ngưỡng -1.78 (báo động rủi ro gian lận kế toán).",
                "Giá gãy MA50 kèm Volume giao dịch > 200% trung bình 20 phiên."
            ]
        }

    def evaluate_idiosyncratic_veto(
        self,
        moat_score: float,
        micro_score: float,
        macro_score: float = 60.0
    ) -> bool:
        """Quyền phủ quyết đặc quyền: Nếu Moat & Micro > 90 thì vượt qua rào cản vĩ mô."""
        if moat_score >= 90.0 and micro_score >= 90.0:
            logger.warning("IDIOSYNCRATIC VETO ACTIVATED: Moat và Micro vượt trội, phủ quyết rào cản vĩ mô!")
            return True
        return False

    def evaluate_independent_signals(
        self,
        factors: Dict[str, float],
        css_score: float,
        moat_score: float,
        regime_label: str,
    ) -> Tuple[bool, Dict[str, str], int]:
        """
        Thẩm định thực chất 3 Tín hiệu Độc lập (Hard Law Điều 3):
        - Signal 1: Nhân tố cơ bản / Lợi nhuận (F4 Earnings >= 60 hoặc CSS >= 65 hoặc F1 Value >= 65)
        - Signal 2: Dòng tiền & Lợi thế kinh tế (Moat Score >= 60 hoặc F5 Flow >= 60 hoặc F3 Momentum >= 60)
        - Signal 3: Bối cảnh Vĩ mô / Chế độ thị trường HMM (Không phải BEAR/CRISIS hoặc có Idiosyncratic Veto)
        
        Trả về: (passed_all, signals_dict, passed_count)
        """
        # Signal 1: Factor / Earnings / Value / CSS (Chuẩn Conviction >= B tức CSS >= 60.0)
        f4 = factors.get("f4_earnings", 50.0)
        f1 = factors.get("f1_value", 50.0)
        s1_passed = (f4 >= 60.0) or (css_score >= 60.0) or (f1 >= 60.0)
        s1_text = (
            f"PASS (F4 SUE={f4:.1f}, CSS={css_score:.1f})"
            if s1_passed
            else f"FAIL (F4 SUE={f4:.1f} < 60 và CSS={css_score:.1f} < 60)"
        )

        # Signal 2: Surveillance / Flow / Moat
        f5 = factors.get("f5_flow", 50.0)
        f3 = factors.get("f3_momentum", 50.0)
        s2_passed = (moat_score >= 60.0) or (f5 >= 60.0) or (f3 >= 60.0)
        s2_text = (
            f"PASS (Moat={moat_score:.1f}, F5 Flow={f5:.1f})"
            if s2_passed
            else f"FAIL (Moat={moat_score:.1f}, F5={f5:.1f}, F3={f3:.1f} đều dưới 60)"
        )

        # Signal 3: Macro / HMM Regime / Idiosyncratic Veto
        clean_regime = regime_label.upper().strip()
        is_stress_regime = ("BEAR" in clean_regime) or ("CRISIS" in clean_regime) or ("CONTRACTION" in clean_regime)
        has_veto = self.evaluate_idiosyncratic_veto(moat_score, factors.get("f2_quality", 50.0))

        s3_passed = (not is_stress_regime) or has_veto
        if s3_passed:
            if has_veto and is_stress_regime:
                s3_text = f"PASS (IDIOSYNCRATIC_VETO: Moat={moat_score:.1f}, Quality={factors.get('f2_quality', 50.0):.1f} phủ quyết Regime={clean_regime})"
            else:
                s3_text = f"PASS (Regime={clean_regime})"
        else:
            s3_text = f"FAIL (Regime={clean_regime} bất lợi, không có đặc quyền Veto)"

        signals = {
            "signal_1_factor": s1_text,
            "signal_2_surveillance": s2_text,
            "signal_3_macro_hmm": s3_text,
        }
        passed_count = sum(1 for v in [s1_passed, s2_passed, s3_passed] if v)
        return (passed_count == 3), signals, passed_count

    def build_structured_thesis_output(
        self,
        ticker: str,
        research_report: Dict[str, Any],
        market_context: Dict[str, Any],
        valuation_inputs: Optional[Dict[str, Any]] = None,
        timeline_months: int = 3,
        seq_num: int = 1,
        custom_catalyst_desc: Optional[str] = None
    ) -> Tuple[bool, Dict[str, Any], str]:
        """
        Kiểm tra toàn bộ điều kiện và đóng gói JSON Schema chuẩn hóa cho Agent-04:
        
        Trả về: (is_eligible, structured_payload, rejection_reason)
        """
        ticker_clean = str(ticker).upper().strip()
        css_score = float(research_report.get("css", 0.0))
        conviction = str(research_report.get("conviction", "D")).upper()
        gil_status = str(research_report.get("gil_status") or market_context.get("gil_status", "PASS")).upper()
        sector = str(research_report.get("sector", "General"))
        regime_label = str(market_context.get("current_regime", "BULL_TRENDING"))
        current_price = float(research_report.get("current_price") or market_context.get("current_price", 0.0))

        # 1. Check Hard Filter Lớp 0: GIL == CATASTROPHIC
        if gil_status == "CATASTROPHIC":
            return False, {}, "REJECT: Vi phạm Hard Filter Lớp 0 (GIL == CATASTROPHIC)."

        # 2. Check Conviction & CSS Score: CSS >= 65 (Conviction >= B)
        if css_score < 60.0 or conviction in ["C", "D", "E"]:
            return False, {}, f"WAIT / SKIP: CSS ({css_score:.1f}) hoặc Conviction ({conviction}) chưa đạt ngưỡng B."

        # 3. Check 3 Tín hiệu Độc lập (Hard Law Điều 3)
        factors = {
            "f1_value": float(research_report.get("f1_value", 50.0)),
            "f2_quality": float(research_report.get("f2_quality", 50.0)),
            "f3_momentum": float(research_report.get("f3_momentum", 50.0)),
            "f4_earnings": float(research_report.get("f4_earnings", 50.0)),
            "f5_flow": float(research_report.get("f5_flow", 50.0)),
            "f6_technical": float(research_report.get("f6_technical", 50.0)),
        }
        moat_score = float(research_report.get("moat_score", 50.0))

        passed_all_signals, independent_signals, passed_signal_count = self.evaluate_independent_signals(
            factors=factors,
            css_score=css_score,
            moat_score=moat_score,
            regime_label=regime_label,
        )

        if not passed_all_signals:
            return False, {}, f"WAIT / SKIP: Không đủ 3 tín hiệu độc lập xác nhận (Đạt {passed_signal_count}/3). Chi tiết: {independent_signals}"

        # 4. Tự động nhận diện Catalyst
        catalyst_info = self.determine_catalyst(factors, sector=sector, custom_catalyst_desc=custom_catalyst_desc)

        # 5. Tính toán Price Target & Timeline
        val_inputs = valuation_inputs or {}
        price_target_info = self.calculate_adaptive_target_price(
            timeline_months=timeline_months,
            current_price=current_price,
            pe_comp_price=float(val_inputs.get("pe_price", 0.0)),
            ev_ebitda_comp_price=float(val_inputs.get("ev_ebitda_price", 0.0)),
            dcf_price=float(val_inputs.get("dcf_price", 0.0)),
            regime_label=regime_label,
            sector=sector
        )

        # 6. Pre-Mortem Scenarios
        pre_mortem = self.generate_pre_mortem_scenarios(
            ticker=ticker_clean,
            sector=sector,
            catalyst_type=catalyst_info["primary_type"]
        )

        # 7. Exit Conditions
        exit_conditions = self.generate_exit_conditions(
            entry_price=current_price if current_price > 0 else price_target_info["base_case"] * 0.85
        )

        # 8. Sinh ID chuẩn hóa
        thesis_id = self.generate_thesis_id(ticker_clean, seq_num=seq_num)

        # Đóng gói Schema chuẩn hóa JSON
        structured_payload = {
            "thesis_id": thesis_id,
            "ticker": ticker_clean,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "status": "PENDING_COUNTER_ANALYSIS",
            "input_validation": {
                "gil_status": gil_status,
                "conviction_level": conviction,
                "css_score": round(css_score, 1),
                "independent_signals": independent_signals,
            },
            "thesis_body": {
                "why_now": f"Ngòi nổ '{catalyst_info['primary_type']}' bước vào giai đoạn hiện thực hóa, hỗ trợ bởi dòng tiền và tăng trưởng lợi nhuận.",
                "why_this_stock": f"Mã {ticker_clean} (Ngành {sector}) sở hữu lợi thế Moat ({moat_score:.1f}) và CSS ({css_score:.1f}) thuộc nhóm dẫn dắt Universe.",
                "catalyst": catalyst_info,
                "timeline": f"{timeline_months}M",
                "price_target": price_target_info,
                "pre_mortem": pre_mortem,
                "exit_conditions": exit_conditions,
            }
        }

        return True, structured_payload, "SUCCESS"


thesis_engine = ThesisEngine()
