"""AGENT-12: Strategy CIO Agent (IOS v5.1 Institutional Sovereign Architecture)

Chức năng & Thẩm quyền Thể chế:
1. Trọng tài Tối cao (Conflict Arbitration): Phân định 3 Tầng Rủi ro (Hard Law vs Critical Risk vs Normal Risk).
2. Thẩm quyền Ngoại lệ (Exception Authority): Cấp phép ngoại lệ có biên an toàn (Boundedness Check <= 5% NAV, <= 48h, Governance Co-sign).
3. Định hướng Vĩ mô Chiến lược (Strategic Direction): Ban hành Directive có Versioning, Macro Regime, Risk Appetite, Sector Tilt & Flash Invalidation Triggers.
4. Phê duyệt Thay đổi Hệ thống Lớn (Major Change Approval): Thẩm định OOS Sharpe, Max Drawdown và kiểm soát Turnover Shock.
5. Kiểm soát Khẩn cấp (Emergency System Control): Kích hoạt System Halt Dual-Tunnel (Đóng băng BUY mới, bảo vệ Defensive Exit Stop-loss).
6. Sổ cái Kiểm toán Bất biến (Cryptographic Audit Trail): SHA-256 Canonical JSON Hash Chaining, neo vào AuditTrailEngine.

BẢO LƯU HIẾN PHÁP: TUYỆT ĐỐI KHÔNG OVERRIDE 6 HARD LAWS, FAILSAFE, HOẶC AUDIT INTEGRITY.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.core.base_agent import BaseAgent
from app.core.registry import AgentRegistry
from app.eval.audit_trail import AuditTrailEngine

logger = logging.getLogger(__name__)


class MacroRegime(str, Enum):
    BULL = "BULL"
    NORMAL = "NORMAL"
    SIDEWAYS = "SIDEWAYS"
    BEAR = "BEAR"
    CRISIS = "CRISIS"


class RiskAppetite(str, Enum):
    AGGRESSIVE = "AGGRESSIVE"
    NEUTRAL = "NEUTRAL"
    DEFENSIVE = "DEFENSIVE"
    CAPITAL_PRESERVATION = "CAPITAL_PRESERVATION"


class SystemHaltState(str, Enum):
    NORMAL = "NORMAL"
    FREEZE_NEW_ORDERS = "FREEZE_NEW_ORDERS"
    SYSTEM_HALT = "SYSTEM_HALT"


class StrategyCIOAgent(BaseAgent):
    """
    AGENT-12: Giám đốc Đầu tư Chiến lược (CIO) & Trọng tài Thể chế Tối cao.
    """

    HARD_LAW_RULES = {
        "DIEU_1", "DIEU_1_HARD_STOP_LOSS_2PCT_NAV",
        "DIEU_2", "DIEU_2_MAX_ADTV20_LIQUIDITY_LIMIT",
        "DIEU_3", "DIEU_3_RULE_OF_THREE_SIGNALS",
        "DIEU_4", "DIEU_4_CONCENTRATION_MAX_15PCT_STOCK_35PCT_SECTOR",
        "DIEU_5", "DIEU_5_BENEISH_GATE",
        "DIEU_6", "DIEU_6_GIL_CATASTROPHIC",
        "HARD_LAW_BREACH", "FAILSAFE_EMERGENCY_LOCK", "HOSE_SHORT_SELLING_PROHIBITION"
    }

    def __init__(self):
        super().__init__(
            agent_name="strategy_cio",
            state_tables=["strategic_allocations", "cio_resolutions", "cio_strategic_directives"],
            log_table="log_strategy_cio",
            enabled=True,
        )
        self.system_halt_state: SystemHaltState = SystemHaltState.NORMAL
        self.last_decision_hash: str = "0" * 64
        self.audit_trail = AuditTrailEngine()
        self._init_cio_tables()

    def _init_cio_tables(self) -> None:
        """Đảm bảo bảng cio_resolutions, cio_strategic_directives và strategic_allocations có trường hash chain và audit mật mã."""
        from app.infrastructure.database.pg_pool import get_conn
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 1. Bảng cio_resolutions
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS cio_resolutions (
                            resolution_id UUID PRIMARY KEY,
                            thesis_id UUID,
                            decision_type VARCHAR(64) NOT NULL DEFAULT 'CONFLICT_RESOLUTION',
                            ticker VARCHAR(16),
                            debate_summary TEXT,
                            final_resolution VARCHAR(64) NOT NULL,
                            verdict_payload JSONB DEFAULT '{}'::jsonb,
                            previous_hash VARCHAR(64) NOT NULL DEFAULT '0000000000000000000000000000000000000000000000000000000000000000',
                            decision_hash VARCHAR(64),
                            governance_cosign BOOLEAN DEFAULT FALSE,
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        );
                        -- Tự phục hồi các cột nâng cấp nếu bảng đã tồn tại từ migration 001
                        ALTER TABLE cio_resolutions ALTER COLUMN final_resolution TYPE VARCHAR(64);
                        ALTER TABLE cio_resolutions ALTER COLUMN thesis_id DROP NOT NULL;
                        ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS decision_type VARCHAR(64) DEFAULT 'CONFLICT_RESOLUTION';
                        ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS ticker VARCHAR(16);
                        ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS verdict_payload JSONB DEFAULT '{}'::jsonb;
                        ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS previous_hash VARCHAR(64) DEFAULT '0000000000000000000000000000000000000000000000000000000000000000';
                        ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS decision_hash VARCHAR(64);
                        ALTER TABLE cio_resolutions ADD COLUMN IF NOT EXISTS governance_cosign BOOLEAN DEFAULT FALSE;
                        CREATE INDEX IF NOT EXISTS idx_cio_resolutions_created ON cio_resolutions (created_at DESC);
                        CREATE INDEX IF NOT EXISTS idx_cio_resolutions_hash ON cio_resolutions (decision_hash);
                    """)

                    # 2. Bảng cio_strategic_directives
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS cio_strategic_directives (
                            directive_id VARCHAR(64) PRIMARY KEY,
                            policy_version VARCHAR(32) NOT NULL DEFAULT 'v5.1_IOS',
                            effective_from DATE NOT NULL,
                            effective_until DATE,
                            status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
                            macro_regime VARCHAR(32) NOT NULL,
                            risk_appetite VARCHAR(32) NOT NULL,
                            strategic_cash_target_pct NUMERIC(6,2) NOT NULL,
                            sector_tilt JSONB NOT NULL DEFAULT '{}'::jsonb,
                            flash_invalidation_thresholds JSONB DEFAULT '{}'::jsonb,
                            rationale TEXT NOT NULL,
                            decision_hash VARCHAR(64),
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        );
                        CREATE INDEX IF NOT EXISTS idx_cio_directives_status ON cio_strategic_directives (status);
                        CREATE INDEX IF NOT EXISTS idx_cio_directives_effective ON cio_strategic_directives (effective_from, effective_until);
                    """)

                    # 3. Bảng strategic_allocations (tương thích ngược)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS strategic_allocations (
                            allocation_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                            date DATE NOT NULL,
                            macro_view TEXT NOT NULL,
                            cash_target_override NUMERIC(6,2),
                            sector_focus JSONB,
                            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
                        );
                    """)

                    cur.execute("SELECT decision_hash FROM cio_resolutions WHERE decision_hash IS NOT NULL ORDER BY created_at DESC LIMIT 1;")
                    row = cur.fetchone()
                    if row and row[0]:
                        self.last_decision_hash = row[0]
        except Exception as e:
            logger.warning(f"[StrategyCIOAgent] Lỗi tự phục hồi schema / nạp hash: {e}")

    def _calculate_canonical_hash(self, payload: Dict[str, Any], previous_hash: str) -> str:
        """Tính mã băm SHA-256 bất biến dựa trên Canonical JSON."""
        serialized = json.dumps(payload, sort_keys=True, default=str)
        hash_input = f"{serialized}_{previous_hash}".encode("utf-8")
        return hashlib.sha256(hash_input).hexdigest()

    def _persist_audit_record(
        self,
        resolution_id: str,
        decision_type: str,
        ticker: Optional[str],
        final_resolution: str,
        payload: Dict[str, Any],
        thesis_id: Optional[str] = None,
        summary: Optional[str] = None,
        gov_cosign: bool = False,
    ) -> str:
        """Lưu trữ phán quyết bất biến và neo chuỗi băm vào AuditTrailEngine của Governance."""
        from app.infrastructure.database.pg_pool import get_conn
        from psycopg2.extras import Json

        decision_hash = self._calculate_canonical_hash(payload, self.last_decision_hash)

        # Chuẩn hóa an toàn resolution_id và thesis_id để tuyệt đối không lỗi cú pháp PostgreSQL UUID
        safe_res_uuid = str(resolution_id)
        try:
            uuid.UUID(safe_res_uuid)
        except (ValueError, AttributeError):
            safe_res_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(resolution_id)))

        safe_thesis_uuid = None
        if thesis_id:
            try:
                safe_thesis_uuid = str(uuid.UUID(str(thesis_id)))
            except (ValueError, AttributeError):
                safe_thesis_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(thesis_id)))

        resolved_summary = summary or payload.get("executive_rationale") or payload.get("rationale", "")

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO cio_resolutions (
                            resolution_id, thesis_id, decision_type, ticker,
                            debate_summary, final_resolution, verdict_payload,
                            previous_hash, decision_hash, governance_cosign, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP);
                    """, (
                        safe_res_uuid,
                        safe_thesis_uuid,
                        decision_type,
                        ticker,
                        resolved_summary,
                        final_resolution,
                        Json(payload),
                        self.last_decision_hash,
                        decision_hash,
                        gov_cosign
                    ))
            self.last_decision_hash = decision_hash
        except Exception as e:
            logger.error(f"[StrategyCIOAgent] Lỗi ghi sổ cái bất biến cio_resolutions: {e}")

        # Neo chéo (Anchor) vào AuditTrailEngine toàn cục
        try:
            self.audit_trail.log_event("strategy_cio", f"CIO_{decision_type}", {
                "resolution_id": str(resolution_id),
                "final_resolution": final_resolution,
                "decision_hash": decision_hash,
                "ticker": ticker,
            })
        except Exception as e:
            logger.warning(f"[StrategyCIOAgent] Lỗi neo audit trail: {e}")

        return decision_hash

    def _update_violation_report_in_db(self, report_id: str, resolution_id: str, status: str) -> None:
        """Cập nhật trạng thái xử lý trong bảng violation_reports."""
        from app.infrastructure.database.pg_pool import get_conn
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE violation_reports
                        SET resolution_status = %s,
                            cio_resolution_id = %s,
                            resolved_at = CURRENT_TIMESTAMP
                        WHERE report_id = %s;
                    """, (status, resolution_id, report_id))
        except Exception as e:
            logger.error(f"[StrategyCIOAgent] Lỗi cập nhật violation_reports: {e}")

    # =========================================================================
    # 1. GOVERNANCE ESCALATION (Xử lý Vi phạm từ Agent 11)
    # =========================================================================
    async def handle_governance_escalation(self, escalation_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý Escalation từ System Governance Agent khi lệnh bị BLOCK.
        Tuân thủ Hiến pháp: Khẳng định tính bất khả xâm phạm của Hard Laws.
        """
        report_id = escalation_data.get("report_id") or str(uuid.uuid4())
        ticker = str(escalation_data.get("ticker", "PORTFOLIO")).upper().strip()
        violated_rule = str(escalation_data.get("violated_rule", "")).upper().strip()
        risk_level = str(escalation_data.get("risk_level", "HIGH")).upper().strip()
        reason = escalation_data.get("reason", "")
        order_payload = escalation_data.get("order_payload", {})

        resolution_id = str(uuid.uuid4())
        is_hard_law = any(hl in violated_rule for hl in self.HARD_LAW_RULES) or risk_level == "CATASTROPHIC"

        if is_hard_law:
            if "DIEU_4" in violated_rule or "Single" in reason or "15%" in reason:
                # Ép hạ quy mô tối đa về mức an toàn theo luật (Không override, cưỡng chế trần 10%)
                final_res = "FORCE_DOWNSIZE"
                exec_rationale = (
                    f"CIO phán quyết: Vi phạm Điều 4 Hard Law ({violated_rule}). Tuyệt đối cấm mua vượt 15% NAV. "
                    f"Ép hạ tỷ trọng về mức tối đa cho phép 10.0% NAV để đảm bảo tuân thủ Hiến pháp."
                )
                resolution_details = {
                    "action": "FORCE_DOWNSIZE",
                    "adjusted_weight_cap": 0.10,
                    "target_ticker": ticker,
                    "hard_law_override_attempted": False,
                }
            else:
                # Các vi phạm khác (Beneish, GIL Catastrophic, T+2.5 Floor Gap, Failsafe) -> XÁC NHẬN HỦY LỆNH HOÀN TOÀN
                final_res = "CONFIRM_BLOCK"
                exec_rationale = (
                    f"CIO xác nhận phán quyết BLOCK của Governance Agent: Mã {ticker} vi phạm nghiêm trọng {violated_rule}. "
                    f"Lý do: {reason}. Theo Hiến pháp đầu tư, CIO không có thẩm quyền override Hard Laws."
                )
                resolution_details = {
                    "action": "CANCEL_ORDER",
                    "hard_law_override_attempted": False,
                    "target_ticker": ticker,
                }
        else:
            final_res = "APPROVE_CONDITIONAL"
            exec_rationale = f"CIO chấp thuận phân bổ có điều kiện cho {ticker} sau khi thẩm định rủi ro soft limits: {reason}."
            resolution_details = {
                "action": "ALLOW_WITH_MONITORING",
                "target_ticker": ticker,
                "adjusted_weight_cap": 0.05,
            }

        payload = {
            "resolution_id": resolution_id,
            "report_id": report_id,
            "ticker": ticker,
            "final_resolution": final_res,
            "executive_rationale": exec_rationale,
            "details": resolution_details,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }

        # Lưu sổ cái mật mã bất biến và cập nhật violation_reports
        self._persist_audit_record(
            resolution_id=resolution_id,
            decision_type="GOVERNANCE_ESCALATION",
            ticker=ticker,
            final_resolution=final_res,
            payload=payload,
            summary=exec_rationale,
        )
        self._update_violation_report_in_db(report_id, resolution_id, final_res)

        return payload

    # =========================================================================
    # 2. CONFLICT RESOLUTION: PHÂN TÁCH MINH BẠCH 3 TẦNG RỦI RO (PHẢN BIỆN 5)
    # =========================================================================
    async def resolve_conflict(self, conflict_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Phân xử xung đột luận điểm (Thesis vs Counter-Thesis hoặc Portfolio vs Risk).
        Áp dụng chặt chẽ Mô hình Phân định 3 Tầng Rủi ro Thể chế (The 3-Tier Risk Hierarchy):
          - Tầng 1: Hard Law (Hiến pháp) -> UPHOLD_BLOCK (100% Zero Tolerance)
          - Tầng 2: Critical Tail Risk (Cận biên Thảm họa) -> DISCRETIONARY_BLOCK hoặc FORCE_RECALCULATION
          - Tầng 3: Normal Risk (Thương mại / Thị trường Thường) -> PROCEED_WITH_PENALTY (Hệ số phạt Kelly)
        """
        resolution_id = str(uuid.uuid4())
        thesis_id = conflict_data.get("thesis_id") or str(uuid.uuid4())
        ticker = str(conflict_data.get("ticker", "PORTFOLIO")).upper().strip()

        counter_verdict = str(conflict_data.get("counter_verdict") or conflict_data.get("counter_view", "PROCEED")).upper().strip()
        cts_score = float(conflict_data.get("cts_score", 0.0))
        hard_law_breach = conflict_data.get("hard_law_breach_detected", False)
        block_reasons = conflict_data.get("block_reasons", [])
        violated_rule = str(conflict_data.get("violated_rule", "")).upper().strip()

        # Kiểm tra sự hiện diện của vi phạm Hard Law
        has_hard_law_violation = (
            hard_law_breach or
            any(hl in violated_rule for hl in self.HARD_LAW_RULES) or
            any(hl in str(r).upper() for r in block_reasons for hl in self.HARD_LAW_RULES) or
            "GIL_CATASTROPHIC" in counter_verdict or
            "BENEISH_FAIL" in counter_verdict
        )

        # ---------------------------------------------------------------------
        # TẦNG 1: HARD LAW (Hiến pháp Đầu tư — Bất khả Xâm phạm)
        # ---------------------------------------------------------------------
        if has_hard_law_violation:
            final_res = "UPHOLD_BLOCK"
            rationale = (
                f"CIO phán quyết [TẦNG 1 - HARD LAW]: Giữ nguyên phán quyết BLOCK đối với mã {ticker}. "
                f"Phát hiện vi phạm nghiêm trọng Điều luật Hiến pháp ({violated_rule or 'HARD_LAW_BREACH'}). "
                f"Theo Nguyên tắc Bất biến số 2: CIO tuyệt đối không có thẩm quyền override Hard Laws."
            )
            weight_cap = 0.0
            conditions = ["NO_NEW_BUY_ORDERS", "CANCEL_PROPOSED_ORDER", "PERMANENT_REJECTION"]
            severity_tier = "TIER_1_HARD_LAW_INVARIANT"

        # ---------------------------------------------------------------------
        # TẦNG 2: CRITICAL TAIL RISK (Rủi ro Khẩn cấp Cận biên Thảm họa)
        # ---------------------------------------------------------------------
        elif cts_score >= 80.0 or counter_verdict == "BLOCK" or "CRITICAL" in counter_verdict:
            final_res = "DISCRETIONARY_BLOCK"
            rationale = (
                f"CIO phán quyết [TẦNG 2 - CRITICAL TAIL RISK]: Kích hoạt quyền phủ quyết chiến lược (Discretionary Block) đối với {ticker}. "
                f"Điểm phản biện Counter-Thesis Score ({cts_score:.1f}/100) hoặc rủi ro thảm họa quá cao: {block_reasons}. "
                f"Chặn giải ngân để bảo toàn vốn trước nguy cơ sập gãy thanh khoản hoặc quản trị mờ ám."
            )
            weight_cap = 0.0
            conditions = ["RETURN_TO_RESEARCH_QUEUE", "SUSPEND_PURCHASE_UNTIL_AUDITED"]
            severity_tier = "TIER_2_CRITICAL_TAIL_RISK"

        # ---------------------------------------------------------------------
        # TẦNG 1: HARD LAW (Hiến pháp Đầu tư — Bất khả Xâm phạm)
        # ---------------------------------------------------------------------
        if has_hard_law_violation:
            final_res = "UPHOLD_BLOCK"
            rationale = (
                f"CIO phán quyết [TẦNG 1 - HARD LAW]: Giữ nguyên phán quyết BLOCK đối với mã {ticker}. "
                f"Phát hiện vi phạm nghiêm trọng Điều luật Hiến pháp ({violated_rule or 'HARD_LAW_BREACH'}). "
                f"Theo Nguyên tắc Bất biến số 2: CIO tuyệt đối không có thẩm quyền override Hard Laws."
            )
            weight_cap = 0.0
            penalty_factor = 0.0
            conditions = ["NO_NEW_BUY_ORDERS", "CANCEL_PROPOSED_ORDER", "PERMANENT_REJECTION"]
            severity_tier = "TIER_1_HARD_LAW_INVARIANT"

        # ---------------------------------------------------------------------
        # TẦNG 2: CRITICAL TAIL RISK (Rủi ro Khẩn cấp Cận biên Thảm họa)
        # ---------------------------------------------------------------------
        elif cts_score >= 80.0 or counter_verdict == "BLOCK" or "CRITICAL" in counter_verdict:
            final_res = "DISCRETIONARY_BLOCK"
            rationale = (
                f"CIO phán quyết [TẦNG 2 - CRITICAL TAIL RISK]: Kích hoạt quyền phủ quyết chiến lược (Discretionary Block) đối với {ticker}. "
                f"Điểm phản biện Counter-Thesis Score ({cts_score:.1f}/100) hoặc rủi ro thảm họa quá cao: {block_reasons}. "
                f"Chặn giải ngân để bảo toàn vốn trước nguy cơ sập gãy thanh khoản hoặc quản trị mờ ám."
            )
            weight_cap = 0.0
            penalty_factor = 0.0
            conditions = ["RETURN_TO_RESEARCH_QUEUE", "SUSPEND_PURCHASE_UNTIL_AUDITED"]
            severity_tier = "TIER_2_CRITICAL_TAIL_RISK"

        # ---------------------------------------------------------------------
        # TẦNG 3: NORMAL RISK (Rủi ro Kinh doanh & Thị trường Thông thường)
        # ---------------------------------------------------------------------
        else:
            final_res = "PROCEED_WITH_PENALTY"
            # Điều tiết tỷ trọng linh hoạt theo thang điểm CTS
            if cts_score >= 50.0 or "CONDITIONAL" in counter_verdict or "WARNING" in counter_verdict:
                weight_cap = 0.08
                penalty_factor = 0.50
                rationale = (
                    f"CIO phán quyết [TẦNG 3 - NORMAL RISK]: Chấp thuận giải ngân thận trọng cho mã {ticker}. "
                    f"Ghi nhận các cảnh báo thị trường/định giá từ Counter-Thesis (CTS={cts_score:.1f}). "
                    f"Áp trần tỷ trọng an toàn {weight_cap*100:.1f}% NAV và áp dụng hệ số phạt Kelly lambda={penalty_factor:.2f}."
                )
                conditions = ["APPLY_RISK_PENALTY_0_5", "MAX_POSITION_WEIGHT_CAP_8PCT", "TIGHT_TRAILING_STOP_LOSS"]
            else:
                weight_cap = 0.15
                penalty_factor = 1.0
                rationale = (
                    f"CIO phán quyết [TẦNG 3 - NORMAL RISK]: Phê duyệt toàn diện luận điểm đầu tư cho {ticker}. "
                    f"Tỷ lệ Risk/Reward vượt trội, rủi ro thương mại ở mức thấp (CTS={cts_score:.1f})."
                )
                conditions = ["STANDARD_QUARTER_KELLY_SIZING", "ROUTINE_MONITORING"]
            severity_tier = "TIER_3_NORMAL_BUSINESS_RISK"

        resolution_payload = {
            "resolution_id": resolution_id,
            "thesis_id": str(thesis_id),
            "ticker": ticker,
            "severity_tier": severity_tier,
            "final_resolution": final_res,
            "weight_cap": weight_cap,
            "allocated_weight_cap": weight_cap,
            "penalty_factor": penalty_factor,
            "executive_rationale": rationale,
            "rationale": rationale,
            "conditions": conditions,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # Lưu sổ cái mật mã bất biến
        dec_hash = self._persist_audit_record(
            resolution_id=resolution_id,
            decision_type="CONFLICT_RESOLUTION",
            ticker=ticker,
            final_resolution=final_res,
            payload=resolution_payload,
            thesis_id=str(thesis_id),
            summary=rationale,
        )
        resolution_payload["decision_hash"] = dec_hash

        return resolution_payload

    # =========================================================================
    # 3. EXCEPTION MANAGEMENT (Quản trị Ngoại lệ có Biên An Toàn)
    # =========================================================================
    async def evaluate_exception_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thẩm định yêu cầu ngoại lệ ngoài quy chế.
        Bắt buộc tuân thủ Boundedness Check (<= 5% NAV, <= 48h, Governance Co-sign, không lách Hard Law).
        """
        exception_id = req.get("exception_id", str(uuid.uuid4()))
        reason = req.get("reason", "")
        scope = req.get("scope", "GENERAL")
        proposed_exposure = float(req.get("max_exposure_nav_pct", 5.0))
        duration_hours = float(req.get("duration_hours", 24.0))

        # Kiểm tra lách Hard Law chuẩn hóa chuỗi (chống bypass bằng khoảng trắng hoặc dấu gạch nối)
        clean_reason = reason.replace(" ", "_").replace("-", "_").upper()
        clean_scope = scope.replace(" ", "_").replace("-", "_").upper()
        clean_rule = str(req.get("violated_rule", "")).replace(" ", "_").replace("-", "_").upper()
        violates_hard_law = (
            any(hl in clean_reason for hl in self.HARD_LAW_RULES) or
            any(hl in clean_scope for hl in self.HARD_LAW_RULES) or
            any(hl in clean_rule for hl in self.HARD_LAW_RULES)
        )

        if violates_hard_law or proposed_exposure > 5.0:
            final_res = "REJECT_HARD_LAW_BYPASS"
            rationale = (
                f"CIO bác bỏ Yêu cầu Ngoại lệ {exception_id}: Tuyệt đối không cho phép ngoại lệ chạm vào Hard Laws "
                f"hoặc vượt quá hạn mức trần 5.0% NAV (Đề xuất: {proposed_exposure:.1f}%)."
            )
            gov_cosign = False
        elif duration_hours > 48.0:
            final_res = "REJECT_EXCESSIVE_DURATION"
            rationale = f"CIO bác bỏ Yêu cầu Ngoại lệ {exception_id}: Hiệu lực {duration_hours:.1f}h vượt quá trần tối đa 48.0 giờ."
            gov_cosign = False
        else:
            final_res = "APPROVE_BOUNDED_EXCEPTION"
            rationale = (
                f"CIO phê duyệt ngoại lệ có giới hạn cho phạm vi [{scope}]: {reason}. "
                f"Hạn mức phân bổ: {proposed_exposure:.1f}% NAV, thời hạn: {duration_hours:.1f}h. Kích hoạt Dual-Key Co-sign."
            )
            gov_cosign = True

        verdict_payload = {
            "exception_id": exception_id,
            "scope": scope,
            "final_resolution": final_res,
            "approved": final_res == "APPROVE_BOUNDED_EXCEPTION",
            "max_exposure_nav_pct": proposed_exposure if final_res == "APPROVE_BOUNDED_EXCEPTION" else 0.0,
            "expiry_hours": duration_hours,
            "governance_cosign": gov_cosign,
            "rationale": rationale,
            "executive_rationale": rationale,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        dec_hash = self._persist_audit_record(
            resolution_id=exception_id,
            decision_type="EXCEPTION_APPROVAL",
            ticker=None,
            final_resolution=final_res,
            payload=verdict_payload,
            summary=rationale,
            gov_cosign=gov_cosign,
        )
        verdict_payload["decision_hash"] = dec_hash
        return verdict_payload

    # =========================================================================
    # 4. STRATEGIC DIRECTION (Định hướng Vĩ mô Chiến lược & Sector Tilt)
    # =========================================================================
    async def issue_strategic_directive(self, macro_inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ban hành Chỉ thị Chiến lược Vĩ mô cấp Quỹ (Monthly Strategic Directive).
        Tích hợp Bộ Kích Hoạt Hủy Bỏ Khẩn Cấp (Flash Invalidation Trigger) trong phiên.
        """
        directive_id = f"CIO-DIR-{datetime.now().strftime('%Y%m')}-{uuid.uuid4().hex[:4].upper()}"

        credit_growth = float(macro_inputs.get("credit_growth_yoy", 12.5))
        sbv_rate = float(macro_inputs.get("sbv_policy_rate", 4.5))
        vix_analog = float(macro_inputs.get("vix_vn_analog", 18.0))
        breadth_ma20 = float(macro_inputs.get("market_breadth_ma20_pct", 55.0))

        if vix_analog > 35.0 or breadth_ma20 < 15.0:
            regime = MacroRegime.CRISIS
            appetite = RiskAppetite.CAPITAL_PRESERVATION
            cash_target = 60.0
            sector_tilt = {"BANK": "UNDERWEIGHT", "REAL_ESTATE": "UNDERWEIGHT", "CONSUMER": "NEUTRAL", "TECH": "NEUTRAL"}
        elif vix_analog > 25.0 or breadth_ma20 < 35.0:
            regime = MacroRegime.BEAR
            appetite = RiskAppetite.DEFENSIVE
            cash_target = 35.0
            sector_tilt = {"BANK": "NEUTRAL", "REAL_ESTATE": "UNDERWEIGHT", "UTILITIES": "OVERWEIGHT", "TECH": "OVERWEIGHT"}
        elif 35.0 <= breadth_ma20 < 50.0 or 20.0 <= vix_analog <= 25.0:
            regime = MacroRegime.SIDEWAYS
            appetite = RiskAppetite.NEUTRAL
            cash_target = 25.0
            sector_tilt = {"BANK": "NEUTRAL", "TECH": "NEUTRAL", "UTILITIES": "OVERWEIGHT", "REAL_ESTATE": "UNDERWEIGHT"}
        elif credit_growth >= 10.0 and sbv_rate <= 5.0 and breadth_ma20 >= 50.0:
            regime = MacroRegime.BULL
            appetite = RiskAppetite.AGGRESSIVE
            cash_target = 10.0
            sector_tilt = {"BANK": "OVERWEIGHT", "TECH": "OVERWEIGHT", "MATERIALS": "OVERWEIGHT", "REAL_ESTATE": "NEUTRAL"}
        else:
            regime = MacroRegime.NORMAL
            appetite = RiskAppetite.NEUTRAL
            cash_target = 20.0
            sector_tilt = {"BANK": "NEUTRAL", "TECH": "OVERWEIGHT", "RETAIL": "OVERWEIGHT", "INDUSTRIAL_PARK": "OVERWEIGHT"}

        rationale = (
            f"Chỉ thị Chiến lược {directive_id}: Chế độ {regime.value}, Khẩu vị rủi ro {appetite.value}. "
            f"VIX_VN_analog={vix_analog:.1f}, Breadth MA20={breadth_ma20:.1f}%, Tín dụng YoY={credit_growth:.1f}%. "
            f"Mục tiêu tiền mặt chiến lược: {cash_target}%. CIO chỉ cấp ràng buộc trần ngành, không stock-pick."
        )

        effective_from = datetime.now(timezone.utc).date().isoformat()
        directive_payload = {
            "directive_id": directive_id,
            "policy_version": "v5.1_IOS",
            "effective_from": effective_from,
            "macro_regime": regime.value,
            "risk_appetite": appetite.value,
            "strategic_cash_target_pct": cash_target,
            "sector_tilt": sector_tilt,
            "rationale": rationale,
            "executive_rationale": rationale,
            "flash_invalidation_thresholds": {
                "max_vix_surge": 35.0,
                "min_breadth_drop": 15.0,
                "sbv_rate_hike_bps": 100.0,
            },
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        # 1. Lưu CSDL vào bảng cio_strategic_directives
        from app.infrastructure.database.pg_pool import get_conn
        from psycopg2.extras import Json
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO cio_strategic_directives (
                            directive_id, policy_version, effective_from, status,
                            macro_regime, risk_appetite, strategic_cash_target_pct,
                            sector_tilt, flash_invalidation_thresholds, rationale, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                        ON CONFLICT (directive_id) DO NOTHING;
                    """, (
                        directive_id, "v5.1_IOS", effective_from, "ACTIVE",
                        regime.value, appetite.value, cash_target,
                        Json(sector_tilt), Json(directive_payload["flash_invalidation_thresholds"]), rationale
                    ))
        except Exception as e:
            logger.error(f"[StrategyCIOAgent] Lỗi lưu cio_strategic_directives: {e}")

        # 2. Dual-write vào strategic_allocations (tương thích ngược các view cũ)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO strategic_allocations (
                            allocation_id, date, macro_view, cash_target_override, sector_focus, created_at
                        ) VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP);
                    """, (
                        str(uuid.uuid4()),
                        effective_from,
                        rationale,
                        cash_target,
                        Json([s for s, t in sector_tilt.items() if t == "OVERWEIGHT"])
                    ))
        except Exception as e:
            logger.warning(f"[StrategyCIOAgent] Lỗi ghi strategic_allocations (legacy fallback): {e}")

        # 3. Đồng thời lưu vào sổ cái băm chung
        dec_hash = self._persist_audit_record(
            resolution_id=str(uuid.uuid4()),
            decision_type="STRATEGIC_DIRECTIVE",
            ticker=None,
            final_resolution=regime.value,
            payload=directive_payload,
            summary=rationale,
        )
        directive_payload["decision_hash"] = dec_hash

        # Cung cấp thêm các trường tương thích ngược với pipeline
        directive_payload["macro_view"] = rationale
        directive_payload["cash_target_override"] = cash_target
        directive_payload["sector_focus"] = [s for s, t in sector_tilt.items() if t == "OVERWEIGHT"]

        return directive_payload

    # =========================================================================
    # 5. MAJOR SYSTEM CHANGE MANAGEMENT (Thẩm định Đề xuất Thay đổi Mô hình)
    # =========================================================================
    async def handle_change_request_escalation(self, cr_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý đề xuất thay đổi mô hình ML / Factor weights khi phát sinh xáo trộn danh mục lớn.
        """
        cr_id = cr_data.get("cr_id", str(uuid.uuid4()))
        turnover_delta = float(cr_data.get("turnover_delta", cr_data.get("weight_turnover_delta", 0.35)))
        sharpe = float(cr_data.get("sharpe", cr_data.get("annualized_sharpe", 1.5)))
        max_dd = float(cr_data.get("max_drawdown", 0.08))
        resolution_id = str(uuid.uuid4())

        # Tiêu chuẩn thể chế: OOS Sharpe >= 1.40, Max Drawdown <= 12%, Turnover Delta <= 35%
        if sharpe >= 1.40 and turnover_delta <= 0.35 and max_dd <= 0.12:
            final_res = "APPROVE_HIGH_TURNOVER_CHANGE"
            rationale = (
                f"CIO phê duyệt Change Request {cr_id}: Độ xáo trộn {turnover_delta*100:.1f}% nằm trong dung sai cho phép "
                f"và được bù đắp thỏa đáng bởi OOS Sharpe ({sharpe:.2f} >= 1.40) cùng Max Drawdown ({max_dd*100:.1f}% <= 12%)."
            )
        else:
            final_res = "REJECT_EXCESSIVE_TURNOVER"
            rationale = (
                f"CIO bác bỏ Change Request {cr_id}: Độ xáo trộn danh mục ({turnover_delta*100:.1f}%) hoặc rủi ro Drawdown ({max_dd*100:.1f}%) "
                f"quá cao so với Sharpe đạt được ({sharpe:.2f}). Rủi ro bào mòn thuế phí T+1.5 không thể chấp nhận."
            )

        verdict_payload = {
            "resolution_id": resolution_id,
            "cr_id": cr_id,
            "final_resolution": final_res,
            "turnover_delta": turnover_delta,
            "annualized_sharpe": sharpe,
            "max_drawdown": max_dd,
            "executive_rationale": rationale,
            "rationale": rationale,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        dec_hash = self._persist_audit_record(
            resolution_id=resolution_id,
            decision_type="MAJOR_CHANGE_APPROVAL",
            ticker=None,
            final_resolution=final_res,
            payload=verdict_payload,
            summary=rationale,
        )
        verdict_payload["decision_hash"] = dec_hash
        return verdict_payload

    # =========================================================================
    # 6. EMERGENCY SYSTEM CONTROL & FAILSAFE DUAL-TUNNEL (Dừng Khẩn Cấp)
    # =========================================================================
    async def handle_emergency_halt(self, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Kích hoạt Trạng thái Dừng Khẩn cấp (SYSTEM HALT).
        Thực thi Dual-Tunnel: Đóng băng chiều MUA mới, nhưng bảo vệ cổng xả phòng vệ cho Stop-loss.
        """
        halt_id = str(uuid.uuid4())
        reason = trigger_data.get("reason", "CRITICAL_FAILSAFE_OR_DRAWDOWN")
        is_failsafe = trigger_data.get("failsafe_active", False)
        drawdown_tier = trigger_data.get("drawdown_tier", "NORMAL")

        if is_failsafe or drawdown_tier in ("RED", "CRITICAL"):
            self.system_halt_state = SystemHaltState.FREEZE_NEW_ORDERS
            status_verdict = "SYSTEM_HALTED_FREEZE_NEW_ORDERS"
            actions_executed = [
                "BLOCK_ALL_INCOMING_BUY_ORDERS",
                "CANCEL_UNEXECUTED_BUY_ORDERS",
                "PRESERVE_ACTIVE_PORTFOLIO_POSITIONS",
                "ALLOW_DEFENSIVE_STOP_LOSS_VIA_SMART_SLICING",
                "NOTIFY_ALL_EXECUTIVE_AGENTS",
            ]
            rationale = (
                f"KÍCH HOẠT DỪNG HỆ THỐNG KHẨN CẤP: {reason}. Failsafe={is_failsafe}, DrawdownTier={drawdown_tier}. "
                f"Toàn bộ lệnh MUA mới bị đóng băng tức thì. Duy trì đường ống ưu tiên cho Stop-loss bảo toàn vốn."
            )
        else:
            self.system_halt_state = SystemHaltState.NORMAL
            status_verdict = "SYSTEM_OPERATIONAL_NORMAL"
            actions_executed = ["RESUME_FULL_PIPELINE_OPERATIONS"]
            rationale = "Hệ thống vận hành an toàn trong ngưỡng dung sai rủi ro thể chế."

        halt_payload = {
            "halt_id": halt_id,
            "system_state": self.system_halt_state.value,
            "final_resolution": status_verdict,
            "actions_executed": actions_executed,
            "executive_rationale": rationale,
            "rationale": rationale,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        dec_hash = self._persist_audit_record(
            resolution_id=halt_id,
            decision_type="EMERGENCY_SYSTEM_HALT",
            ticker=None,
            final_resolution=status_verdict,
            payload=halt_payload,
            summary=rationale,
        )
        halt_payload["decision_hash"] = dec_hash
        return halt_payload

    # =========================================================================
    # 7. AUDIT TRAIL VERIFICATION & DIRECTIVE INSPECTION (Truy vấn & Xác thực)
    # =========================================================================
    def get_active_directive(self) -> Optional[Dict[str, Any]]:
        """Lấy Chỉ thị Chiến lược Vĩ mô đang có hiệu lực gần nhất từ CSDL."""
        from app.infrastructure.database.pg_pool import get_conn
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT directive_id, policy_version, effective_from, effective_until,
                               status, macro_regime, risk_appetite, strategic_cash_target_pct,
                               sector_tilt, flash_invalidation_thresholds, rationale, decision_hash, created_at
                        FROM cio_strategic_directives
                        WHERE status = 'ACTIVE'
                        ORDER BY created_at DESC
                        LIMIT 1;
                    """)
                    row = cur.fetchone()
                    if not row:
                        return None
                    return {
                        "directive_id": row[0],
                        "policy_version": row[1],
                        "effective_from": str(row[2]),
                        "effective_until": str(row[3]) if row[3] else None,
                        "status": row[4],
                        "macro_regime": row[5],
                        "risk_appetite": row[6],
                        "strategic_cash_target_pct": float(row[7]),
                        "sector_tilt": row[8],
                        "flash_invalidation_thresholds": row[9],
                        "rationale": row[10],
                        "executive_rationale": row[10],
                        "decision_hash": row[11],
                        "created_at": row[12].isoformat() if row[12] else None,
                    }
        except Exception as e:
            logger.error(f"[StrategyCIOAgent] Lỗi truy vấn active directive: {e}")
            return None

    def verify_audit_chain(self, limit: int = 100) -> Dict[str, Any]:
        """
        Kiểm toán xác thực tính toàn vẹn mật mã của Sổ cái Phán quyết CIO.
        Tái tính toán chuỗi băm SHA-256 từ Canonical JSON của từng phán quyết liên tiếp.
        """
        from app.infrastructure.database.pg_pool import get_conn
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT resolution_id, decision_type, final_resolution, verdict_payload, previous_hash, decision_hash, created_at
                        FROM cio_resolutions
                        WHERE decision_hash IS NOT NULL
                        ORDER BY created_at ASC
                        LIMIT %s;
                    """, (limit,))
                    rows = cur.fetchall()

            if not rows:
                return {
                    "status": "EMPTY",
                    "verified": True,
                    "records_checked": 0,
                    "message": "Sổ cái chưa có bản ghi phán quyết nào."
                }

            checked_count = 0
            for row in rows:
                res_id, dec_type, final_res, payload, prev_hash, stored_hash, created_at = row
                
                # Tái tính toán băm SHA-256 Canonical JSON
                parsed_payload = payload if isinstance(payload, dict) else json.loads(payload)
                calc_hash = self._calculate_canonical_hash(parsed_payload, prev_hash)
                
                if calc_hash != stored_hash:
                    return {
                        "status": "TAMPERED_CONTENT",
                        "verified": False,
                        "failed_resolution_id": str(res_id),
                        "calculated_hash": calc_hash,
                        "stored_hash": stored_hash,
                        "records_checked": checked_count,
                    }
                checked_count += 1

            return {
                "status": "VERIFIED_VALID",
                "verified": True,
                "records_checked": checked_count,
                "latest_hash": self.last_decision_hash,
                "message": f"Toàn bộ {checked_count} phán quyết được xác thực toàn vẹn mật mã SHA-256."
            }
        except Exception as e:
            logger.error(f"[StrategyCIOAgent] Lỗi khi xác thực chuỗi băm audit trail: {e}")
            return {"status": "ERROR", "verified": False, "error": str(e)}

    def as_tool(self) -> Dict[str, Any]:
        """Cung cấp metadata phục vụ FastMCP / Chatbot Tool Call."""
        tool_meta = super().as_tool()
        tool_meta["description"] = (
            "AGENT-12: Giám đốc Đầu tư Chiến lược (CIO) & Trọng tài Thể chế Tối cao. "
            "Chịu trách nhiệm phân xử xung đột 3 tầng rủi ro, ban hành chỉ thị vĩ mô, "
            "phê duyệt ngoại lệ bounded <=5% NAV, kiểm soát dừng khẩn cấp và lưu sổ cái băm SHA-256."
        )
        tool_meta["supported_actions"] = [
            "issue_strategic_directive",
            "resolve_conflict",
            "evaluate_exception_request",
            "handle_governance_escalation",
            "handle_change_request_escalation",
            "handle_emergency_halt",
            "get_active_directive",
            "verify_audit_chain",
            "get_system_status",
        ]
        return tool_meta

    # =========================================================================
    # PROCESS DISPATCHER
    # =========================================================================
    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Điều phối các sự kiện Thể chế tới các module thẩm quyền của CIO."""
        action = str(event_data.get("action", "")).strip().lower()

        # 0. Truy vấn tra cứu nhanh không làm thay đổi trạng thái
        if action == "get_active_directive":
            directive = self.get_active_directive()
            return {"data": directive, "trace": {"cio_action": "GET_ACTIVE_DIRECTIVE"}}

        if action == "verify_audit_chain":
            limit = int(event_data.get("limit", 100))
            verify_res = self.verify_audit_chain(limit=limit)
            return {"data": verify_res, "trace": {"cio_action": "VERIFY_AUDIT_CHAIN"}}

        if action == "get_system_status":
            return {
                "data": {
                    "system_halt_state": self.system_halt_state.value,
                    "last_decision_hash": self.last_decision_hash,
                },
                "trace": {"cio_action": "GET_SYSTEM_STATUS"}
            }

        # 1. Sự cố Khẩn cấp Failsafe hoặc Kích hoạt Dừng Hệ thống
        if event_data.get("failsafe_active") or event_data.get("trigger_system_halt") or action == "emergency_halt":
            res = await self.handle_emergency_halt(event_data)
            return {"data": res, "trace": {"cio_action": "EMERGENCY_HALT"}}

        # 2. Xử lý Escalation khi có lệnh vi phạm từ Governance (Agent 11)
        if "escalation" in event_data or "violation_report" in event_data or action == "escalation":
            escalation_payload = event_data.get("escalation") or event_data.get("violation_report") or event_data
            res = await self.handle_governance_escalation(escalation_payload)
            trace = {"escalation_source": "system_governance_agent", "verdict": res["final_resolution"]}
            return {"data": res, "trace": trace}

        # 3. Escalation Change Request từ Governance
        if "escalation_change_request" in event_data or "change_request" in event_data or action == "change_request":
            cr_payload = event_data.get("escalation_change_request") or event_data.get("change_request") or event_data
            res = await self.handle_change_request_escalation(cr_payload)
            trace = {"escalation_source": "system_governance_change_request", "verdict": res["final_resolution"]}
            return {"data": res, "trace": trace}

        # 4. Phân xử Xung đột Luận điểm (Thesis vs Counter-Thesis hoặc Portfolio vs Risk)
        if "conflict" in event_data or action == "resolve_conflict":
            conflict_payload = event_data.get("conflict") or event_data
            res = await self.resolve_conflict(conflict_payload)
            trace = {
                "debate_synthesis": {
                    "final_verdict": res["final_resolution"],
                    "severity_tier": res.get("severity_tier"),
                    "allocated_weight_cap": res.get("weight_cap"),
                    "penalty_factor": res.get("penalty_factor"),
                }
            }
            return {"data": res, "trace": trace}

        # 5. Yêu cầu Ngoại lệ (Exception Request)
        if "exception_request" in event_data or action == "exception_request":
            req_payload = event_data.get("exception_request") or event_data
            res = await self.evaluate_exception_request(req_payload)
            return {"data": res, "trace": {"cio_action": "EXCEPTION_EVALUATION"}}

        # 6. Ban hành Chỉ thị Vĩ mô Chiến lược (Macro Review)
        macro_inputs = event_data.get("macro_data") or event_data
        res = await self.issue_strategic_directive(macro_inputs)
        trace = {"regime_context": res.get("macro_regime"), "strategic_cash": res.get("strategic_cash_target_pct")}
        return {"data": res, "trace": trace}
