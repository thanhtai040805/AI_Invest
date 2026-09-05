"""Institutional Test Suite for Agent 12 (Strategy CIO Agent) — IOS v5.1.

Kiểm thử toàn diện 6 Trụ cột Thể chế:
1. Phân định Minh bạch 3 Tầng Rủi ro (Hard Law vs Critical Tail Risk vs Normal Risk).
2. Xử lý Escalation từ Governance & Bảo vệ Tính Bất khả Xâm phạm của Hard Laws.
3. Quản trị Ngoại lệ có Giới hạn Biên An Toàn (Exception Boundedness & Dual-Key Co-sign).
4. Ban hành Chỉ thị Chiến lược Vĩ mô (Strategic Directive Versioning & Flash Invalidation).
5. Thẩm định Thay đổi Mô hình ML (Major Change Request & Turnover Shock Control).
6. Hệ thống Dừng Khẩn cấp Dual-Tunnel (Emergency Halt & Freeze New Orders).
7. Chuỗi băm Mật mã SHA-256 Bất biến (Canonical Hash Chaining & Anchor).
"""

import asyncio
import pytest
from datetime import datetime

from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp toàn bộ 12 agents
from app.domain.agents.strategy_cio import StrategyCIOAgent, MacroRegime, RiskAppetite, SystemHaltState


@pytest.fixture
def cio_agent():
    agent = AgentRegistry.get_agent("strategy_cio")
    if agent is None:
        agent = StrategyCIOAgent()
        AgentRegistry.register(agent)
    return agent


# =============================================================================
# 1. KIỂM THỬ PHÂN TÁCH MINH BẠCH 3 TẦNG RỦI RO (PHẢN BIỆN 5)
# =============================================================================

def test_conflict_resolution_3_tier_risk_hierarchy(cio_agent):
    """Kiểm tra phân định rạch ròi 3 Tầng Rủi ro trong Conflict Resolution."""
    async def _run():
        # ---------------------------------------------------------------------
        # TẦNG 1: HARD LAW (Hiến pháp) -> BẮT BUỘC UPHOLD_BLOCK (Zero Tolerance)
        # ---------------------------------------------------------------------
        res_t1_dieu1 = await cio_agent.resolve_conflict({
            "ticker": "HPG",
            "violated_rule": "DIEU_1_HARD_STOP_LOSS_2PCT_NAV",
            "hard_law_breach_detected": True,
            "counter_verdict": "CONDITIONAL",  # Dù Counter nói conditional nhưng vi phạm Hard Law
            "cts_score": 40.0,
        })
        assert res_t1_dieu1["final_resolution"] == "UPHOLD_BLOCK"
        assert res_t1_dieu1["severity_tier"] == "TIER_1_HARD_LAW_INVARIANT"
        assert res_t1_dieu1["weight_cap"] == 0.0
        assert "NO_NEW_BUY_ORDERS" in res_t1_dieu1["conditions"]

        res_t1_gil = await cio_agent.resolve_conflict({
            "ticker": "NVL",
            "counter_verdict": "GIL_CATASTROPHIC",
            "block_reasons": ["DIEU_6_GIL_CATASTROPHIC_CROSS_OWNERSHIP"],
        })
        assert res_t1_gil["final_resolution"] == "UPHOLD_BLOCK"
        assert res_t1_gil["severity_tier"] == "TIER_1_HARD_LAW_INVARIANT"
        assert res_t1_gil["weight_cap"] == 0.0

        # ---------------------------------------------------------------------
        # TẦNG 2: CRITICAL TAIL RISK -> DISCRETIONARY_BLOCK (Phán đoán Chiến lược)
        # ---------------------------------------------------------------------
        res_t2 = await cio_agent.resolve_conflict({
            "ticker": "DIG",
            "counter_verdict": "BLOCK",
            "cts_score": 85.0,  # CTS >= 80
            "block_reasons": ["HEAVY_INSIDER_SELLING", "ACCRUAL_ANOMALY"],
        })
        assert res_t2["final_resolution"] == "DISCRETIONARY_BLOCK"
        assert res_t2["severity_tier"] == "TIER_2_CRITICAL_TAIL_RISK"
        assert res_t2["weight_cap"] == 0.0
        assert "RETURN_TO_RESEARCH_QUEUE" in res_t2["conditions"]

        # ---------------------------------------------------------------------
        # TẦNG 3: NORMAL RISK -> PROCEED_WITH_PENALTY (Điều tiết vốn bằng Sizing)
        # ---------------------------------------------------------------------
        # 3A. Rủi ro thị trường đáng lưu ý (CTS = 60, Counter = CONDITIONAL)
        res_t3_penalty = await cio_agent.resolve_conflict({
            "ticker": "SSI",
            "counter_verdict": "CONDITIONAL",
            "cts_score": 60.0,
            "block_reasons": ["HIGH_PE_VALUATION", "MARKET_VOLATILITY"],
        })
        assert res_t3_penalty["final_resolution"] == "PROCEED_WITH_PENALTY"
        assert res_t3_penalty["severity_tier"] == "TIER_3_NORMAL_BUSINESS_RISK"
        assert res_t3_penalty["weight_cap"] == 0.08
        assert "APPLY_RISK_PENALTY_0_5" in res_t3_penalty["conditions"]

        # 3B. Rủi ro thông thường thấp (CTS = 20, Counter = PROCEED)
        res_t3_standard = await cio_agent.resolve_conflict({
            "ticker": "FPT",
            "counter_verdict": "PROCEED",
            "cts_score": 20.0,
        })
        assert res_t3_standard["final_resolution"] == "PROCEED_WITH_PENALTY"
        assert res_t3_standard["severity_tier"] == "TIER_3_NORMAL_BUSINESS_RISK"
        assert res_t3_standard["weight_cap"] == 0.15

    asyncio.run(_run())


