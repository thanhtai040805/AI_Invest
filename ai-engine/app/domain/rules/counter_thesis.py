"""Counter Thesis Engine (IOS v5.1)

Chức năng:
1. Tính toán 3-Tier Counter-Thesis Score (CTS):
   - Base CTS = Business Risk (45%) + Market Risk (35%) + Model Risk (20%)
   - ML Interaction Multiplier (Mô hình phi tuyến quét rủi ro cộng hưởng)
   - Regime Multiplier (Hệ số môi trường 6 trạng thái HMM, ưu đãi 1.1x khi có ngoại lệ Bắt đáy Capitulation)
2. Phân loại Phán quyết (Thresholds):
   - CTS 0–30:  PROCEED (Gửi nguyên bản sang Portfolio Agent)
   - CTS 31–60: CONDITIONAL (Bắt buộc kèm execution_constraints: giảm size, siết stop-loss)
   - CTS > 60:  BLOCK (Hủy Thesis kèm lý do)
   - GIL == CATASTROPHIC: CTS = 100 -> BLOCK (Zero Exception)
3. Hỗ trợ LLM Devil's Advocate để sinh phân tích định tính và lỗ hổng luận điểm.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.domain.rules.market.hmm_regime_engine import MarketRegimeV2

logger = logging.getLogger(__name__)


class Verdict(Enum):
    PROCEED = "PROCEED"
    CONDITIONAL = "CONDITIONAL"
    BLOCK = "BLOCK"
    APPROVE = "PROCEED"  # Backward compatibility
    REJECT = "BLOCK"     # Backward compatibility
    NEEDS_REVISION = "CONDITIONAL"  # Backward compatibility


@dataclass
class ExecutionConstraints:
    max_position_size_multiplier: float = 1.0
    stop_loss_pct_override: Optional[float] = None
    tranche_allocation: List[float] = field(default_factory=lambda: [1.0])
    entry_ceiling_price: Optional[float] = None
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "max_position_size_multiplier": self.max_position_size_multiplier,
            "stop_loss_pct_override": self.stop_loss_pct_override,
            "tranche_allocation": self.tranche_allocation,
            "entry_ceiling_price": self.entry_ceiling_price,
            "reason": self.reason,
        }


@dataclass
class CounterThesisReport:
    thesis_id: str
    ticker: str
    base_cts: float
    interaction_multiplier: float
    regime_multiplier: float
    final_cts: float
    verdict: Verdict
    rule_of_three_passed: bool
    is_capitulation_rebound: bool
    block_reasons: List[str]
    holes: List[str]
    execution_constraints: Optional[Dict[str, Any]]
    rationale: str


class CounterThesisEngine:
    """
    Quy tắc định lượng 3-Tier CTS & Devil's Advocate cho Agent-05.
    """

    def __init__(self, llm_client=None):
        self.llm_client = llm_client

    def calculate_base_cts(self, risk_features: Dict[str, float]) -> float:
        """
        Tính Base CTS qua 3 tầng rủi ro (0 - 100 điểm):
        - Business Risk (45%): GIL (15%), Beneish (10%), Rec_Spike (10%), Graph_RPT (10%)
        - Market Risk (35%): Macro_Headwind (15%), Liquidity_Stress (20%)
        - Model Risk (20%): Missing_Data / Staleness (20%)
        """
        gil_score = float(risk_features.get("gil_risk", 0.0))
        beneish_score = float(risk_features.get("beneish_risk", 0.0))
        rec_spike = float(risk_features.get("receivable_spike", 0.0))
        graph_rpt = float(risk_features.get("graph_rpt_risk", 0.0))

        macro_headwind = float(risk_features.get("macro_headwind", 0.0))
        liquidity_stress = float(risk_features.get("liquidity_stress", 0.0))

        missing_data = float(risk_features.get("missing_data", 0.0))

        # 1. Business Risk (45%)
        business_risk = (
            0.15 * gil_score +
            0.10 * beneish_score +
            0.10 * rec_spike +
            0.10 * graph_rpt
        )

        # 2. Market Risk (35%)
        market_risk = (
            0.15 * macro_headwind +
            0.20 * liquidity_stress
        )

        # 3. Model Risk (20%)
        model_risk = 0.20 * missing_data

        base_cts = business_risk + market_risk + model_risk
        return round(max(0.0, min(100.0, base_cts)), 2)

    def calculate_ml_interaction(self, risk_features: Dict[str, float]) -> float:
        """
        Tính hệ số cộng hưởng phi tuyến (ML Interaction Multiplier) trên HOSE:
        Quét 4 cặp rủi ro cộng hưởng chí mạng, kẹp trần tối đa 1.40x.
        """
        multiplier = 1.0

        r_rec = float(risk_features.get("receivable_spike", 0.0))
        r_liq = float(risk_features.get("liquidity_stress", 0.0))
        r_macro = float(risk_features.get("macro_headwind", 0.0))
        r_rpt = float(risk_features.get("graph_rpt_risk", 0.0))
        r_beneish = float(risk_features.get("beneish_risk", 0.0))
        r_missing = float(risk_features.get("missing_data", 0.0))

        # Cặp 1: Phải thu tăng cao + Thanh khoản cạn (Nguy cơ kẹt sàn)
        if r_rec >= 60.0 and r_liq >= 60.0:
            multiplier += 0.15

        # Cặp 2: Vĩ mô xấu + Thanh khoản cạn (Dòng tiền tháo chạy)
        if r_macro >= 60.0 and r_liq >= 60.0:
            multiplier += 0.15

        # Cặp 3: Sân sau RPT + Phải thu phình to (Rút ruột dòng tiền)
        if r_rpt >= 60.0 and r_rec >= 60.0:
            multiplier += 0.15

        # Cặp 4: Dữ liệu BCTC chậm/mù + M-Score tiệm cận ranh giới gian lận
        if r_missing >= 50.0 and r_beneish >= 50.0:
            multiplier += 0.10

        return round(min(multiplier, 1.40), 2)

    def check_capitulation_criteria(
        self,
        market_data: Dict[str, Any],
        stock_data: Dict[str, Any]
    ) -> Tuple[bool, List[str]]:
        """
        Kiểm tra 5 tiêu chí Bắt đáy Khoa học (Capitulation Rebound - Bẫy 3):
        1. Định giá chạm đáy cực đại (P/E <= 10.0 hoặc P/B <= 1.2 hoặc zscore <= -2.0)
        2. Khối lượng Hấp thụ sàn kỷ lục (Vol >= 180% MA20 và giá thoát đáy sàn)
        3. Độ rộng thị trường phục hồi (Tỷ lệ cổ phiếu > MA20 tăng vọt >= 15% hoặc phân kỳ dương)
        4. Khối ngoại / Tự doanh mua ròng đột biến trong phiên
        5. CSAD Herding đạt đỉnh hoảng loạn (> 0.015)
        """
        reasons_met = []

        # 1. Valuation Extreme
        pe = float(stock_data.get("pe_ratio", 99.0))
        pb = float(stock_data.get("pb_ratio", 99.0))
        val_zscore = float(stock_data.get("valuation_zscore", 0.0))
        if pe <= 10.0 or pb <= 1.2 or val_zscore <= -2.0:
            reasons_met.append("Định giá P/E hoặc P/B chạm mức hoảng loạn cực đại lịch sử (-2 Std Dev).")

        # 2. Absorption Volume
        cur_vol = float(stock_data.get("volume", 0.0))
        ma20_vol = float(stock_data.get("vol_ma20", 1.0))
        floor_absorbed = bool(stock_data.get("floor_absorbed", False))
        if (ma20_vol > 0 and cur_vol >= 1.8 * ma20_vol) or floor_absorbed:
            reasons_met.append("Khối lượng hấp thụ sàn (Absorption / Selling Climax) bùng nổ > 180% MA20.")

        # 3. Market Breadth Reversal
        breadth_recovery = float(market_data.get("breadth_recovery_pct", 0.0))
        if breadth_recovery >= 15.0 or bool(market_data.get("breadth_divergence", False)):
            reasons_met.append("Độ rộng thị trường phân kỳ dương / phục hồi mạnh mẽ (> 15% cổ phiếu vượt MA20).")

        # 4. Foreign / Prop Net Flow
        foreign_net = float(market_data.get("foreign_net_flow", 0.0))
        if foreign_net > 0:
            reasons_met.append("Dòng tiền lớn (Khối ngoại / Tự doanh) giải ngân mua ròng đột biến.")

        # 5. CSAD Herding
        csad_score = float(market_data.get("csad_score", 0.0))
        if csad_score >= 0.015:
            reasons_met.append("Chỉ số tâm lý bầy đàn CSAD đạt đỉnh hoảng loạn cực đại.")

        # Bắt buộc đạt tối thiểu 3/5 điều kiện để kích hoạt Capitulation Exception
        is_capitulation = len(reasons_met) >= 3
        return is_capitulation, reasons_met

    def get_regime_multiplier(self, regime_name: str, is_capitulation: bool = False) -> float:
        """Lấy hệ số môi trường theo 6 trạng thái HMM, có ưu đãi bắt đáy."""
        return MarketRegimeV2.get_multiplier(regime_name, is_capitulation=is_capitulation)

    def determine_verdict_and_constraints(
        self,
        final_cts: float,
        gil_flag: str,
        current_price: float,
        is_capitulation: bool = False
    ) -> Tuple[Verdict, List[str], Optional[ExecutionConstraints]]:
        """Phân loại phán quyết cuối cùng và sinh ràng buộc thực thi."""
        block_reasons = []

        # Hard Law: GIL CATASTROPHIC hoặc DATA_ERROR (Mục Failure Modes IOS v5.1)
        gil_clean = str(gil_flag).upper().strip()
        if gil_clean == "CATASTROPHIC":
            block_reasons.append("Hard Law Veto: Phát hiện rủi ro sở hữu chéo và kiệt quệ tài chính GIL CATASTROPHIC.")
            return Verdict.BLOCK, block_reasons, None
        elif gil_clean == "DATA_ERROR":
            block_reasons.append("Hard Law Veto: Lỗi dữ liệu đồ thị sở hữu chéo (GIL) từ SAG Backend -> Default BLOCK theo Hiến pháp IOS v5.1.")
            return Verdict.BLOCK, block_reasons, None

        if final_cts > 60.0:
            block_reasons.append(f"Điểm Final CTS ({final_cts:.1f}) vượt ngưỡng an toàn (> 60.0). Rủi ro tổng hợp quá cao.")
            return Verdict.BLOCK, block_reasons, None

        elif final_cts >= 31.0:
            # CONDITIONAL
            if is_capitulation:
                constraints = ExecutionConstraints(
                    max_position_size_multiplier=0.4,
                    stop_loss_pct_override=0.06,
                    tranche_allocation=[0.3, 0.3, 0.4],
                    entry_ceiling_price=round(current_price * 1.03, 0) if current_price > 0 else None,
                    reason="Ngoại lệ Bắt đáy Capitulation: Vốn 100% tiền tươi, giải ngân 3 đợt (30%-30%-40%), siết Stop-loss."
                )
            else:
                constraints = ExecutionConstraints(
                    max_position_size_multiplier=0.5,
                    stop_loss_pct_override=0.05,
                    tranche_allocation=[0.5, 0.5],
                    entry_ceiling_price=round(current_price * 1.02, 0) if current_price > 0 else None,
                    reason=f"Phán quyết CONDITIONAL (CTS={final_cts:.1f}): Ép giảm 50% quy mô vị thế, siết stop-loss cứng 5%."
                )
            return Verdict.CONDITIONAL, block_reasons, constraints

        else:
            # PROCEED (CTS 0 - 30)
            return Verdict.PROCEED, block_reasons, None

    async def analyze_thesis_with_llm(
        self,
        ticker: str,
        thesis_payload: Dict[str, Any],
        signals: List[str]
    ) -> Tuple[bool, float, bool, List[str], str]:
        """
        LLM Semantic Inquisitor:
        1. Kiểm tra tính độc lập thực sự của 3 tín hiệu (Semantic Independence).
        2. Chấm điểm rủi ro ngữ cảnh ẩn (Blind-spot Risk: 0 - 20 điểm).
        3. Tìm lỗ hổng logic chết người (Fatal Logic Flaw).
        
        Trả về: (is_truly_independent, blindspot_penalty, fatal_flaw, holes, rationale)
        """
        holes = []
        rationale = "Đạt yêu cầu phản biện định tính từ LLM."

        if not self.llm_client:
            return True, 0.0, False, holes, rationale

        thesis_body = thesis_payload.get("thesis_body", {})
        prompt = f"""
        Bạn là Chuyên gia Phản biện Đầu tư (Devil's Advocate) khắt khe bậc nhất sàn HOSE.
        Nhiệm vụ:
        1. Kiểm tra các tín hiệu hỗ trợ có thực sự ĐỘC LẬP về mặt bản chất không hay chỉ là 1 sự kiện được viết lại nhiều lần?
        2. Tìm các rủi ro ngữ cảnh bị bỏ sót (tiến độ dự án, pháp lý, rủi ro khách hàng, xung đột lợi ích ban lãnh đạo).
        3. Chấm điểm phạt rủi ro ngữ cảnh ẩn (blindspot_penalty) từ 0 đến 20 điểm (0: không có rủi ro ẩn, 20: rủi ro ngữ cảnh nghiêm trọng).
        4. Có lỗ hổng logic chết người (fatal_flaw) khiến toàn bộ luận điểm sụp đổ không?
        
        Cổ phiếu: {ticker}
        Luận điểm: {json.dumps(thesis_body, ensure_ascii=False)}
        Tín hiệu hỗ trợ: {', '.join(signals)}
        
        Trả về kết quả dưới dạng JSON chuẩn:
        {{
            "is_truly_independent": true,
            "blindspot_penalty": 5.0,
            "fatal_flaw": false,
            "holes": ["Lỗ hổng 1", "Lỗ hổng 2"],
            "rationale": "Phân tích phản biện chi tiết"
        }}
        """
        try:
            resp = await self.llm_client.chat(prompt)
            data = json.loads(resp)
            is_indep = bool(data.get("is_truly_independent", True))
            penalty = float(data.get("blindspot_penalty", 0.0))
            fatal_flaw = bool(data.get("fatal_flaw", False))
            holes = list(data.get("holes", []))
            rationale = str(data.get("rationale", rationale))
            return is_indep, penalty, fatal_flaw, holes, rationale
        except Exception as e:
            logger.warning(f"LLM Devil's Advocate error: {e}")
            return True, 0.0, False, holes, rationale

    async def evaluate_counter_thesis(
        self,
        ticker: str,
        thesis_payload: Dict[str, Any],
        risk_features: Dict[str, float],
        market_data: Dict[str, Any],
        stock_data: Dict[str, Any],
    ) -> CounterThesisReport:
        """
        Thực hiện toàn bộ quy trình phản biện 3-Tier CTS kết hợp LLM Semantic Inquisitor.
        """
        ticker_clean = str(ticker).upper().strip()
        thesis_id = str(thesis_payload.get("thesis_id", f"THESIS_{ticker_clean}"))
        
        # Trích xuất và xác thực tín hiệu độc lập (Hỗ trợ dict, list, input_validation, metadata)
        raw_signals = (
            thesis_payload.get("confirming_signals")
            or thesis_payload.get("input_validation", {}).get("independent_signals")
            or thesis_payload.get("metadata", {}).get("independent_signals")
            or []
        )
        
        passed_signals: List[str] = []
        if isinstance(raw_signals, dict):
            for k, v in raw_signals.items():
                v_str = str(v).strip()
                if v_str.upper().startswith("PASS") or v_str.upper().startswith("TRUE") or "PASS" in v_str.upper():
                    passed_signals.append(f"{k}: {v_str}")
                elif not (v_str.upper().startswith("FAIL") or v_str.upper().startswith("FALSE")):
                    passed_signals.append(f"{k}: {v_str}")
        elif isinstance(raw_signals, list):
            for s in raw_signals:
                s_str = str(s).strip()
                if not (s_str.upper().startswith("FAIL") or s_str.upper().startswith("FALSE")):
                    passed_signals.append(s_str)

        signals = passed_signals

        # 1. Gọi LLM Devil's Advocate để thẩm định ngữ cảnh & tính độc lập thực sự
        llm_indep, llm_blindspot_penalty, fatal_flaw, llm_holes, llm_rationale = await self.analyze_thesis_with_llm(
            ticker=ticker_clean,
            thesis_payload=thesis_payload,
            signals=signals
        )

        # 2. Kiểm tra Hard Law Điều 3 (Rule of Three) kết hợp LLM
        unique_signals = len(set(signals))
        rule_of_three_passed = (unique_signals >= 3) and llm_indep

        # 3. Kiểm tra cờ GIL
        gil_status = str(risk_features.get("gil_status") or thesis_payload.get("input_validation", {}).get("gil_status", "PASS")).upper().strip()
        if gil_status in ["CATASTROPHIC", "DATA_ERROR"]:
            risk_features["gil_risk"] = 100.0

        # 4. Kiểm tra ngoại lệ Bắt đáy Capitulation (Bẫy 3)
        is_capitulation, capitulation_reasons = self.check_capitulation_criteria(market_data, stock_data)

        # 5. Tính toán 3-Tier CTS (Có tích hợp điểm phạt ngữ cảnh LLM Blind-spot)
        base_quant_cts = self.calculate_base_cts(risk_features)
        base_cts = min(100.0, round(base_quant_cts + llm_blindspot_penalty, 2))

        interaction_multiplier = self.calculate_ml_interaction(risk_features)
        regime_label = str(market_data.get("current_regime", "BULL_TRENDING"))
        regime_multiplier = self.get_regime_multiplier(regime_label, is_capitulation=is_capitulation)

        # Tính Final CTS
        if gil_status in ["CATASTROPHIC", "DATA_ERROR"] or fatal_flaw:
            final_cts = 100.0
        elif not rule_of_three_passed:
            final_cts = max(65.0, base_cts * interaction_multiplier * regime_multiplier)
        else:
            final_cts = round(base_cts * interaction_multiplier * regime_multiplier, 1)

        # 6. Phân loại Phán quyết và Constraints
        current_price = float(stock_data.get("current_price", 0.0))
        verdict, block_reasons, constraints = self.determine_verdict_and_constraints(
            final_cts=final_cts,
            gil_flag=gil_status,
            current_price=current_price,
            is_capitulation=is_capitulation
        )

        if not rule_of_three_passed:
            verdict = Verdict.BLOCK
            reason = "Vi phạm Hard Law Điều 3: Không đủ 3 tín hiệu độc lập." if unique_signals < 3 else "Vi phạm Hard Law Điều 3: LLM phát hiện các tín hiệu bị trùng lặp ngữ nghĩa (không độc lập)."
            block_reasons.append(reason)

        if fatal_flaw:
            verdict = Verdict.BLOCK
            block_reasons.append("Phủ quyết bởi LLM Devil's Advocate: Phát hiện lỗ hổng logic chết người (Fatal Flaw).")

        all_holes = block_reasons + llm_holes
        if is_capitulation:
            all_holes.append(f"Capitulation Trigger: {'; '.join(capitulation_reasons)}")

        constraints_dict = constraints.to_dict() if constraints else None

        return CounterThesisReport(
            thesis_id=thesis_id,
            ticker=ticker_clean,
            base_cts=base_cts,
            interaction_multiplier=interaction_multiplier,
            regime_multiplier=regime_multiplier,
            final_cts=final_cts,
            verdict=verdict,
            rule_of_three_passed=rule_of_three_passed,
            is_capitulation_rebound=is_capitulation,
            block_reasons=block_reasons,
            holes=all_holes,
            execution_constraints=constraints_dict,
            rationale=llm_rationale if verdict != Verdict.BLOCK else f"Bị chặn bởi Counter Thesis Agent. Lý do: {'; '.join(block_reasons)}",
        )


counter_thesis_engine = CounterThesisEngine()
