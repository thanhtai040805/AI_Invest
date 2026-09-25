"""AGENT-12: Strategy CIO Agent (IOS v5.1 Institutional Sovereign Architecture)

Chá»©c nÄƒng & Tháº©m quyá»n Thá»ƒ cháº¿:
1. Trá»ng tĂ i Tá»‘i cao (Conflict Arbitration): PhĂ¢n Ä‘á»‹nh 3 Táº§ng Rá»§i ro (Hard Law vs Critical Risk vs Normal Risk).
2. Tháº©m quyá»n Ngoáº¡i lá»‡ (Exception Authority): Cáº¥p phĂ©p ngoáº¡i lá»‡ cĂ³ biĂªn an toĂ n (Boundedness Check <= 5% NAV, <= 48h, Governance Co-sign).
3. Äá»‹nh hÆ°á»›ng VÄ© mĂ´ Chiáº¿n lÆ°á»£c (Strategic Direction): Ban hĂ nh Directive cĂ³ Versioning, Macro Regime, Risk Appetite, Sector Tilt & Flash Invalidation Triggers.
4. PhĂª duyá»‡t Thay Ä‘á»•i Há»‡ thá»‘ng Lá»›n (Major Change Approval): Tháº©m Ä‘á»‹nh OOS Sharpe, Max Drawdown vĂ  kiá»ƒm soĂ¡t Turnover Shock.
5. Kiá»ƒm soĂ¡t Kháº©n cáº¥p (Emergency System Control): KĂ­ch hoáº¡t System Halt Dual-Tunnel (ÄĂ³ng bÄƒng BUY má»›i, báº£o vá»‡ Defensive Exit Stop-loss).
6. Sá»• cĂ¡i Kiá»ƒm toĂ¡n Báº¥t biáº¿n (Cryptographic Audit Trail): SHA-256 Canonical JSON Hash Chaining, neo vĂ o AuditTrailEngine.

Báº¢O LÆ¯U HIáº¾N PHĂP: TUYá»†T Äá»I KHĂ”NG OVERRIDE HARD LAWS, FAILSAFE, HOáº¶C AUDIT INTEGRITY.
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
    AGENT-12: GiĂ¡m Ä‘á»‘c Äáº§u tÆ° Chiáº¿n lÆ°á»£c (CIO) & Trá»ng tĂ i Thá»ƒ cháº¿ Tá»‘i cao.
    """

    HARD_LAW_RULES = {
        "DIEU_1", "DIEU_1_HARD_STOP_LOSS_2PCT_NAV",
        "DIEU_2", "DIEU_2_MAX_ADTV20_LIQUIDITY_LIMIT",
        "DIEU_3", "DIEU_3_RULE_OF_THREE_SIGNALS",
        "DIEU_4", "DIEU_4_CONCENTRATION_MAX_15PCT_STOCK_35PCT_SECTOR",
        "DIEU_5", "DIEU_5_BENEISH_GATE",
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
        try:
            from app.domain.rules.strategic_memo_generator import StrategicMemoGenerator
            from app.infrastructure.llm.client import get_unified_llm_client
            self.memo_generator = StrategicMemoGenerator(llm_client=get_unified_llm_client())
        except Exception as e_memo:
            logger.debug(f"[StrategyCIOAgent] KhĂ´ng thá»ƒ khá»Ÿi táº¡o memo_generator: {e_memo}")
            self.memo_generator = None

    def _init_cio_tables(self) -> None:
        """Load latest CIO hash; schema is managed by Prisma/migrations, not runtime."""
        from app.infrastructure.database.pg_pool import get_conn
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT decision_hash FROM cio_resolutions WHERE decision_hash IS NOT NULL ORDER BY created_at DESC LIMIT 1;")
                    row = cur.fetchone()
                    if row and row[0]:
                        self.last_decision_hash = row[0]
        except Exception as e:
            logger.warning(f"[StrategyCIOAgent] KhĂ´ng thá»ƒ náº¡p hash CIO tá»« database: {e}")

    def _calculate_canonical_hash(self, payload: Dict[str, Any], previous_hash: str) -> str:
        """TĂ­nh mĂ£ bÄƒm SHA-256 báº¥t biáº¿n dá»±a trĂªn Canonical JSON."""
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
        """LÆ°u trá»¯ phĂ¡n quyáº¿t báº¥t biáº¿n vĂ  neo chuá»—i bÄƒm vĂ o AuditTrailEngine cá»§a Governance."""
        from app.infrastructure.database.pg_pool import get_conn
        from psycopg2.extras import Json

        decision_hash = self._calculate_canonical_hash(payload, self.last_decision_hash)


        # Chuáº©n hĂ³a an toĂ n resolution_id vĂ  thesis_id Ä‘á»ƒ tuyá»‡t Ä‘á»‘i khĂ´ng lá»—i cĂº phĂ¡p PostgreSQL UUID
        safe_res_uuid = str(resolution_id)
        try:
            uuid.UUID(safe_res_uuid)
        except (ValueError, AttributeError):
            safe_res_uuid = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(resolution_id)))

        safe_thesis_id = str(thesis_id).strip()[:64] if thesis_id else None

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
                        safe_thesis_id,
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
            logger.error(f"[StrategyCIOAgent] Lá»—i ghi sá»• cĂ¡i báº¥t biáº¿n cio_resolutions: {e}")

        # Neo chĂ©o (Anchor) vĂ o AuditTrailEngine toĂ n cá»¥c
        try:
            self.audit_trail.log_event("strategy_cio", f"CIO_{decision_type}", {
                "resolution_id": str(resolution_id),
                "final_resolution": final_resolution,
                "decision_hash": decision_hash,
                "ticker": ticker,
            })
        except Exception as e:
            logger.warning(f"[StrategyCIOAgent] Lá»—i neo audit trail: {e}")

        return decision_hash

    async def _publish_cio_event(self, payload: Dict[str, Any], decision_type: str = "CIO_RESOLUTION") -> None:
        """Báº¯n sá»± kiá»‡n CIO_RESOLUTION lĂªn RabbitMQ Event Bus."""
        try:
            from app.core.event_topics import EventTopics
            await self.publish_event(
                topic=EventTopics.CIO_RESOLUTION,
                payload={
                    "resolution_id": str(payload.get("resolution_id") or payload.get("halt_id") or payload.get("directive_id") or uuid.uuid4()),
                    "decision_type": str(payload.get("decision_type", decision_type)),
                    "ticker": payload.get("ticker"),
                    "final_resolution": str(payload.get("final_resolution", "")),
                    "decision_hash": payload.get("decision_hash", self.last_decision_hash),
                    "executive_rationale": str(payload.get("executive_rationale") or payload.get("rationale", "")),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as e_pub:
            logger.warning(f"[StrategyCIOAgent] Lá»—i báº¯n sá»± kiá»‡n CIO_RESOLUTION: {e_pub}")

    def _update_violation_report_in_db(self, report_id: str, resolution_id: str, status: str) -> None:
        """Cáº­p nháº­t tráº¡ng thĂ¡i xá»­ lĂ½ trong báº£ng violation_reports."""
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
            logger.error(f"[StrategyCIOAgent] Lá»—i cáº­p nháº­t violation_reports: {e}")

    # =========================================================================
    # 1. GOVERNANCE ESCALATION (Xá»­ lĂ½ Vi pháº¡m tá»« Agent 11)
    # =========================================================================
    async def handle_governance_escalation(self, escalation_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xá»­ lĂ½ Escalation tá»« System Governance Agent khi lá»‡nh bá»‹ BLOCK.
        TuĂ¢n thá»§ Hiáº¿n phĂ¡p: Kháº³ng Ä‘á»‹nh tĂ­nh báº¥t kháº£ xĂ¢m pháº¡m cá»§a Hard Laws.
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
                # Ă‰p háº¡ quy mĂ´ tá»‘i Ä‘a vá» má»©c an toĂ n theo luáº­t (KhĂ´ng override, cÆ°á»¡ng cháº¿ tráº§n 10%)
                final_res = "FORCE_DOWNSIZE"
                exec_rationale = (
                    f"CIO phĂ¡n quyáº¿t: Vi pháº¡m Äiá»u 4 Hard Law ({violated_rule}). Tuyá»‡t Ä‘á»‘i cáº¥m mua vÆ°á»£t 15% NAV. "
                    f"Ă‰p háº¡ tá»· trá»ng vá» má»©c tá»‘i Ä‘a cho phĂ©p 10.0% NAV Ä‘á»ƒ Ä‘áº£m báº£o tuĂ¢n thá»§ Hiáº¿n phĂ¡p."
                )
                resolution_details = {
                    "action": "FORCE_DOWNSIZE",
                    "adjusted_weight_cap": 0.10,
                    "target_ticker": ticker,
                    "hard_law_override_attempted": False,
                }
            else:
                # CĂ¡c vi pháº¡m khĂ¡c (Beneish, T+2.5 Floor Gap, Failsafe) -> XĂC NHáº¬N Há»¦Y Lá»†NH HOĂ€N TOĂ€N
                final_res = "CONFIRM_BLOCK"
                exec_rationale = (
                    f"CIO xĂ¡c nháº­n phĂ¡n quyáº¿t BLOCK cá»§a Governance Agent: MĂ£ {ticker} vi pháº¡m nghiĂªm trá»ng {violated_rule}. "
                    f"LĂ½ do: {reason}. Theo Hiáº¿n phĂ¡p Ä‘áº§u tÆ°, CIO khĂ´ng cĂ³ tháº©m quyá»n override Hard Laws."
                )
                resolution_details = {
                    "action": "CANCEL_ORDER",
                    "hard_law_override_attempted": False,
                    "target_ticker": ticker,
                }
        else:
            final_res = "APPROVE_CONDITIONAL"
            exec_rationale = f"CIO cháº¥p thuáº­n phĂ¢n bá»• cĂ³ Ä‘iá»u kiá»‡n cho {ticker} sau khi tháº©m Ä‘á»‹nh rá»§i ro soft limits: {reason}."
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

        # LÆ°u sá»• cĂ¡i máº­t mĂ£ báº¥t biáº¿n vĂ  cáº­p nháº­t violation_reports
        dec_hash = self._persist_audit_record(
            resolution_id=resolution_id,
            decision_type="GOVERNANCE_ESCALATION",
            ticker=ticker,
            final_resolution=final_res,
            payload=payload,
            summary=exec_rationale,
        )
        payload["decision_hash"] = dec_hash
        self._update_violation_report_in_db(report_id, resolution_id, final_res)
        await self._publish_cio_event(payload, decision_type="GOVERNANCE_ESCALATION")

        return payload

    # =========================================================================
    # 2. CONFLICT RESOLUTION: PHĂ‚N TĂCH MINH Báº CH 3 Táº¦NG Rá»¦I RO (PHáº¢N BIá»†N 5)
    # =========================================================================
    async def resolve_conflict(self, conflict_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        PhĂ¢n xá»­ xung Ä‘á»™t luáº­n Ä‘iá»ƒm (Thesis vs Counter-Thesis hoáº·c Portfolio vs Risk).
        Ăp dá»¥ng cháº·t cháº½ MĂ´ hĂ¬nh PhĂ¢n Ä‘á»‹nh 3 Táº§ng Rá»§i ro Thá»ƒ cháº¿ (The 3-Tier Risk Hierarchy):
          - Táº§ng 1: Hard Law (Hiáº¿n phĂ¡p) -> UPHOLD_BLOCK (100% Zero Tolerance)
          - Táº§ng 2: Critical Tail Risk (Cáº­n biĂªn Tháº£m há»a) -> DISCRETIONARY_BLOCK hoáº·c FORCE_RECALCULATION
          - Táº§ng 3: Normal Risk (ThÆ°Æ¡ng máº¡i / Thá»‹ trÆ°á»ng ThÆ°á»ng) -> PROCEED_WITH_PENALTY (Há»‡ sá»‘ pháº¡t Kelly)
        """
        resolution_id = str(uuid.uuid4())
        thesis_id = conflict_data.get("thesis_id") or str(uuid.uuid4())
        ticker = str(conflict_data.get("ticker", "PORTFOLIO")).upper().strip()

        counter_verdict = str(conflict_data.get("counter_verdict") or conflict_data.get("counter_view", "PROCEED")).upper().strip()
        cts_score = float(conflict_data.get("cts_score", 0.0))
        hard_law_breach = conflict_data.get("hard_law_breach_detected", False)
        block_reasons = conflict_data.get("block_reasons", [])
        violated_rule = str(conflict_data.get("violated_rule", "")).upper().strip()

        # Kiá»ƒm tra sá»± hiá»‡n diá»‡n cá»§a vi pháº¡m Hard Law
        has_hard_law_violation = (
            hard_law_breach or
            any(hl in violated_rule for hl in self.HARD_LAW_RULES) or
            any(hl in str(r).upper() for r in block_reasons for hl in self.HARD_LAW_RULES) or
            "BENEISH_FAIL" in counter_verdict
        )


        # ---------------------------------------------------------------------
        # Táº¦NG 1: HARD LAW (Hiáº¿n phĂ¡p Äáº§u tÆ° â€” Báº¥t kháº£ XĂ¢m pháº¡m)
        # ---------------------------------------------------------------------
        if has_hard_law_violation:
            final_res = "UPHOLD_BLOCK"
            rationale = (
                f"CIO phĂ¡n quyáº¿t [Táº¦NG 1 - HARD LAW]: Giá»¯ nguyĂªn phĂ¡n quyáº¿t BLOCK Ä‘á»‘i vá»›i mĂ£ {ticker}. "
                f"PhĂ¡t hiá»‡n vi pháº¡m nghiĂªm trá»ng Äiá»u luáº­t Hiáº¿n phĂ¡p ({violated_rule or 'HARD_LAW_BREACH'}). "
                f"Theo NguyĂªn táº¯c Báº¥t biáº¿n sá»‘ 2: CIO tuyá»‡t Ä‘á»‘i khĂ´ng cĂ³ tháº©m quyá»n override Hard Laws."
            )
            weight_cap = 0.0
            penalty_factor = 0.0
            conditions = ["NO_NEW_BUY_ORDERS", "CANCEL_PROPOSED_ORDER", "PERMANENT_REJECTION"]
            severity_tier = "TIER_1_HARD_LAW_INVARIANT"

        # ---------------------------------------------------------------------
        # Táº¦NG 2: CRITICAL TAIL RISK (Rá»§i ro Kháº©n cáº¥p Cáº­n biĂªn Tháº£m há»a)
        # ---------------------------------------------------------------------
        elif cts_score >= 80.0 or counter_verdict == "BLOCK" or "CRITICAL" in counter_verdict:
            final_res = "DISCRETIONARY_BLOCK"
            block_causes = []
            if cts_score >= 80.0:
                block_causes.append(f"CTS {cts_score:.1f} exceeds the block threshold of 80")
            if counter_verdict == "BLOCK":
                block_causes.extend(block_reasons or ["Counter-Thesis returned BLOCK"])
            elif "CRITICAL" in counter_verdict:
                block_causes.append(f"Counter-Thesis returned {counter_verdict}")
            rationale = f"CIO blocks {ticker} at CTS={cts_score:.1f}/100. Causes: {'; '.join(block_causes)}."
            weight_cap = 0.0
            penalty_factor = 0.0
            conditions = ["RETURN_TO_RESEARCH_QUEUE", "SUSPEND_PURCHASE_UNTIL_AUDITED"]
            severity_tier = "TIER_2_CRITICAL_TAIL_RISK" if cts_score >= 80.0 or "CRITICAL" in counter_verdict else "TIER_2_COUNTERTHESIS_VETO"

        # ---------------------------------------------------------------------
        # Táº¦NG 3: NORMAL RISK (Rá»§i ro Kinh doanh & Thá»‹ trÆ°á»ng ThĂ´ng thÆ°á»ng)
        # ---------------------------------------------------------------------
        else:
            final_res = "PROCEED_WITH_PENALTY"
            # Äiá»u tiáº¿t tá»· trá»ng linh hoáº¡t theo thang Ä‘iá»ƒm CTS
            if cts_score >= 50.0 or "CONDITIONAL" in counter_verdict or "WARNING" in counter_verdict:
                weight_cap = 0.08
                penalty_factor = 0.50
                rationale = (
                    f"CIO phĂ¡n quyáº¿t [Táº¦NG 3 - NORMAL RISK]: Cháº¥p thuáº­n giáº£i ngĂ¢n tháº­n trá»ng cho mĂ£ {ticker}. "
                    f"Ghi nháº­n cĂ¡c cáº£nh bĂ¡o thá»‹ trÆ°á»ng/Ä‘á»‹nh giĂ¡ tá»« Counter-Thesis (CTS={cts_score:.1f}). "
                    f"Ăp tráº§n tá»· trá»ng an toĂ n {weight_cap*100:.1f}% NAV vĂ  Ă¡p dá»¥ng há»‡ sá»‘ pháº¡t Kelly lambda={penalty_factor:.2f}."
                )
                conditions = ["APPLY_RISK_PENALTY_0_5", "MAX_POSITION_WEIGHT_CAP_8PCT", "TIGHT_TRAILING_STOP_LOSS"]
            else:
                weight_cap = 0.15
                penalty_factor = 1.0
                rationale = (
                    f"CIO phĂ¡n quyáº¿t [Táº¦NG 3 - NORMAL RISK]: PhĂª duyá»‡t toĂ n diá»‡n luáº­n Ä‘iá»ƒm Ä‘áº§u tÆ° cho {ticker}. "
                    f"Tá»· lá»‡ Risk/Reward vÆ°á»£t trá»™i, rá»§i ro thÆ°Æ¡ng máº¡i á»Ÿ má»©c tháº¥p (CTS={cts_score:.1f})."
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

        # LÆ°u sá»• cĂ¡i máº­t mĂ£ báº¥t biáº¿n
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
        await self._publish_cio_event(resolution_payload, decision_type="CONFLICT_RESOLUTION")

        return resolution_payload

    # =========================================================================
    # 3. EXCEPTION MANAGEMENT (Quáº£n trá»‹ Ngoáº¡i lá»‡ cĂ³ BiĂªn An ToĂ n)
    # =========================================================================
    async def evaluate_exception_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """
        Tháº©m Ä‘á»‹nh yĂªu cáº§u ngoáº¡i lá»‡ ngoĂ i quy cháº¿.
        Báº¯t buá»™c tuĂ¢n thá»§ Boundedness Check (<= 5% NAV, <= 48h, Governance Co-sign, khĂ´ng lĂ¡ch Hard Law).
        """
        exception_id = req.get("exception_id", str(uuid.uuid4()))
        reason = req.get("reason", "")
        scope = req.get("scope", "GENERAL")
        proposed_exposure = float(req.get("max_exposure_nav_pct", 5.0))
        duration_hours = float(req.get("duration_hours", 24.0))

        # Kiá»ƒm tra lĂ¡ch Hard Law chuáº©n hĂ³a chuá»—i (chá»‘ng bypass báº±ng khoáº£ng tráº¯ng hoáº·c dáº¥u gáº¡ch ná»‘i)
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
                f"CIO bĂ¡c bá» YĂªu cáº§u Ngoáº¡i lá»‡ {exception_id}: Tuyá»‡t Ä‘á»‘i khĂ´ng cho phĂ©p ngoáº¡i lá»‡ cháº¡m vĂ o Hard Laws "
                f"hoáº·c vÆ°á»£t quĂ¡ háº¡n má»©c tráº§n 5.0% NAV (Äá» xuáº¥t: {proposed_exposure:.1f}%)."
            )
            gov_cosign = False
        elif duration_hours > 48.0:
            final_res = "REJECT_EXCESSIVE_DURATION"
            rationale = f"CIO bĂ¡c bá» YĂªu cáº§u Ngoáº¡i lá»‡ {exception_id}: Hiá»‡u lá»±c {duration_hours:.1f}h vÆ°á»£t quĂ¡ tráº§n tá»‘i Ä‘a 48.0 giá»."
            gov_cosign = False
        else:
            final_res = "APPROVE_BOUNDED_EXCEPTION"
            rationale = (
                f"CIO phĂª duyá»‡t ngoáº¡i lá»‡ cĂ³ giá»›i háº¡n cho pháº¡m vi [{scope}]: {reason}. "
                f"Háº¡n má»©c phĂ¢n bá»•: {proposed_exposure:.1f}% NAV, thá»i háº¡n: {duration_hours:.1f}h. KĂ­ch hoáº¡t Dual-Key Co-sign."
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
        await self._publish_cio_event(verdict_payload, decision_type="EXCEPTION_APPROVAL")
        return verdict_payload

    # =========================================================================
    # 4. STRATEGIC DIRECTION (Äá»‹nh hÆ°á»›ng VÄ© mĂ´ Chiáº¿n lÆ°á»£c & Sector Tilt)
    # =========================================================================
    async def issue_strategic_directive(self, macro_inputs: Dict[str, Any]) -> Dict[str, Any]:
        """
        Ban hĂ nh Chá»‰ thá»‹ Chiáº¿n lÆ°á»£c VÄ© mĂ´ cáº¥p Quá»¹ (Monthly Strategic Directive).
        TĂ­ch há»£p Bá»™ KĂ­ch Hoáº¡t Há»§y Bá» Kháº©n Cáº¥p (Flash Invalidation Trigger) trong phiĂªn.
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
            f"Chá»‰ thá»‹ Chiáº¿n lÆ°á»£c {directive_id}: Cháº¿ Ä‘á»™ {regime.value}, Kháº©u vá»‹ rá»§i ro {appetite.value}. "
            f"VIX_VN_analog={vix_analog:.1f}, Breadth MA20={breadth_ma20:.1f}%, TĂ­n dá»¥ng YoY={credit_growth:.1f}%. "
            f"Má»¥c tiĂªu tiá»n máº·t chiáº¿n lÆ°á»£c: {cash_target}%. CIO chá»‰ cáº¥p rĂ ng buá»™c tráº§n ngĂ nh, khĂ´ng stock-pick."
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

        # 1. LÆ°u CSDL vĂ o báº£ng cio_strategic_directives
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
            logger.error(f"[StrategyCIOAgent] Lá»—i lÆ°u cio_strategic_directives: {e}")

        # 2. Dual-write vĂ o strategic_allocations (tÆ°Æ¡ng thĂ­ch ngÆ°á»£c cĂ¡c view cÅ©)
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
            logger.warning(f"[StrategyCIOAgent] Lá»—i ghi strategic_allocations (legacy fallback): {e}")

        # 3. Äá»“ng thá»i lÆ°u vĂ o sá»• cĂ¡i bÄƒm chung
        dec_hash = self._persist_audit_record(
            resolution_id=str(uuid.uuid4()),
            decision_type="STRATEGIC_DIRECTIVE",
            ticker=None,
            final_resolution=regime.value,
            payload=directive_payload,
            summary=rationale,
        )
        directive_payload["decision_hash"] = dec_hash

        # Cung cáº¥p thĂªm cĂ¡c trÆ°á»ng tÆ°Æ¡ng thĂ­ch ngÆ°á»£c vá»›i pipeline
        directive_payload["macro_view"] = rationale
        directive_payload["cash_target_override"] = cash_target
        directive_payload["sector_focus"] = [s for s, t in sector_tilt.items() if t == "OVERWEIGHT"]
        await self._publish_cio_event(directive_payload, decision_type="STRATEGIC_DIRECTIVE")

        return directive_payload

    # =========================================================================
    # 5. MAJOR SYSTEM CHANGE MANAGEMENT (Tháº©m Ä‘á»‹nh Äá» xuáº¥t Thay Ä‘á»•i MĂ´ hĂ¬nh)
    # =========================================================================
    async def handle_change_request_escalation(self, cr_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xá»­ lĂ½ Ä‘á» xuáº¥t thay Ä‘á»•i mĂ´ hĂ¬nh ML / Factor weights khi phĂ¡t sinh xĂ¡o trá»™n danh má»¥c lá»›n.
        """
        cr_id = cr_data.get("cr_id", str(uuid.uuid4()))
        turnover_delta = float(cr_data.get("turnover_delta", cr_data.get("weight_turnover_delta", 0.35)))
        sharpe = float(cr_data.get("sharpe", cr_data.get("annualized_sharpe", 1.5)))
        max_dd = float(cr_data.get("max_drawdown", 0.08))
        resolution_id = str(uuid.uuid4())

        # TiĂªu chuáº©n thá»ƒ cháº¿: OOS Sharpe >= 1.40, Max Drawdown <= 12%, Turnover Delta <= 35%
        if sharpe >= 1.40 and turnover_delta <= 0.35 and max_dd <= 0.12:
            final_res = "APPROVE_HIGH_TURNOVER_CHANGE"
            rationale = (
                f"CIO phĂª duyá»‡t Change Request {cr_id}: Äá»™ xĂ¡o trá»™n {turnover_delta*100:.1f}% náº±m trong dung sai cho phĂ©p "
                f"vĂ  Ä‘Æ°á»£c bĂ¹ Ä‘áº¯p thá»a Ä‘Ă¡ng bá»Ÿi OOS Sharpe ({sharpe:.2f} >= 1.40) cĂ¹ng Max Drawdown ({max_dd*100:.1f}% <= 12%)."
            )
        else:
            final_res = "REJECT_EXCESSIVE_TURNOVER"
            rationale = (
                f"CIO bĂ¡c bá» Change Request {cr_id}: Äá»™ xĂ¡o trá»™n danh má»¥c ({turnover_delta*100:.1f}%) hoáº·c rá»§i ro Drawdown ({max_dd*100:.1f}%) "
                f"quĂ¡ cao so vá»›i Sharpe Ä‘áº¡t Ä‘Æ°á»£c ({sharpe:.2f}). Rá»§i ro bĂ o mĂ²n thuáº¿ phĂ­ T+1.5 khĂ´ng thá»ƒ cháº¥p nháº­n."
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
        await self._publish_cio_event(verdict_payload, decision_type="MAJOR_CHANGE_APPROVAL")
        return verdict_payload

    # =========================================================================
    # 6. EMERGENCY SYSTEM CONTROL & FAILSAFE DUAL-TUNNEL (Dá»«ng Kháº©n Cáº¥p)
    # =========================================================================
    async def handle_emergency_halt(self, trigger_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        KĂ­ch hoáº¡t Tráº¡ng thĂ¡i Dá»«ng Kháº©n cáº¥p (SYSTEM HALT).
        Thá»±c thi Dual-Tunnel: ÄĂ³ng bÄƒng chiá»u MUA má»›i, nhÆ°ng báº£o vá»‡ cá»•ng xáº£ phĂ²ng vá»‡ cho Stop-loss.
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
                f"KĂCH HOáº T Dá»ªNG Há»† THá»NG KHáº¨N Cáº¤P: {reason}. Failsafe={is_failsafe}, DrawdownTier={drawdown_tier}. "
                f"ToĂ n bá»™ lá»‡nh MUA má»›i bá»‹ Ä‘Ă³ng bÄƒng tá»©c thĂ¬. Duy trĂ¬ Ä‘Æ°á»ng á»‘ng Æ°u tiĂªn cho Stop-loss báº£o toĂ n vá»‘n."
            )
        else:
            self.system_halt_state = SystemHaltState.NORMAL
            status_verdict = "SYSTEM_OPERATIONAL_NORMAL"
            actions_executed = ["RESUME_FULL_PIPELINE_OPERATIONS"]
            rationale = "Há»‡ thá»‘ng váº­n hĂ nh an toĂ n trong ngÆ°á»¡ng dung sai rá»§i ro thá»ƒ cháº¿."

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
        await self._publish_cio_event(halt_payload, decision_type="EMERGENCY_SYSTEM_HALT")
        return halt_payload

    # =========================================================================
    # 7. AUDIT TRAIL VERIFICATION & DIRECTIVE INSPECTION (Truy váº¥n & XĂ¡c thá»±c)
    # =========================================================================
    def get_active_directive(self) -> Optional[Dict[str, Any]]:
        """Láº¥y Chá»‰ thá»‹ Chiáº¿n lÆ°á»£c VÄ© mĂ´ Ä‘ang cĂ³ hiá»‡u lá»±c gáº§n nháº¥t tá»« CSDL."""
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
            logger.error(f"[StrategyCIOAgent] Lá»—i truy váº¥n active directive: {e}")
            return None

    def verify_audit_chain(self, limit: int = 100) -> Dict[str, Any]:
        """
        Kiá»ƒm toĂ¡n xĂ¡c thá»±c tĂ­nh toĂ n váº¹n máº­t mĂ£ cá»§a Sá»• cĂ¡i PhĂ¡n quyáº¿t CIO.
        TĂ¡i tĂ­nh toĂ¡n chuá»—i bÄƒm SHA-256 tá»« Canonical JSON cá»§a tá»«ng phĂ¡n quyáº¿t liĂªn tiáº¿p.
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
                    "message": "Sá»• cĂ¡i chÆ°a cĂ³ báº£n ghi phĂ¡n quyáº¿t nĂ o."
                }

            checked_count = 0
            for row in rows:
                res_id, dec_type, final_res, payload, prev_hash, stored_hash, created_at = row
                
                # TĂ¡i tĂ­nh toĂ¡n bÄƒm SHA-256 Canonical JSON
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
                "message": f"ToĂ n bá»™ {checked_count} phĂ¡n quyáº¿t Ä‘Æ°á»£c xĂ¡c thá»±c toĂ n váº¹n máº­t mĂ£ SHA-256."
            }
        except Exception as e:
            logger.error(f"[StrategyCIOAgent] Lá»—i khi xĂ¡c thá»±c chuá»—i bÄƒm audit trail: {e}")
            return {"status": "ERROR", "verified": False, "error": str(e)}

    def as_tool(self) -> Dict[str, Any]:
        """Cung cáº¥p metadata phá»¥c vá»¥ FastMCP / Chatbot Tool Call."""
        tool_meta = super().as_tool()
        tool_meta["description"] = (
            "AGENT-12: GiĂ¡m Ä‘á»‘c Äáº§u tÆ° Chiáº¿n lÆ°á»£c (CIO) & Trá»ng tĂ i Thá»ƒ cháº¿ Tá»‘i cao. "
            "Chá»‹u trĂ¡ch nhiá»‡m phĂ¢n xá»­ xung Ä‘á»™t 3 táº§ng rá»§i ro, ban hĂ nh chá»‰ thá»‹ vÄ© mĂ´, "
            "phĂª duyá»‡t ngoáº¡i lá»‡ bounded <=5% NAV, kiá»ƒm soĂ¡t dá»«ng kháº©n cáº¥p vĂ  lÆ°u sá»• cĂ¡i bÄƒm SHA-256."
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
        return await self._process_event(event_data)

    async def _process_event(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """Äiá»u phá»‘i cĂ¡c sá»± kiá»‡n Thá»ƒ cháº¿ tá»›i cĂ¡c module tháº©m quyá»n cá»§a CIO."""
        action = str(event_data.get("action", "")).strip().lower()

        # 0. Truy váº¥n tra cá»©u nhanh khĂ´ng lĂ m thay Ä‘á»•i tráº¡ng thĂ¡i
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

        # 1. Sá»± cá»‘ Kháº©n cáº¥p Failsafe hoáº·c KĂ­ch hoáº¡t Dá»«ng Há»‡ thá»‘ng
        if event_data.get("failsafe_active") or event_data.get("trigger_system_halt") or action == "emergency_halt":
            res = await self.handle_emergency_halt(event_data)
            return {"data": res, "trace": {"cio_action": "EMERGENCY_HALT"}}

        # 2. Xá»­ lĂ½ Escalation khi cĂ³ lá»‡nh vi pháº¡m tá»« Governance (Agent 11)
        if "escalation" in event_data or "violation_report" in event_data or action == "escalation":
            escalation_payload = event_data.get("escalation") or event_data.get("violation_report") or event_data
            res = await self.handle_governance_escalation(escalation_payload)
            trace = {"escalation_source": "system_governance_agent", "verdict": res["final_resolution"]}
            return {"data": res, "trace": trace}

        # 3. Escalation Change Request tá»« Governance
        if "escalation_change_request" in event_data or "change_request" in event_data or action == "change_request":
            cr_payload = event_data.get("escalation_change_request") or event_data.get("change_request") or event_data
            res = await self.handle_change_request_escalation(cr_payload)
            trace = {"escalation_source": "system_governance_change_request", "verdict": res["final_resolution"]}
            return {"data": res, "trace": trace}

        # 4. PhĂ¢n xá»­ Xung Ä‘á»™t Luáº­n Ä‘iá»ƒm (Thesis vs Counter-Thesis hoáº·c Portfolio vs Risk)
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

        # 5. YĂªu cáº§u Ngoáº¡i lá»‡ (Exception Request)
        if "exception_request" in event_data or action == "exception_request":
            req_payload = event_data.get("exception_request") or event_data
            res = await self.evaluate_exception_request(req_payload)
            return {"data": res, "trace": {"cio_action": "EXCEPTION_EVALUATION"}}

        # 6. Táº¡o BĂ¡o cĂ¡o Cáº­p nháº­t Chiáº¿n lÆ°á»£c (Strategic Memo Generator)
        if "strategic_memo" in event_data or "generate_memo" in event_data or action in ("strategic_memo", "generate_memo"):
            memo_payload = event_data.get("strategic_memo") or event_data.get("memo_data") or event_data
            ticker = str(memo_payload.get("ticker", "")).upper().strip()
            company_name = str(memo_payload.get("company_name", ticker))
            memo_text = await self.generate_strategic_memo(
                ticker=ticker,
                company_name=company_name,
                business_quality_data=memo_payload.get("business_quality_data"),
                thesis_payload=memo_payload.get("thesis_payload") or memo_payload.get("investment_thesis"),
                counter_payload=memo_payload.get("counter_payload") or memo_payload.get("counter_thesis"),
                financial_summary=memo_payload.get("financial_summary"),
                target_date=memo_payload.get("target_date"),
            )
            return {
                "data": {
                    "ticker": ticker,
                    "company_name": company_name,
                    "strategic_memo": memo_text,
                },
                "trace": {"cio_action": "STRATEGIC_MEMO_GENERATED", "ticker": ticker}
            }

        # 7. Ban hĂ nh Chá»‰ thá»‹ VÄ© mĂ´ Chiáº¿n lÆ°á»£c (Macro Review)
        macro_inputs = event_data.get("macro_data") or event_data
        res = await self.issue_strategic_directive(macro_inputs)
        trace = {"regime_context": res.get("macro_regime"), "strategic_cash": res.get("strategic_cash_target_pct")}
        return {"data": res, "trace": trace}

    async def generate_strategic_memo(
        self,
        ticker: str,
        company_name: Optional[str] = None,
        business_quality_data: Optional[Dict[str, Any]] = None,
        thesis_payload: Optional[Dict[str, Any]] = None,
        counter_payload: Optional[Dict[str, Any]] = None,
        financial_summary: Optional[Dict[str, Any]] = None,
        target_date: Optional[str] = None,
    ) -> str:
        """Táº¡o BĂ¡o cĂ¡o Cáº­p nháº­t Chiáº¿n lÆ°á»£c CIO vá»›i Persona Smart Money vĂ  cáº¥u trĂºc 4 pháº§n."""
        clean_ticker = str(ticker).upper().strip()
        c_name = company_name or clean_ticker

        # Khá»Ÿi táº¡o generator náº¿u chÆ°a cĂ³
        if not self.memo_generator:
            try:
                from app.domain.rules.strategic_memo_generator import StrategicMemoGenerator
                from app.infrastructure.llm.client import get_unified_llm_client
                self.memo_generator = StrategicMemoGenerator(llm_client=get_unified_llm_client())
            except Exception as e:
                logger.warning(f"[StrategyCIOAgent] Lá»—i táº¡o StrategicMemoGenerator: {e}")
                from app.domain.rules.strategic_memo_generator import StrategicMemoGenerator
                self.memo_generator = StrategicMemoGenerator(llm_client=None)

        return await self.memo_generator.generate_memo(
            ticker=clean_ticker,
            company_name=c_name,
            business_quality_data=business_quality_data,
            thesis_payload=thesis_payload,
            counter_payload=counter_payload,
            financial_summary=financial_summary,
            target_date=target_date,
        )