# =============================================================================
# 2. KIỂM THỬ XỬ LÝ ESCALATION TỪ GOVERNANCE AGENT
# =============================================================================

def test_governance_escalation_handling(cio_agent):
    """Kiểm tra xử lý Escalation vi phạm từ Agent 11."""
    async def _run():
        # Vi phạm Điều 4 (Trần tỷ trọng 15%) -> FORCE_DOWNSIZE (Áp trần 10%, không override)
        esc_dieu4 = {
            "report_id": "REP_001",
            "ticker": "FPT",
            "violated_rule": "DIEU_4_CONCENTRATION_MAX_15PCT_STOCK_35PCT_SECTOR",
            "risk_level": "CRITICAL",
            "reason": "Single stock concentration reaches 16.0% NAV > 15.0% cap",
        }
        res_d4 = await cio_agent.handle_governance_escalation(esc_dieu4)
        assert res_d4["final_resolution"] == "FORCE_DOWNSIZE"
        assert res_d4["details"]["hard_law_override_attempted"] is False
        assert res_d4["details"]["adjusted_weight_cap"] <= 0.15

        # Vi phạm Beneish (Điều 5) -> CONFIRM_BLOCK (Hủy lệnh hoàn toàn)
        esc_dieu5 = {
            "report_id": "REP_002",
            "ticker": "RÁC",
            "violated_rule": "DIEU_5_BENEISH_GATE",
            "risk_level": "CATASTROPHIC",
            "reason": "Beneish M-Score = -1.45 > -1.78 threshold",
        }
        res_d5 = await cio_agent.handle_governance_escalation(esc_dieu5)
        assert res_d5["final_resolution"] == "CONFIRM_BLOCK"
        assert res_d5["details"]["action"] == "CANCEL_ORDER"

    asyncio.run(_run())


# =============================================================================
# 3. KIỂM THỬ QUẢN TRỊ NGOẠI LỆ CÓ BIÊN AN TOÀN (EXCEPTION BOUNDEDNESS)
# =============================================================================

