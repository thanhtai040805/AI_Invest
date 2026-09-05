"""Engine 8: Decision Output Engine (IOS v5.1)

Chức năng:
- Đóng gói quyết định phân bổ vốn thành 4 nhóm dữ liệu chuẩn định chế:
    A. Portfolio Decision: Hành động chiến lược (BUY, SELL, HOLD, REBALANCE)
    B. Capital Allocation: Bản đồ 4 tầng phân bổ vốn (preliminary -> portfolio -> executable -> incremental)
    C. Portfolio Impact: Tác động lên sức chứa ngành, tương quan, đệm tiền mặt và rủi ro biên
    D. Decision Log: Nhật ký thẩm định & audit trail giải trình nguyên nhân ra quyết định
- Đồng thời tương thích ngược 100% với Agent-06 (Portfolio Risk) và Agent-08 (Trade Execution).
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class DecisionOutputEngine:
    def format_output(
        self,
        ticker: str,
        price: float,
        total_nav: float,
        conviction: str,
        eligibility_res: Any,
        prob_res: Any,
        kelly_res: Any,
        construction_res: Any,
        dynamic_res: Any,
        liquidity_res: Any,
        rebalance_res: Any,
        decision_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        d_id = decision_id or str(uuid.uuid4())
        now_str = datetime.now().isoformat()

        # 1. Nhóm A: Portfolio Decision
        # Chuẩn hóa portfolio_decision thành 4 nhóm chuẩn định chế (BUY, HOLD, REBALANCE, SELL)
        core_decision = str(rebalance_res.action).upper()
        if "HOLD" in core_decision:
            core_decision = "HOLD"
        elif "BUY" in core_decision:
            core_decision = "BUY"
        elif "SELL" in core_decision:
            core_decision = "SELL"
        elif "REBALANCE" in core_decision:
            core_decision = "REBALANCE"
        else:
            core_decision = "HOLD"

        group_a = {
            "portfolio_decision": core_decision,
            "raw_action": rebalance_res.action,
            "sub_action": rebalance_res.sub_action,
            "ticker": ticker,
            "execution_urgency": "NORMAL",
            "decision_id": d_id,
        }

        # 2. Nhóm B: Capital Allocation (4 Tầng tỷ trọng vốn cốt lõi)
        shares_for_order = abs(rebalance_res.incremental_shares)
        order_value = round(shares_for_order * price, 2)
        group_b = {
            "ticker": ticker,
            "current_weight": rebalance_res.current_weight,
            "available_shares": rebalance_res.available_shares,
            "locked_t25_shares": rebalance_res.locked_t25_shares,
            "preliminary_target": kelly_res.preliminary_target,
            "portfolio_target": construction_res.portfolio_target,
            "executable_target": rebalance_res.executable_target,
            "incremental_weight": rebalance_res.incremental_weight,
            "target_shares": rebalance_res.target_shares,
            "incremental_shares": rebalance_res.incremental_shares,
            "target_value": round(rebalance_res.executable_target * total_nav, 2),
            "order_value_vnd": order_value,
        }

        # 3. Nhóm C: Portfolio Impact
        group_c = {
            "sector": construction_res.sector,
            "sector_exposure_after": construction_res.sector_exposure_after,
            "sector_limit": construction_res.sector_limit,
            "factor_exposure": "ACCEPTABLE",
            "stress_correlation": construction_res.stress_correlation_status,
            "marginal_risk": "ACCEPTABLE" if construction_res.marginal_risk_pct < 0.01 else "MONITOR",
            "marginal_risk_pct": construction_res.marginal_risk_pct,
            "cash_before": round(dynamic_res.cash_balance / total_nav, 4) if total_nav > 0 else 0.0,
            "cash_after": dynamic_res.cash_after,
            "min_cash_target": dynamic_res.min_cash_target,
            "portfolio_risk_after": "WITHIN_LIMIT",
        }

        # 4. Nhóm D: Decision Log
        all_reasons: List[str] = []
        all_reasons.extend(eligibility_res.rejection_reasons if not eligibility_res.eligible else ["Đủ điều kiện đầu tư cơ bản"])
        all_reasons.extend(construction_res.construction_reasons)
        all_reasons.extend(dynamic_res.allocation_reasons)
        all_reasons.extend(liquidity_res.liquidity_reasons)
        all_reasons.extend(rebalance_res.rebalance_reasons)

        group_d = {
            "research": eligibility_res.research_status,
            "thesis": eligibility_res.thesis_status,
            "counter_thesis": eligibility_res.counter_thesis_status,
            "p_calibrated": prob_res.prob_win,
            "payoff_ratio": prob_res.payoff_ratio,
            "expected_edge": prob_res.expected_edge,
            "quarter_kelly_raw": kelly_res.quarter_kelly_raw,
            "deadband_passed": rebalance_res.deadband_passed,
            "reason": all_reasons,
        }

        # Tạo Rationale tổng hợp
        main_reasons = [r for r in all_reasons if r]
        rationale_text = " | ".join(main_reasons[:4]) if main_reasons else f"Quyết định {rebalance_res.action} cho mã {ticker}"

        # Đóng gói Dictionary với cả 4 nhóm và các trường phẳng tương thích ngược
        full_decision = {
            # 4 Nhóm chuẩn
            "portfolio_decision": group_a,
            "capital_allocation": group_b,
            "portfolio_impact": group_c,
            "decision_log": group_d,
            # Backward compatibility fields cho Agent-06 và Agent-08
            "decision_id": d_id,
            "timestamp": now_str,
            "ticker": ticker,
            "side": "BUY" if rebalance_res.action in ("BUY", "REBALANCE") and rebalance_res.incremental_shares > 0 else ("SELL" if rebalance_res.action in ("SELL", "REBALANCE") and rebalance_res.incremental_shares < 0 else "HOLD"),
            "action": rebalance_res.action,
            "quantity": shares_for_order,
            "target_shares": shares_for_order if shares_for_order > 0 else rebalance_res.target_shares,
            "allocated_amount_vnd": order_value,
            "allocated_weight_pct": round(abs(rebalance_res.incremental_weight) * 100.0, 2),
            "target_price": price,
            "price": price,
            "sector": construction_res.sector,
            "conviction": conviction,
            "source_p_b": prob_res.source_description,
            "rationale": rationale_text,
            "campaign": rebalance_res.campaign_info,
        }

        trace = {
            "engine_pipeline": "8-Engine Sovereign Institutional Portfolio Allocation",
            "eligibility_passed": eligibility_res.eligible,
            "p_calibrated": prob_res.prob_win,
            "payoff_ratio": prob_res.payoff_ratio,
            "quarter_kelly_raw": kelly_res.quarter_kelly_raw,
            "preliminary_target": kelly_res.preliminary_target,
            "portfolio_target": construction_res.portfolio_target,
            "executable_target": rebalance_res.executable_target,
            "incremental_weight": rebalance_res.incremental_weight,
            "deadband_passed": rebalance_res.deadband_passed,
            "execution_horizon_days": liquidity_res.execution_horizon_days,
            "market_regime_scaler": kelly_res.regime_multiplier,
            "min_cash_target": dynamic_res.min_cash_target,
        }

        return {"data": full_decision, "trace": trace}
