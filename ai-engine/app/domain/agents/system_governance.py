"""AGENT-11: System Governance Agent (IOS v5.1 Institutional Sovereign Architecture)

Kiến trúc Tam Giác Quyền Lực:
1. COMPLIANCE: Thẩm tra 6 Hard Laws bất khả xâm phạm, Ma trận Thẩm quyền (Authority), Quy chế sàn HOSE.
2. AUDIT: Sổ cái bất biến SHA-256 Hash Chaining liên tục, toàn vẹn mật mã (Full Chain Verifier), Versioning.
3. CHANGE: Thẩm định Yêu cầu Thay đổi mô hình (Change Request), Phân tích Tác động (Impact), OOS Validation.
4. DECISION GATE: Cổng phán quyết Pre-Trade & Model Change:
   - PASS -> Cấp chữ ký số Governance Token, cho phép gửi sang Execution Agent hoặc lưu DB.
   - BLOCK -> Sinh Violation Report chuẩn hóa -> Tự động kích hoạt Escalation Router sang AGENT-12 (Strategy CIO).
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.core.registry import AgentRegistry
from app.eval.audit_trail import AuditTrailEngine
from app.domain.rules.failsafe import FailsafeEngine, FailsafeStatus
from app.domain.rules.hard_laws import ProposedOrder, PortfolioState
from app.domain.rules.governance.compliance_engine import (
    GovernanceComplianceEngine,
    ComplianceResult,
    ComplianceVerdict,
    RiskSeverity,
)
from app.domain.rules.governance.change_engine import (
    GovernanceChangeEngine,
    ChangeRequest,
    ChangeEvaluationResult,
    ChangeStatus,
)

logger = logging.getLogger(__name__)


class SystemGovernanceAgent(BaseAgent):
    """
    AGENT-11: Tòa án Tối cao & Trọng tài Thể chế Đầu tư Tự trị (Supreme Governance Gatekeeper).
    """

    def __init__(self):
        super().__init__(
            agent_name="system_governance",
            state_tables=["governance_rules", "audit_reports", "violation_reports"],
            log_table="log_system_governance",
            enabled=True,
        )
        self.compliance_engine = GovernanceComplianceEngine()
        self.audit_trail = AuditTrailEngine()
        self.change_engine = GovernanceChangeEngine()
        self.failsafe_engine = FailsafeEngine(
            heartbeat_interval=30.0,
            latency_threshold_ms=1500.0,
            missed_heartbeats_limit=3,
        )
        self._init_and_sync_governance_rules_to_db()

    def _init_and_sync_governance_rules_to_db(self) -> None:
        """
        Đồng bộ toàn bộ 6 Hard Laws, Chính sách Vi cấu trúc HOSE, và Ma trận Thẩm quyền
        vào bảng governance_rules trong PostgreSQL. Đảm bảo CSDL luôn phản ánh đúng Hiến pháp đầu tư.
        """
        from app.infrastructure.database.pg_pool import get_conn

        rules = [
            ("DIEU_1", "Hard Stop-loss 2% NAV (Kịch bản Gap sàn T+2.5)", "HARD_LAW", True, {"max_nav_loss_pct": 2.0, "floor_gap_risk_pct": 19.6}),
            ("DIEU_2", "Giới hạn Thanh khoản Khớp lệnh Liên tục 20% ADTV20", "HARD_LAW", True, {"max_adtv20_pct": 20.0}),
            ("DIEU_3", "Nguyên tắc Ba Tín hiệu Độc lập (Rule of Three)", "HARD_LAW", True, {"min_confirming_signals": 3}),
            ("DIEU_4", "Trần Tỷ trọng Danh mục (15% Cổ phiếu / 35% Ngành)", "HARD_LAW", True, {"max_single_stock_pct": 15.0, "max_single_sector_pct": 35.0}),
            ("DIEU_5", "Cổng Beneish M-Score Lớp 0 (Loại trừ Gian lận BCTC)", "HARD_LAW", True, {"threshold": -1.78}),
            ("DIEU_6", "Cổng GIL OCR Network (Sở hữu chéo Rủi ro Thảm họa)", "HARD_LAW", True, {"max_ocr_score": 0.85}),
            ("HOSE_MICROSTRUCTURE", "Quy chế Vi Cấu Trúc Sàn HOSE", "MARKET_POLICY", True, {"lot_size": 100, "max_order_shares": 500000, "allow_short_selling": False}),
            ("AUTHORITY_MATRIX", "Ma trận Thẩm quyền Phát Lệnh & Quyết định", "AUTHORITY", True, {"portfolio_allocation": ["BUY", "SELL", "REBALANCE"], "position_monitoring": ["STOP_LOSS_EMERGENCY_SELL"], "system_governance": ["KILL_SWITCH_HALT", "EMERGENCY_FREEZE"]}),
            ("CHANGE_MANAGEMENT", "Quản trị Thay đổi Mô hình ML & Phân tích Sốc Đảo chiều", "CHANGE_MANAGEMENT", True, {"max_turnover_shock_pct": 30.0, "min_oos_sharpe": 1.2, "max_oos_drawdown_pct": 10.0}),
        ]
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    for r_id, r_name, r_cat, is_act, params in rules:
                        cur.execute("""
                            INSERT INTO governance_rules (rule_id, rule_name, rule_category, is_active, parameters, updated_at)
                            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                            ON CONFLICT (rule_id) DO UPDATE SET
                                rule_name = EXCLUDED.rule_name,
                                rule_category = EXCLUDED.rule_category,
                                is_active = EXCLUDED.is_active,
                                parameters = EXCLUDED.parameters,
                                updated_at = CURRENT_TIMESTAMP;
                        """, (r_id, r_name, r_cat, is_act, json.dumps(params)))
            logger.info("[SystemGovernanceAgent] Đã đồng bộ 9 quy tắc Hiến pháp vào bảng governance_rules.")
        except Exception as e:
            logger.warning(f"[SystemGovernanceAgent] Không thể đồng bộ governance_rules: {e}")

    def _save_audit_report_to_db(self, report: Dict[str, Any]) -> None:
        """Lưu trữ báo cáo kiểm toán hệ thống vào bảng audit_reports trong PostgreSQL."""
        from app.infrastructure.database.pg_pool import get_conn
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO audit_reports (
                            report_id, audit_date, integrity_status, violations_count, summary, created_at
                        ) VALUES (%s, CURRENT_DATE, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (report_id) DO NOTHING;
                    """, (
                        report["report_id"],
                        "VERIFIED" if report.get("chain_integrity_valid") else "COMPROMISED",
                        0 if report.get("system_status") == "COMPLIANT" else 1,
                        json.dumps(report, ensure_ascii=False, default=str),
                    ))
            logger.info(f"[SystemGovernanceAgent] Đã lưu báo cáo kiểm toán ID={report['report_id']} vào audit_reports.")
        except Exception as e:
            logger.error(f"[SystemGovernanceAgent] Lỗi lưu audit_reports xuống CSDL: {e}")

    def _save_violation_report_to_db(self, report: Dict[str, Any]) -> None:
        """Lưu trữ báo cáo vi phạm vào bảng violation_reports trong PostgreSQL."""
        from psycopg2.extras import Json
        from app.infrastructure.database.pg_pool import get_conn

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO violation_reports (
                            report_id, timestamp, ticker, issuing_agent,
                            violated_rule, risk_level, reason, order_payload,
                            escalated_to, resolution_status
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (report_id) DO NOTHING;
                    """, (
                        report["report_id"],
                        report["timestamp"],
                        report.get("ticker"),
                        report["issuing_agent"],
                        report["violated_rule"],
                        report["risk_level"],
                        report["reason"],
                        Json(report.get("order_payload", {})),
                        report.get("escalated_to", "strategy_cio"),
                        report.get("resolution_status", "PENDING_CIO_ARBITRATION"),
                    ))
        except Exception as e:
            logger.error(f"[SystemGovernanceAgent] Lỗi lưu violation_report xuống CSDL: {e}")

    async def evaluate_pre_trade_gate(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        CỔNG PHÁN QUYẾT TỐI CAO PRE-TRADE (DECISION GATE):
        Thẩm định toàn diện trước khi bất kỳ lệnh nào được gửi sang Broker / Execution Agent.
        """
        raw_order = event_data.get("order") or event_data.get("proposed_order") or event_data.get("order_instruction") or {}
        raw_portfolio = event_data.get("portfolio") or event_data.get("portfolio_state") or {}
        issuing_agent = str(event_data.get("issuing_agent") or event_data.get("sender") or "portfolio_allocation").lower().strip()
        order_intent = str(event_data.get("order_intent") or raw_order.get("action") or raw_order.get("side") or "BUY").upper().strip()

        ticker = str(raw_order.get("ticker", "UNKNOWN")).upper().strip()
        side = "SELL" if "SELL" in order_intent else "BUY"
        quantity = int(raw_order.get("shares") or raw_order.get("quantity") or raw_order.get("approved_shares") or raw_order.get("target_shares") or 0)
        price = float(raw_order.get("price") or raw_order.get("target_price") or 0.0)
        stop_loss_price = raw_order.get("stop_loss_price")
        if stop_loss_price is not None:
            stop_loss_price = float(stop_loss_price)
        sector = str(raw_order.get("sector") or raw_portfolio.get("positions", {}).get(ticker, {}).get("sector", "Unknown"))

        # 1. Kiểm tra Failsafe Broker Connection
        broker_hb = event_data.get("broker_heartbeat", {"latency_ms": 120.0, "is_connected": True, "missed_beats": 0})
        latency_ms = float(broker_hb.get("latency_ms", 120.0))
        is_connected = bool(broker_hb.get("is_connected", True))
        missed_beats = int(broker_hb.get("missed_beats", 0))

        if not is_connected or missed_beats >= 3 or latency_ms > 1500.0:
            self.failsafe_engine.status = FailsafeStatus.ACTIVE
            failsafe_active = True
            failsafe_reason = f"BROKER_DISCONNECT_OR_LATENCY_SPIKE ({latency_ms:.0f}ms, Missed={missed_beats})"
        else:
            failsafe_active = False
            failsafe_reason = ""

        # Cho phép lệnh Stop-Loss khẩn cấp từ position_monitoring được bảo toàn vốn
        if failsafe_active and not (issuing_agent == "position_monitoring" and side == "SELL"):
            report_id = str(uuid.uuid4())
            violation_report = {
                "report_id": report_id,
                "timestamp": datetime.now().isoformat(),
                "ticker": ticker,
                "issuing_agent": issuing_agent,
                "violated_rule": "FAILSAFE_EMERGENCY_LOCK",
                "risk_level": "CRITICAL",
                "reason": f"Hệ thống đang kích hoạt Failsafe Kill-Switch ({failsafe_reason}). Chặn toàn bộ lệnh thông thường.",
                "order_payload": raw_order,
                "escalated_to": "strategy_cio",
                "resolution_status": "BLOCKED_BY_FAILSAFE",
            }
            self._save_violation_report_to_db(violation_report)
            self.audit_trail.log_event("system_governance", "ORDER_BLOCKED_FAILSAFE", violation_report)

            return {
                "verdict": "BLOCK",
                "decision": "BLOCK",
                "is_compliant": False,
                "governance_token": None,
                "violation_report": violation_report,
                "reason": violation_report["reason"],
            }

        # 2. Xây dựng Object Đề xuất Lệnh và Trạng thái Danh mục
        proposed_order = ProposedOrder(
            ticker=ticker,
            side=side,
            quantity=quantity,
            price=price,
            stop_loss_price=stop_loss_price,
            sector=sector,
        )

        nav = float(raw_portfolio.get("total_nav") or raw_portfolio.get("nav") or 1_000_000_000.0)
        positions = raw_portfolio.get("positions", {})
        sector_exposure = raw_portfolio.get("sector_exposure", {})
        locked_t25 = float(raw_portfolio.get("locked_t25_value", 0.0))

        portfolio_state = PortfolioState(
            nav=nav,
            positions=positions,
            sector_exposure=sector_exposure,
            locked_t25_value=locked_t25,
        )

        adtv20 = float(event_data.get("adtv20", 2_000_000.0))
        signals_count = int(event_data.get("confirming_signals_count", 3))
        beneish_ok = bool(event_data.get("beneish_passed", True))
        gil_ocr = float(event_data.get("gil_ocr_score", 0.0))
        available_sh = event_data.get("available_shares")

        # 3. Thẩm định qua Compliance Engine
        comp_res: ComplianceResult = self.compliance_engine.evaluate_order(
            order=proposed_order,
            portfolio=portfolio_state,
            adtv20_continuous=adtv20,
            issuing_agent=issuing_agent,
            order_intent=order_intent,
            confirming_signals_count=signals_count,
            beneish_passed=beneish_ok,
            gil_ocr_score=gil_ocr,
            available_shares=available_sh,
        )

        # 4. Phân xử Kết quả (Decision Gate Resolution)
        if not comp_res.is_compliant:
            report_id = str(uuid.uuid4())
            violation_report = {
                "report_id": report_id,
                "timestamp": datetime.now().isoformat(),
                "ticker": ticker,
                "issuing_agent": issuing_agent,
                "violated_rule": comp_res.violated_rule,
                "risk_level": comp_res.risk_level.value,
                "reason": comp_res.reason,
                "order_payload": {
                    "ticker": ticker,
                    "side": side,
                    "quantity": quantity,
                    "price": price,
                    "order_intent": order_intent,
                },
                "details": comp_res.details,
                "escalated_to": "strategy_cio",
                "resolution_status": "ESCALATED_TO_CIO",
            }
            # Lưu CSDL và ghi sổ cái băm SHA-256
            self._save_violation_report_to_db(violation_report)
            self.audit_trail.log_event("system_governance", "ORDER_BLOCKED", violation_report)

            # TỰ ĐỘNG ĐỊNH TUYẾN ESCALATION SANG AGENT-12 (STRATEGY CIO)
            cio_resolution = None
            try:
                cio_resp = await AgentRegistry.dispatch("strategy_cio", {
                    "escalation": violation_report,
                    "violation_report": violation_report,
                })
                if cio_resp.get("status") == "SUCCESS":
                    cio_resolution = cio_resp.get("result", {}).get("data")
            except Exception as e:
                logger.warning(f"[SystemGovernanceAgent] Không thể chuyển tiếp Escalation sang Strategy CIO: {e}")

            return {
                "verdict": "BLOCK",
                "decision": "BLOCK",
                "is_compliant": False,
                "governance_token": None,
                "violation_report": violation_report,
                "cio_resolution": cio_resolution,
                "reason": comp_res.reason,
            }

        # 5. Nếu PASS -> Sinh chữ ký số Governance Signature Token & Cho phép thực thi
        gov_token = hashlib.sha256(
            f"{ticker}_{quantity}_{price}_{datetime.now().isoformat()}".encode("utf-8")
        ).hexdigest()[:16]

        approval_payload = {
            "token": f"GOV_{gov_token.upper()}",
            "ticker": ticker,
            "quantity": quantity,
            "price": price,
            "side": side,
            "issuing_agent": issuing_agent,
            "status": "APPROVED",
        }
        self.audit_trail.log_event("system_governance", "ORDER_APPROVED", approval_payload)

        return {
            "verdict": "PASS",
            "decision": "PASS",
            "is_compliant": True,
            "governance_token": f"GOV_{gov_token.upper()}",
            "approved_order": proposed_order.__dict__,
            "reason": comp_res.reason,
        }

    async def evaluate_change_request_gate(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        CỔNG PHÁN QUYẾT QUẢN TRỊ THAY ĐỔI MÔ HÌNH HỌC MÁY (CHANGE GATE):
        Thẩm định đề xuất thay đổi trọng số hoặc tham số từ Agent-10 trước khi cho phép ghi CSDL.
        """
        cr_raw = event_data.get("change_request", {})
        cr = ChangeRequest(
            cr_id=cr_raw.get("cr_id", f"CR_{uuid.uuid4().hex[:8].upper()}"),
            initiator_agent=cr_raw.get("initiator_agent", "reinforcement_learning"),
            target_component=cr_raw.get("target_component", "rl_factor_weights"),
            proposed_changes=cr_raw.get("proposed_changes", {}),
            current_state=cr_raw.get("current_state", {}),
            oos_returns=cr_raw.get("oos_returns", []),
            rationale=cr_raw.get("rationale", ""),
        )

        cr_result: ChangeEvaluationResult = self.change_engine.evaluate_change_request(cr)

        # Ghi nhận vào sổ cái băm
        audit_payload = {
            "cr_id": cr.cr_id,
            "status": cr_result.status.value,
            "approved": cr_result.approved,
            "sharpe": cr_result.annualized_sharpe,
            "max_dd": cr_result.max_drawdown,
            "turnover_delta": cr_result.weight_turnover_delta,
            "reason": cr_result.reason,
        }
        self.audit_trail.log_event("system_governance", f"CHANGE_REQUEST_{cr_result.status.value}", audit_payload)

        # Nếu cần CIO phê duyệt thủ công do Turnover Shock
        cio_resolution = None
        approved_final = cr_result.approved
        status_final = cr_result.status.value

        if cr_result.requires_cio_resolution:
            try:
                cio_resp = await AgentRegistry.dispatch("strategy_cio", {
                    "escalation_change_request": audit_payload,
                    "change_request": cr.__dict__,
                })
                if cio_resp.get("status") == "SUCCESS":
                    cio_resolution = cio_resp.get("result", {}).get("data")
                    if cio_resolution and cio_resolution.get("final_resolution") == "APPROVE_HIGH_TURNOVER_CHANGE":
                        approved_final = True
                        status_final = "APPROVED_BY_CIO"
            except Exception as e:
                logger.warning(f"[SystemGovernanceAgent] Escalation Change Request tới Strategy CIO thất bại: {e}")

        return {
            "cr_id": cr.cr_id,
            "approved": approved_final,
            "status": status_final,
            "annualized_sharpe": cr_result.annualized_sharpe,
            "max_drawdown": cr_result.max_drawdown,
            "weight_turnover_delta": cr_result.weight_turnover_delta,
            "reason": cr_result.reason,
            "cio_resolution": cio_resolution,
        }

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý toàn diện các tác vụ Quản trị Hệ thống:
        1. Pre-Trade Gate: Khi có 'order', 'proposed_order', hoặc 'order_instruction'.
        2. Change Gate: Khi có 'change_request'.
        3. EOD / Heartbeat Audit: Tương thích ngược với các pipeline cũ.
        """
        # 1. Rẽ nhánh Pre-Trade Gate
        if any(k in event_data for k in ("order", "proposed_order", "order_instruction", "pre_trade_check")):
            return await self.evaluate_pre_trade_gate(event_data)

        # 2. Rẽ nhánh Change Request Gate
        if "change_request" in event_data:
            return await self.evaluate_change_request_gate(event_data)

        # 3. Rẽ nhánh Audit Trail & Heartbeat Kiểm toán định kỳ (EOD)
        broker_hb = event_data.get("broker_heartbeat", {"latency_ms": 120.0, "is_connected": True, "missed_beats": 0})
        actions_to_audit = event_data.get("actions_to_audit", [])

        latency_ms = float(broker_hb.get("latency_ms", 120.0))
        is_connected = bool(broker_hb.get("is_connected", True))
        missed_beats = int(broker_hb.get("missed_beats", 0))

        failsafe_active = False
        failsafe_reason = ""

        if not is_connected or missed_beats >= 3:
            failsafe_active = True
            failsafe_reason = f"BROKER_DISCONNECTED: Missed {missed_beats} heartbeats."
            self.failsafe_engine.status = FailsafeStatus.ACTIVE
        elif latency_ms > 1500.0:
            failsafe_active = True
            failsafe_reason = f"HIGH_LATENCY_SPIKE: Broker latency reached {latency_ms:.0f}ms (> 1500ms limit)."
            self.failsafe_engine.status = FailsafeStatus.ACTIVE
        else:
            self.failsafe_engine.status = FailsafeStatus.INACTIVE

        # Ghi nhận các hành động vào Sổ cái Bất biến SHA-256
        audited_records_count = 0
        for act in actions_to_audit:
            agent_id = act.get("agent_id", "system")
            event_type = act.get("event_type", "DECISION")
            details = act.get("details", {})
            try:
                self.audit_trail.log_event(agent_id, event_type, details)
                audited_records_count += 1
            except Exception:
                pass

        # Kiểm toán toàn vẹn chuỗi băm
        is_valid, records_checked, chain_err = self.audit_trail.verify_full_chain()

        report_id = str(uuid.uuid4())
        governance_report = {
            "report_id": report_id,
            "timestamp": datetime.now().isoformat(),
            "system_status": "FAILSAFE_ACTIVE_EMERGENCY_LOCK" if failsafe_active else "COMPLIANT",
            "failsafe_status": self.failsafe_engine.status.value,
            "failsafe_triggered": failsafe_active,
            "failsafe_reason": failsafe_reason,
            "broker_latency_ms": latency_ms,
            "audited_actions_logged": audited_records_count,
            "chain_integrity_valid": is_valid,
            "chain_records_verified": records_checked,
            "chain_error": chain_err,
            "current_hash_chain_tail": self.audit_trail.last_hash,
            "hard_laws_enforced": [
                "DIEU_1_HARD_STOP_LOSS_2PCT_NAV",
                "DIEU_2_MAX_ADTV20_LIQUIDITY_LIMIT",
                "DIEU_3_RULE_OF_THREE_INDEPENDENT_SIGNALS",
                "DIEU_4_CONCENTRATION_MAX_15PCT_STOCK_35PCT_SECTOR",
                "DIEU_5_BENEISH_CLASS_0_GATE",
                "DIEU_6_GIL_CATASTROPHIC_ZERO_TOLERANCE",
            ],
        }

        # Lưu báo cáo kiểm toán vào bảng audit_reports trong PostgreSQL
        self._save_audit_report_to_db(governance_report)

        trace = {
            "compliance_engine": self.compliance_engine.__class__.__name__,
            "audit_trail_engine": self.audit_trail.__class__.__name__,
            "change_engine": self.change_engine.__class__.__name__,
            "current_hash_chain_tail": self.audit_trail.last_hash,
            "chain_status": "VALID_CRYPTOGRAPHIC_INTEGRITY" if is_valid else f"COMPROMISED: {chain_err}",
        }

        return {"data": governance_report, "trace": trace}