def test_exception_management_boundedness(cio_agent):
    """Kiểm tra quy định Boundedness Check và Dual-Key Governance Co-sign."""
    async def _run():
        # Cố tình lách Hard Law -> Bị bác bỏ ngay lập tức
        res_bypass = await cio_agent.evaluate_exception_request({
            "reason": "Yêu cầu ngoại lệ bỏ qua DIEU_1_HARD_STOP_LOSS cho deal lớn",
            "max_exposure_nav_pct": 3.0,
            "duration_hours": 24.0,
        })
        assert res_bypass["approved"] is False
        assert res_bypass["final_resolution"] == "REJECT_HARD_LAW_BYPASS"
        assert res_bypass["governance_cosign"] is False

        # Đề xuất tỷ trọng vượt trần 5.0% NAV -> Bị bác bỏ
        res_over_exposure = await cio_agent.evaluate_exception_request({
            "reason": "Cơ hội thâu tóm đột xuất",
            "max_exposure_nav_pct": 8.0,  # > 5%
            "duration_hours": 24.0,
        })
        assert res_over_exposure["approved"] is False
        assert res_over_exposure["final_resolution"] == "REJECT_HARD_LAW_BYPASS"

        # Đề xuất thời hạn vượt trần 48.0 giờ -> Bị bác bỏ
        res_over_duration = await cio_agent.evaluate_exception_request({
            "reason": "Chờ đợi ĐHCĐ bất thường",
            "max_exposure_nav_pct": 4.0,
            "duration_hours": 72.0,  # > 48h
        })
        assert res_over_duration["approved"] is False
        assert res_over_duration["final_resolution"] == "REJECT_EXCESSIVE_DURATION"

        # Ngoại lệ hợp lệ trong biên an toàn -> Phê duyệt kèm Governance Co-sign
        res_valid = await cio_agent.evaluate_exception_request({
            "reason": "Điều chỉnh khớp lệnh lô thỏa thuận ngoại lai",
            "scope": "TACTICAL_ALLOCATION",
            "max_exposure_nav_pct": 3.5,
            "duration_hours": 24.0,
        })
        assert res_valid["approved"] is True
        assert res_valid["final_resolution"] == "APPROVE_BOUNDED_EXCEPTION"
        assert res_valid["governance_cosign"] is True
        assert res_valid["max_exposure_nav_pct"] == 3.5

    asyncio.run(_run())


# =============================================================================
# 4. KIỂM THỬ BAN HÀNH CHỈ THỊ CHIẾN LƯỢC VĨ MÔ & FLASH INVALIDATION
# =============================================================================

def test_strategic_directive_generation(cio_agent):
    """Kiểm tra sinh Directive vĩ mô, Cash Target và Flash Invalidation Thresholds."""
    async def _run():
        # Kịch bản Khủng hoảng (VIX vọt xà, độ rộng sụp đổ)
        crisis_macro = {
            "credit_growth_yoy": 8.0,
            "sbv_policy_rate": 6.5,
            "vix_vn_analog": 38.0,  # > 35
            "market_breadth_ma20_pct": 12.0,  # < 15
        }
        res_crisis = await cio_agent.issue_strategic_directive(crisis_macro)
        assert res_crisis["macro_regime"] == "CRISIS"
        assert res_crisis["risk_appetite"] == "CAPITAL_PRESERVATION"
        assert res_crisis["strategic_cash_target_pct"] >= 50.0
        assert res_crisis["sector_tilt"]["REAL_ESTATE"] == "UNDERWEIGHT"
        assert "flash_invalidation_thresholds" in res_crisis

        # Kịch bản Bull Trending ổn định
        bull_macro = {
            "credit_growth_yoy": 13.5,
            "sbv_policy_rate": 4.0,
            "vix_vn_analog": 16.0,
            "market_breadth_ma20_pct": 65.0,
        }
        res_bull = await cio_agent.issue_strategic_directive(bull_macro)
        assert res_bull["macro_regime"] == "BULL"
        assert res_bull["risk_appetite"] == "AGGRESSIVE"
        assert res_bull["strategic_cash_target_pct"] <= 15.0

    asyncio.run(_run())


