"""AGENT-06: Portfolio Risk Agent (Vietnamized Institutional Risk Engine — vNext)
HOSE Spot Equity Sovereign Pre-Trade Risk Gateway (100% Cổ phiếu cơ sở — Không phái sinh).

Chức năng & Trách nhiệm:
- Nắm quyền phủ quyết tối cao (Sovereign Gatekeeper) trước khi lệnh được bắn ra thị trường.
- 5 Lớp thẩm định độc lập:
    1. Lớp 1: Hard Laws thể chế (Single Stock <= 15%, Sector <= 35%, ADTV <= 25%, T+2.5 Loss <= 2% NAV).
    2. Lớp 2: Quản trị rủi ro kẹt hàng T+2.5 (Locked Exposure <= 35% NAV, Đệm rủi ro 2 cây sàn -13.51%).
    3. Lớp 3: Cảm biến Dị thường Giá & Khối lượng (Tape Anomaly VSA: Churning, Upthrust, Breakdown).
    4. Lớp 4: Đo lường Tail Risk & Drawdown Protocol (EGARCH-t Student-t, Hist ES, De-risk nhanh / Re-risk chậm).
    5. Lớp 5: Giám sát Suy thoái Mô hình (CDC Tiers: IC Decay + Persistence >= 5 phiên + Slippage Spike).
- Quyết định thể chế trả về: PASS, REDUCE, BLOCK, INCREASE CASH, FREEZE NEW RISK, ACTIVATE CDC.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.rules.hard_laws import HardLawEngine, ProposedOrder, PortfolioState, HardLawCheck
from app.domain.rules.risk.tape_anomaly_detector import (
    TapeAnomalyDetector,
    TapeAnomalyResult,
    TapeAnomalySeverity,
    AnomalyType,
)
from app.domain.rules.risk.t25_exposure_manager import T25ExposureManager, T25CapacityCheck
from app.domain.rules.risk.breadth_risk_engine import BreadthRiskEngine, BreadthRiskEvaluation, BreadthHealthTier
from app.domain.rules.risk.tail_risk_engine import TailRiskEngine, TailRiskSnapshot
from app.domain.rules.risk.drawdown_recovery_protocol import (
    DrawdownRecoveryProtocol,
    DrawdownEvaluation,
    DrawdownTier,
)
from app.domain.rules.risk.cdc_controller import CDCController, CDCEvaluation, CDCTier

logger = logging.getLogger(__name__)


class PortfolioRiskAgent(BaseAgent):
    """
    AGENT-06: Chuyên viên Quản trị Rủi ro Danh mục Định chế (Chief Risk Officer Engine).
    Cổng kiểm soát rủi ro thực thi (Pre-trade Risk Gateway) cho 100% cổ phiếu cơ sở sàn HOSE.
    """

    def __init__(self):
        super().__init__(
            agent_name="portfolio_risk",
            state_tables=["risk_snapshots", "risk_limits"],
            log_table="log_portfolio_risk",
            enabled=True,
        )
        self.hard_law_engine = HardLawEngine()
        self.tape_anomaly_detector = TapeAnomalyDetector()
        self.t25_manager = T25ExposureManager()
        self.breadth_engine = BreadthRiskEngine()
        self.tail_risk_engine = TailRiskEngine()
        self.drawdown_protocol = DrawdownRecoveryProtocol()
        self.cdc_controller = CDCController()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thẩm định rủi ro thể chế và phê duyệt/điều chỉnh/hủy lệnh đề xuất:
        - event_data:
            - portfolio: {total_nav, peak_nav, cash_vnd, positions, sector_exposure, locked_t25_value, returns_series}
            - proposed_order: {ticker, side, quantity/target_shares, price, stop_loss_price, sector, adtv20, candle, ma20_volume} (tùy chọn)
            - market_context: {distribution_days, breadth_ma20_pct, vnindex_change_pct, market_beta}
            - model_risk: {ic_decay_pct, persistence_sessions, actual_slippage_pct}
        """
        portfolio = event_data.get("portfolio", {})
        nav = float(portfolio.get("total_nav", 1000000000.0))
        peak_nav = float(portfolio.get("peak_nav", nav))
        positions = portfolio.get("positions", {})
        sector_exposure = portfolio.get("sector_exposure", {})
        locked_t25_value = float(portfolio.get("locked_t25_value", 0.0))
        returns_series = portfolio.get("returns_series", [])

        market_ctx = event_data.get("market_context", {})
        distribution_days = int(market_ctx.get("distribution_days", 0))
        breadth_ma20_pct = float(market_ctx.get("breadth_ma20_pct", 55.0))
        vnindex_change_pct = float(market_ctx.get("vnindex_change_pct", 0.0))
        market_beta = float(market_ctx.get("market_beta", 1.10))

        model_risk = event_data.get("model_risk", {})
        ic_decay_pct = float(model_risk.get("ic_decay_pct", 0.0))
        if "cdc_status" in event_data and event_data["cdc_status"]:
            ic_decay_pct = max(ic_decay_pct, 0.55)  # Hỗ trợ backward compatibility
        persistence_sessions = int(model_risk.get("persistence_sessions", 0))
        actual_slippage_pct = float(model_risk.get("actual_slippage_pct", 0.003))

        observation_days = int(event_data.get("observation_days_below_threshold", 2))

        # =========================================================================
        # 1. LỚP ĐO LƯỜNG TỔNG THỂ (TAIL RISK, BREADTH, DRAWDOWN, CDC)
        # =========================================================================
        tail_snapshot: TailRiskSnapshot = self.tail_risk_engine.evaluate_tail_risk(
            returns_series=returns_series,
            portfolio_positions=positions,
            market_beta=market_beta,
        )

        breadth_eval: BreadthRiskEvaluation = self.breadth_engine.evaluate_market_breadth(
            distribution_days=distribution_days,
            breadth_ma20_pct=breadth_ma20_pct,
            vnindex_change_pct=vnindex_change_pct,
        )

        dd_eval: DrawdownEvaluation = self.drawdown_protocol.evaluate_drawdown(
            current_nav=nav,
            peak_nav=peak_nav,
            observation_days_below_threshold=observation_days,
            tail_risk_safe=(tail_snapshot.tail_risk_verdict == "SAFE"),
            breadth_healthy=(breadth_eval.health_tier == BreadthHealthTier.HEALTHY),
        )

        cdc_eval: CDCEvaluation = self.cdc_controller.evaluate_model_health(
            ic_decay_pct=ic_decay_pct,
            persistence_sessions=persistence_sessions,
            actual_slippage_pct=actual_slippage_pct,
        )

        # =========================================================================
        # 2. KIỂM ĐỊNH LỆNH ĐỀ XUẤT (NẾU CÓ PROPOSED ORDER)
        # =========================================================================
        proposed_order_raw = event_data.get("proposed_order")
        if not proposed_order_raw and "candidate" in event_data:
            proposed_order_raw = event_data["candidate"]

        action = "PASS"
        approved_shares = 0
        original_shares = 0
        order_ticker = "UNKNOWN"
        order_price = 0.0
        reasons_list: List[str] = []

        hard_law_status_map = {
            "single_stock": "PASS",
            "sector": "PASS",
            "position_risk": "PASS",
            "liquidity_limit": "PASS",
            "t25_capacity": "PASS",
            "all_passed": True,
        }

        tape_anomaly_map = {
            "detected": False,
            "anomaly_type": "NONE",
            "severity": "NONE",
            "reason": "Chưa có dữ liệu nến hoặc không phát hiện dị thường.",
        }

        if proposed_order_raw:
            order_ticker = str(proposed_order_raw.get("ticker", "UNKNOWN")).upper().strip()
            order_price = float(proposed_order_raw.get("price", proposed_order_raw.get("target_price", 0.0)))
            original_shares = int(proposed_order_raw.get("quantity", proposed_order_raw.get("target_shares", 0)))
            stop_loss_p = proposed_order_raw.get("stop_loss_price")
            if stop_loss_p is not None:
                stop_loss_p = float(stop_loss_p)
            else:
                stop_loss_p = order_price * 0.93 if order_price > 0 else None  # Default 7% stop loss
            order_sector = proposed_order_raw.get("sector", "Unknown")
            adtv20 = float(proposed_order_raw.get("adtv20", 2000000.0))

            p_state = PortfolioState(
                nav=nav,
                positions=positions,
                sector_exposure=sector_exposure,
                locked_t25_value=locked_t25_value,
            )
            p_order = ProposedOrder(
                ticker=order_ticker,
                side=proposed_order_raw.get("side", "BUY"),
                quantity=original_shares,
                price=order_price,
                stop_loss_price=stop_loss_p,
                sector=order_sector,
            )

            # A. Kiểm tra Hard Laws (Lớp 1)
            hl_check: HardLawCheck = self.hard_law_engine.check_order(p_order, p_state, adtv20)
            if not hl_check.passed:
                hard_law_status_map["all_passed"] = False
                if "Single" in str(hl_check.violated_law) or "15%" in hl_check.reason:
                    hard_law_status_map["single_stock"] = "BLOCK"
                elif "ngành" in str(hl_check.violated_law) or "35%" in hl_check.reason:
                    hard_law_status_map["sector"] = "BLOCK"
                elif "Thanh Khoản" in str(hl_check.violated_law) or "ADTV20" in hl_check.reason:
                    hard_law_status_map["liquidity_limit"] = "BLOCK"
                else:
                    hard_law_status_map["position_risk"] = "BLOCK"
                reasons_list.append(f"HARD LAW VIOLATION: {hl_check.reason}")

            # B. Kiểm tra Sức chứa T+2.5 và Đệm rủi ro 2 cây sàn (Lớp 2)
            proposed_val = order_price * original_shares
            t25_check: T25CapacityCheck = self.t25_manager.check_t25_capacity(
                nav=nav,
                locked_t25_value=locked_t25_value,
                proposed_order_value=proposed_val,
                price=order_price,
                stop_loss_price=stop_loss_p,
            )
            if not t25_check.passed:
                hard_law_status_map["t25_capacity"] = "BLOCK" if t25_check.max_safe_shares == 0 else "WARNING"
                reasons_list.append(f"T+2.5 RISK: {t25_check.reason}")

            # C. Kiểm tra Cảm biến Dị thường Giá & Volume VSA (Lớp 3)
            candle_data = proposed_order_raw.get("candle") or proposed_order_raw.get("current_candle")
            if candle_data:
                ma20_vol = float(proposed_order_raw.get("ma20_volume", adtv20))
                ma20_pr = float(proposed_order_raw.get("ma20_price", 0.0)) or None
                swing_low_pr = float(proposed_order_raw.get("swing_low_price", 0.0)) or None
                tape_res: TapeAnomalyResult = self.tape_anomaly_detector.analyze_candle(
                    candle=candle_data,
                    ma20_volume=ma20_vol,
                    ma20_price=ma20_pr,
                    swing_low_price=swing_low_pr,
                )
                tape_anomaly_map = {
                    "detected": tape_res.has_anomaly,
                    "anomaly_type": tape_res.anomaly_type.value,
                    "severity": tape_res.severity.value,
                    "reason": tape_res.reason,
                }
                if tape_res.severity == TapeAnomalySeverity.CRITICAL:
                    reasons_list.append(f"TAPE ANOMALY: {tape_res.reason}")

            # =========================================================================
            # 3. TỔNG HỢP PHÁN QUYẾT THỂ CHẾ (POLICY DECISION MATRIX)
            # =========================================================================
            # Điều kiện BLOCK Tuyệt đối:
            is_blocked = (
                (not hl_check.passed)
                or (hard_law_status_map["t25_capacity"] == "BLOCK")
                or (tape_anomaly_map["severity"] == "CRITICAL")
                or (dd_eval.tier == DrawdownTier.RED)
                or (cdc_eval.tier == CDCTier.RED)
                or (breadth_eval.action_recommended == "BLOCK_BUY")
            )

            if is_blocked:
                action = "BLOCK"
                approved_shares = 0
            else:
                # Tính toán hệ số co giãn quy mô vị thế (Scaling Factor)
                scale_factors = [
                    dd_eval.exposure_scale_factor,
                    cdc_eval.sizing_scale_factor,
                ]
                if breadth_eval.action_recommended == "REDUCE_SIZE":
                    scale_factors.append(0.50)
                if tape_anomaly_map["severity"] == TapeAnomalySeverity.WARNING:
                    scale_factors.append(0.70)

                combined_scale = min(scale_factors)
                calculated_shares = int(original_shares * combined_scale)

                if t25_check.max_safe_shares > 0:
                    calculated_shares = min(calculated_shares, t25_check.max_safe_shares)

                # Làm tròn lô 100 cổ phiếu sàn HOSE
                approved_shares = (calculated_shares // 100) * 100 if calculated_shares >= 100 else 0

                if approved_shares <= 0:
                    action = "BLOCK"
                    reasons_list.append("Sau khi áp dụng hệ số co giãn rủi ro, số lượng cổ phiếu khả dụng < 100 cổ.")
                elif approved_shares < original_shares:
                    action = "REDUCE"
                    reduction_pct = round((1.0 - (approved_shares / original_shares)) * 100, 1)
                    reasons_list.append(f"Tự động cắt giảm {reduction_pct}% quy mô do điều kiện phòng thủ.")
                else:
                    action = "PASS"
                    reasons_list.append("Thỏa mãn 100% tiêu chuẩn an toàn vốn thể chế.")

        decision_id = f"RSK-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        min_cash_target = max(
            dd_eval.min_cash_target_pct,
            breadth_eval.recommended_min_cash_pct,
            cdc_eval.recommended_min_cash_pct,
        )

        final_rationale = " | ".join(reasons_list) if reasons_list else "Danh mục hoạt động trong hạn mức an toàn."

        risk_output = {
            "risk_status": action,
            "decision_id": decision_id,
            "timestamp": datetime.now().isoformat(),
            "ticker": order_ticker,
            "hard_laws": hard_law_status_map,
            "tape_anomaly": tape_anomaly_map,
            "tail_risk": {
                "historical_es_97_5": tail_snapshot.historical_es_97_5,
                "egarch_student_t_es": tail_snapshot.egarch_student_t_es,
                "stress_es": tail_snapshot.stress_es,
                "data_status": tail_snapshot.data_status,
                "tail_risk_verdict": tail_snapshot.tail_risk_verdict,
            },
            "concentration": {
                "stock_weight_post": round((order_price * approved_shares) / nav, 4) if nav > 0 else 0.0,
                "sector_weight_post": round(sector_exposure.get(proposed_order_raw.get("sector", ""), 0.0) / nav, 4) if nav > 0 else 0.0,
                "avg_correlation": 0.44,
            },
            "market_breadth": {
                "distribution_days": breadth_eval.distribution_days_count,
                "breadth_ma20_pct": breadth_eval.breadth_ma20_pct,
                "health_tier": breadth_eval.health_tier.value,
                "is_divergence": breadth_eval.is_divergence_green_index_red_breadth,
            },
            "drawdown": {
                "current_drawdown_pct": dd_eval.current_drawdown_pct,
                "tier": dd_eval.tier.value,
                "re_risking_state": dd_eval.re_risking_state,
            },
            "cdc": {
                "tier": cdc_eval.tier.value,
                "is_cdc_active": cdc_eval.is_cdc_active,
                "ic_decay_pct": cdc_eval.ic_decay_pct,
            },
            "decision": {
                "decision_id": decision_id,
                "ticker": order_ticker,
                "action": action,
                "side": "BUY" if approved_shares > 0 else "HOLD",
                "price": order_price,
                "target_price": order_price,
                "original_shares": original_shares,
                "approved_shares": approved_shares,
                "target_shares": approved_shares,
                "shares": approved_shares,
                "approved_weight_pct": round(((approved_shares * order_price) / nav) * 100, 2) if nav > 0 else 0.0,
                "exposure_reduction_pct": round((1.0 - (approved_shares / original_shares)) * 100, 1) if original_shares > 0 else 0.0,
                "min_cash_target_pct": min_cash_target,
                "rationale": final_rationale,
            },
            # Backward compatibility fields for legacy callers
            "drawdown_tier": dd_eval.tier.value,
            "max_drawdown_pct": dd_eval.current_drawdown_pct,
            "drawdown_action": dd_eval.action_description,
            "garch_cash_target_pct": min_cash_target,
            "macro_risk_score": 50.0,
            "es_97_5_pct": tail_snapshot.historical_es_97_5 * 100,
            "cdc_active": cdc_eval.is_cdc_active,
            "proposed_order_check": {
                "passed": (action != "BLOCK"),
                "violated_law": None if action != "BLOCK" else "HARD_LAW_OR_RISK_GATE",
                "reason": final_rationale,
            },
            "governance": {
                "model_version": "VIETNAM_INSTITUTIONAL_RISK_vNext",
                "policy_version": "HOSE_SPOT_EQUITY_RISK_POLICY_2026",
                "asset_scope": "100% SPOT EQUITY HOSE (NO DERIVATIVES)",
            },
        }

        trace = {
            "risk_gateway": "Sovereign Institutional Pre-Trade Gateway",
            "layers_evaluated": [
                "1. Hard Laws (Single Stock, Sector, ADTV Capacity, T+2.5 Floor Loss)",
                "2. T+2.5 Exposure Lock Manager",
                "3. Tape Anomaly Detector (VSA Churning, Upthrust, Breakdown)",
                "4. Tail Risk Engine (Historical ES 97.5%, EGARCH-t, Stress Matrix)",
                "5. Drawdown Phased Recovery State Machine",
                "6. Capital Degradation Controller (CDC)",
            ],
            "asset_scope": "100% Cổ phiếu cơ sở giao ngay (Spot Equity)",
        }

        return {"data": risk_output, "trace": trace}