# =============================================================================
# 5. KIỂM THỬ THẨM ĐỊNH THAY ĐỔI MÔ HÌNH (MAJOR CHANGE REQUEST)
# =============================================================================

def test_major_change_request_evaluation(cio_agent):
    """Kiểm tra thẩm định Change Request: Cho phép Sharpe cao xáo trộn vừa phải, bác bỏ xáo trộn quá lớn."""
    async def _run():
        # OOS Sharpe tốt (1.6) và Turnover vừa phải (25%) -> Phê duyệt
        cr_good = {
            "cr_id": "CR_STABLE",
            "turnover_delta": 0.25,
            "sharpe": 1.6,
            "max_drawdown": 0.08,
        }
        res_good = await cio_agent.handle_change_request_escalation(cr_good)
        assert res_good["final_resolution"] == "APPROVE_HIGH_TURNOVER_CHANGE"

        # Turnover quá lớn (40% > 35%) -> Bác bỏ vì rủi ro bào mòn thuế phí
        cr_excessive = {
            "cr_id": "CR_CHURN",
            "turnover_delta": 0.40,
            "sharpe": 1.45,
            "max_drawdown": 0.10,
        }
        res_bad = await cio_agent.handle_change_request_escalation(cr_excessive)
        assert res_bad["final_resolution"] == "REJECT_EXCESSIVE_TURNOVER"

    asyncio.run(_run())


# =============================================================================
# 6. KIỂM THỬ EMERGENCY SYSTEM CONTROL (DUAL-TUNNEL HALT)
# =============================================================================

def test_emergency_system_halt_dual_tunnel(cio_agent):
    """Kiểm tra cơ chế Dừng Khẩn Cấp Dual-Tunnel: Chặn lệnh BUY mới, mở kênh Stop-loss."""
    async def _run():
        trigger_halt = {
            "reason": "CRITICAL_FAILSAFE_BROKER_DISCONNECT",
            "failsafe_active": True,
            "drawdown_tier": "RED",
        }
        res_halt = await cio_agent.handle_emergency_halt(trigger_halt)
        assert res_halt["system_state"] == SystemHaltState.FREEZE_NEW_ORDERS.value
        assert res_halt["final_resolution"] == "SYSTEM_HALTED_FREEZE_NEW_ORDERS"
        assert "BLOCK_ALL_INCOMING_BUY_ORDERS" in res_halt["actions_executed"]
        assert "ALLOW_DEFENSIVE_STOP_LOSS_VIA_SMART_SLICING" in res_halt["actions_executed"]

    asyncio.run(_run())


# =============================================================================
# 7. KIỂM THỬ TOÀN VẸN CHUỖI BĂM SHA-256 CANONICAL HASH
# =============================================================================

def test_cryptographic_hash_chaining(cio_agent):
    """Kiểm tra chuỗi băm SHA-256 liên kết qua các phán quyết liên tiếp."""
    async def _run():
        h0 = cio_agent.last_decision_hash

        res1 = await cio_agent.resolve_conflict({
            "ticker": "VNM",
            "counter_verdict": "PROCEED",
            "cts_score": 15.0,
        })
        h1 = res1["decision_hash"]
        assert h1 is not None and len(h1) == 64
        assert h1 != h0

        res2 = await cio_agent.evaluate_exception_request({
            "reason": "Ngoại lệ điều phối dòng tiền",
            "max_exposure_nav_pct": 2.0,
            "duration_hours": 12.0,
        })
        h2 = res2["decision_hash"]
        assert h2 is not None and len(h2) == 64
        assert h2 != h1
        assert cio_agent.last_decision_hash == h2

    asyncio.run(_run())


# =============================================================================
# 8. KIỂM THỬ XÁC THỰC SỔ CÁI BĂM SHA-256 (VERIFY AUDIT CHAIN)
# =============================================================================

def test_verify_audit_chain_integrity(cio_agent):
    """Kiểm toán toàn vẹn mật mã: hàm verify_audit_chain xác thực tính bất biến của chuỗi băm."""
    verify_res = cio_agent.verify_audit_chain(limit=50)
    assert verify_res["verified"] is True
    assert verify_res["status"] in ("VERIFIED_VALID", "EMPTY")
    assert "records_checked" in verify_res


# =============================================================================
# 9. KIỂM THỬ BẢO VỆ POSTGRES VỚI MÃ ĐỊNH DANH TÙY BIẾN (NON-UUID SAFEGUARD)
# =============================================================================

def test_custom_non_uuid_identifiers_safeguard(cio_agent):
    """Đảm bảo các chuỗi ID tùy biến (ví dụ EXC-2026-CUSTOM-001) không làm gãy type PostgreSQL UUID."""
    async def _run():
        res_custom = await cio_agent.evaluate_exception_request({
            "exception_id": "EXC-2026-CUSTOM-001",
            "reason": "Phân bổ ngoại lệ đặc biệt",
            "max_exposure_nav_pct": 2.5,
            "duration_hours": 12.0,
        })
        assert res_custom["approved"] is True
        assert res_custom["exception_id"] == "EXC-2026-CUSTOM-001"
        assert res_custom["decision_hash"] is not None

        # Conflict với thesis_id tùy biến (không phải UUID 36 ký tự)
        res_conflict_custom = await cio_agent.resolve_conflict({
            "thesis_id": "CUSTOM_THESIS_VNM_999",
            "ticker": "VNM",
            "counter_verdict": "PROCEED",
            "cts_score": 10.0,
        })
        assert res_conflict_custom["final_resolution"] == "PROCEED_WITH_PENALTY"
        assert res_conflict_custom["penalty_factor"] == 1.0

    asyncio.run(_run())


# =============================================================================
# 10. KIỂM THỬ CHẾ ĐỘ THỊ TRƯỜNG SIDEWAYS VÀ TRUY VẤN ACTIVE DIRECTIVE
# =============================================================================

def test_sideways_macro_regime_and_active_directive(cio_agent):
    """Kiểm tra chế độ SIDEWAYS và chức năng tra cứu active directive."""
    async def _run():
        sideways_macro = {
            "credit_growth_yoy": 10.0,
            "sbv_policy_rate": 5.0,
            "vix_vn_analog": 22.0,           # VIX 20-25
            "market_breadth_ma20_pct": 42.0,  # 35-50%
        }
        res_sideways = await cio_agent.issue_strategic_directive(sideways_macro)
        assert res_sideways["macro_regime"] == "SIDEWAYS"
        assert res_sideways["risk_appetite"] == "NEUTRAL"
        assert res_sideways["strategic_cash_target_pct"] == 25.0
        assert res_sideways["executive_rationale"] is not None

        # Truy vấn active directive vừa sinh
        active = cio_agent.get_active_directive()
        assert active is not None
        assert active["macro_regime"] == "SIDEWAYS"
        assert active["strategic_cash_target_pct"] == 25.0

    asyncio.run(_run())


# =============================================================================
# 11. KIỂM THỬ ĐIỀU PHỐI TRUY VẤN NHANH QUA AGENTREGISTRY (PROCESS DISPATCHER)
# =============================================================================

def test_query_dispatcher_actions(cio_agent):
    """Kiểm tra các action tra cứu nhanh không tạo rác dữ liệu: get_system_status, verify_audit_chain."""
    async def _run():
        status_resp = await AgentRegistry.dispatch("strategy_cio", {"action": "get_system_status"})
        assert status_resp["status"] == "SUCCESS"
        assert status_resp["result"]["data"]["system_halt_state"] in ("NORMAL", "FREEZE_NEW_ORDERS")

        verify_resp = await AgentRegistry.dispatch("strategy_cio", {"action": "verify_audit_chain", "limit": 20})
        assert verify_resp["status"] == "SUCCESS"
        assert verify_resp["result"]["data"]["verified"] is True

        tool_meta = cio_agent.as_tool()
        assert "supported_actions" in tool_meta
        assert "verify_audit_chain" in tool_meta["supported_actions"]

    asyncio.run(_run())

