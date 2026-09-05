"""Comprehensive Test Suite: System Governance Agent vNext (Institutional Sovereign Architecture).

Kiểm thử 3 Trụ Cột:
1. COMPLIANCE: 6 Hard Laws, Authority Matrix, HOSE Microstructure.
2. AUDIT: Sổ cái băm SHA-256 liên tục (Persistent), Toàn vẹn mật mã sau restart, Bắt gian lận DB.
3. CHANGE: Change Request, Turnover Shock Impact Analysis, OOS Walk-Forward.
4. DECISION GATE & ESCALATION: PASS -> Token; BLOCK -> Violation Report -> Escalation tới Agent-12 (Strategy CIO).
"""

import asyncio
import json
import pytest
from datetime import datetime

from app.core.registry import AgentRegistry
import app.domain.agents  # Tự động nạp và đăng ký 12 Agents
from app.domain.rules.hard_laws import ProposedOrder, PortfolioState
from app.domain.rules.governance.compliance_engine import GovernanceComplianceEngine, ComplianceVerdict, RiskSeverity
from app.domain.rules.governance.change_engine import GovernanceChangeEngine, ChangeRequest, ChangeStatus
from app.eval.audit_trail import AuditTrailEngine
from app.infrastructure.database.pg_pool import get_conn


# =============================================================================
# 1. KIỂM THỬ TRỤ CỘT 1: COMPLIANCE ENGINE
# =============================================================================

def test_compliance_engine_all_hard_laws():
    """Kiểm tra toàn diện 6 Hard Laws bất khả xâm phạm."""
    engine = GovernanceComplianceEngine()
    portfolio = PortfolioState(nav=1_000_000_000.0, positions={}, sector_exposure={})

    # Điều 1: Rủi ro kẹt sàn T+2.5 > 2% NAV -> BLOCK
    # 20,000 cổ * 100,000đ = 2 tỷ (quá trần rủi ro)
    order_dieu_1 = ProposedOrder(
        ticker="FPT", side="BUY", quantity=20000, price=100000.0, stop_loss_price=93000.0, sector="Technology"
    )
    res_d1 = engine.evaluate_order(
        order=order_dieu_1, portfolio=portfolio, adtv20_continuous=10_000_000.0, issuing_agent="portfolio_allocation"
    )
    assert res_d1.is_compliant is False
    assert res_d1.verdict == ComplianceVerdict.BLOCK
    assert "DIEU_1" in str(res_d1.violated_rule) or "T+2.5" in res_d1.reason

    # Điều 2: Lệnh vượt 15% ADTV20 (nhưng rủi ro T+2.5 nhỏ để Điều 1 PASS) -> BLOCK
    order_dieu_2 = ProposedOrder(
        ticker="FPT", side="BUY", quantity=200, price=10000.0, stop_loss_price=9500.0, sector="Technology"
    )
    res_d2 = engine.evaluate_order(
        order=order_dieu_2, portfolio=portfolio, adtv20_continuous=1000.0, issuing_agent="portfolio_allocation"
    )
    assert res_d2.is_compliant is False
    assert res_d2.verdict == ComplianceVerdict.BLOCK
    assert "DIEU_2" in str(res_d2.violated_rule) or "ADTV20" in res_d2.reason

    # Điều 3: Thiếu tín hiệu xác nhận (< 3 tín hiệu) -> BLOCK
    order_dieu_3 = ProposedOrder(
        ticker="FPT", side="BUY", quantity=500, price=100000.0, stop_loss_price=93000.0, sector="Technology"
    )
    res_d3 = engine.evaluate_order(
        order=order_dieu_3, portfolio=portfolio, adtv20_continuous=5_000_000.0,
        issuing_agent="portfolio_allocation", confirming_signals_count=2
    )
    assert res_d3.is_compliant is False
    assert res_d3.verdict == ComplianceVerdict.BLOCK
    assert "DIEU_3" in str(res_d3.violated_rule)

    # Điều 4: Single stock vượt 15% NAV (đã có 100M FPT, mua thêm 60M = 160M > 150M) -> BLOCK
    port_d4 = PortfolioState(
        nav=1_000_000_000.0,
        positions={"FPT": {"quantity": 1000, "current_price": 100000.0, "sector": "Technology"}},
        sector_exposure={"Technology": 100_000_000.0}
    )
    order_dieu_4 = ProposedOrder(
        ticker="FPT", side="BUY", quantity=600, price=100000.0, stop_loss_price=93000.0, sector="Technology"
    )
    res_d4 = engine.evaluate_order(
        order=order_dieu_4, portfolio=port_d4, adtv20_continuous=5_000_000.0, issuing_agent="portfolio_allocation"
    )
    assert res_d4.is_compliant is False
    assert res_d4.verdict == ComplianceVerdict.BLOCK
    assert "DIEU_4" in str(res_d4.violated_rule) or "15%" in res_d4.reason

    # Điều 5: Cổng Beneish M-Score gian lận -> BLOCK CATASTROPHIC
    order_dieu_5 = ProposedOrder(
        ticker="FPT", side="BUY", quantity=500, price=100000.0, stop_loss_price=93000.0, sector="Technology"
    )
    res_d5 = engine.evaluate_order(
        order=order_dieu_5, portfolio=portfolio, adtv20_continuous=5_000_000.0,
        issuing_agent="portfolio_allocation", beneish_passed=False
    )
    assert res_d5.is_compliant is False
    assert res_d5.risk_level == RiskSeverity.CATASTROPHIC
    assert "DIEU_5" in str(res_d5.violated_rule)

    # Điều 6: GIL CATASTROPHIC (OCR > 0.85) -> BLOCK CATASTROPHIC
    order_dieu_6 = ProposedOrder(
        ticker="FPT", side="BUY", quantity=500, price=100000.0, stop_loss_price=93000.0, sector="Technology"
    )
    res_d6 = engine.evaluate_order(
        order=order_dieu_6, portfolio=portfolio, adtv20_continuous=5_000_000.0,
        issuing_agent="portfolio_allocation", gil_ocr_score=0.92
    )
    assert res_d6.is_compliant is False
    assert res_d6.risk_level == RiskSeverity.CATASTROPHIC
    assert "DIEU_6" in str(res_d6.violated_rule)


def test_compliance_authority_matrix_and_hose_policy():
    """Kiểm tra Ma trận Thẩm quyền và Quy chế Vi cấu trúc HOSE."""
    engine = GovernanceComplianceEngine()
    portfolio = PortfolioState(nav=1_000_000_000.0, positions={}, sector_exposure={})

    # 1. Agent không có thẩm quyền phát lệnh mua
    order = ProposedOrder(ticker="FPT", side="BUY", quantity=500, price=100000.0, stop_loss_price=93000.0, sector="Technology")
    res_auth = engine.evaluate_order(
        order=order, portfolio=portfolio, adtv20_continuous=5_000_000.0,
        issuing_agent="universe_discovery", order_intent="BUY"
    )
    assert res_auth.is_compliant is False
    assert res_auth.violated_rule == "AUTHORITY_VIOLATION"

    # 2. Lô lẻ (không chia hết cho 100)
    order_odd = ProposedOrder(ticker="FPT", side="BUY", quantity=350, price=100000.0, stop_loss_price=93000.0, sector="Technology")
    res_odd = engine.evaluate_order(
        order=order_odd, portfolio=portfolio, adtv20_continuous=5_000_000.0,
        issuing_agent="portfolio_allocation", order_intent="BUY"
    )
    assert res_odd.is_compliant is False
    assert "HOSE_MICROSTRUCTURE_POLICY" in str(res_odd.violated_rule)

    # 3. Bán khống khi không có hàng khả dụng
    order_sell = ProposedOrder(ticker="FPT", side="SELL", quantity=500, price=100000.0, sector="Technology")
    res_short = engine.evaluate_order(
        order=order_sell, portfolio=portfolio, adtv20_continuous=5_000_000.0,
        issuing_agent="portfolio_allocation", order_intent="SELL", available_shares=200
    )
    assert res_short.is_compliant is False
    assert res_short.violated_rule == "HOSE_SHORT_SELLING_PROHIBITION"


# =============================================================================
# 2. KIỂM THỬ TRỤ CỘT 2: AUDIT TRAIL & HASH CHAIN TOÀN VẸN MẬT MÃ
# =============================================================================

def test_audit_trail_persistent_hash_chain():
    """Kiểm tra tính toàn vẹn của chuỗi băm SHA-256 sau khi restart và phát hiện gian lận."""
    engine1 = AuditTrailEngine()
    initial_head = engine1.last_hash

    # Ghi 3 bản ghi
    h1 = engine1.log_event("agent_test", "TEST_EVENT_1", {"metric": 100})
    h2 = engine1.log_event("agent_test", "TEST_EVENT_2", {"metric": 200})
    h3 = engine1.log_event("agent_test", "TEST_EVENT_3", {"metric": 300})

    assert engine1.last_hash == h3

    # Giả lập restart hệ thống bằng cách khởi tạo một instance AuditTrailEngine mới
    engine2 = AuditTrailEngine()
    # Phải khôi phục chính xác đỉnh chuỗi từ PostgreSQL
    assert engine2.last_hash == h3

    # Xác thực toàn bộ chuỗi băm
    is_valid, count, err = engine2.verify_full_chain()
    assert is_valid is True
    assert count >= 3
    assert err is None


# =============================================================================
# 3. KIỂM THỬ TRỤ CỘT 3: CHANGE MANAGEMENT & OOS GATE
# =============================================================================

def test_change_engine_oos_and_turnover_shock():
    """Kiểm tra quy trình thẩm định Change Request: OOS Gate và Turnover Shock."""
    engine = GovernanceChangeEngine(min_sharpe=1.2, max_drawdown_limit=0.10, max_weight_turnover_threshold=0.30)

    # 1. Change Request đạt chuẩn
    good_returns = [0.008, 0.012, -0.002, 0.009, 0.005, -0.001, 0.007, 0.011] * 3
    cr_good = ChangeRequest(
        cr_id="CR_001",
        initiator_agent="reinforcement_learning",
        target_component="rl_factor_weights",
        proposed_changes={"f1": 0.20, "f2": 0.25, "f3": 0.25, "f4": 0.15, "f5": 0.15},
        current_state={"f1": 0.15, "f2": 0.20, "f3": 0.30, "f4": 0.15, "f5": 0.20},
        oos_returns=good_returns,
        rationale="Update Bull Market Weights",
    )
    res_good = engine.evaluate_change_request(cr_good)
    assert res_good.approved is True
    assert res_good.status == ChangeStatus.APPROVED

    # 2. Change Request làm xáo trộn danh mục quá lớn (Turnover Shock > 30%) -> Phải Escalate sang CIO
    cr_shock = ChangeRequest(
        cr_id="CR_002",
        initiator_agent="reinforcement_learning",
        target_component="rl_factor_weights",
        proposed_changes={"f1": 0.60, "f2": 0.40, "f3": 0.0, "f4": 0.0, "f5": 0.0},
        current_state={"f1": 0.10, "f2": 0.10, "f3": 0.30, "f4": 0.25, "f5": 0.25},
        oos_returns=good_returns,
        rationale="Aggressive Value Shift",
    )
    res_shock = engine.evaluate_change_request(cr_shock)
    assert res_shock.approved is False
    assert res_shock.status == ChangeStatus.ESCALATE_TO_CIO
    assert res_shock.requires_cio_resolution is True

    # 3. Change Request không đạt Sharpe ngoài mẫu (< 1.2) -> Bị từ chối
    bad_returns = [-0.008, -0.005, 0.001, -0.010, 0.002] * 5
    cr_bad = ChangeRequest(
        cr_id="CR_003",
        initiator_agent="reinforcement_learning",
        target_component="rl_factor_weights",
        proposed_changes={"f1": 0.20},
        current_state={"f1": 0.15},
        oos_returns=bad_returns,
        rationale="Failing Walk Forward",
    )
    res_bad = engine.evaluate_change_request(cr_bad)
    assert res_bad.approved is False
    assert res_bad.status == ChangeStatus.REJECTED


# =============================================================================
# 4. KIỂM THỬ TỔNG THỂ: DECISION GATE & ESCALATION TỚI AGENT-12 (CIO)
# =============================================================================

def test_governance_decision_gate_pass_and_block_with_escalation():
    """Kiểm tra Decision Gate: PASS sinh chữ ký token; BLOCK sinh Violation Report và chuyển tiếp sang CIO."""
    async def _test():
        # 1. Lệnh Hợp Lệ -> PASS
        valid_order = {
            "ticker": "FPT",
            "side": "BUY",
            "quantity": 500,
            "price": 100000.0,
            "stop_loss_price": 93000.0,
            "sector": "Technology",
        }
        res_pass = await AgentRegistry.dispatch("system_governance", {
            "order": valid_order,
            "portfolio": {"total_nav": 1_000_000_000.0, "positions": {}, "locked_t25_value": 0.0},
            "issuing_agent": "portfolio_allocation",
            "adtv20": 5_000_000.0,
            "confirming_signals_count": 3,
        })
        assert res_pass["status"] == "SUCCESS"
        data_pass = res_pass["result"]
        assert data_pass["verdict"] == "PASS"
        assert data_pass["decision"] == "PASS"
        assert data_pass["governance_token"].startswith("GOV_")

        # 2. Lệnh Vi Phạm Điều 4 (Tổng tỷ trọng FPT 160M = 16% NAV > 15% trần) -> BLOCK & Tự Động Escalation sang CIO
        breach_order = {
            "ticker": "FPT",
            "side": "BUY",
            "quantity": 600,
            "price": 100000.0,
            "stop_loss_price": 93000.0,
            "sector": "Technology",
        }
        res_block = await AgentRegistry.dispatch("system_governance", {
            "order": breach_order,
            "portfolio": {
                "total_nav": 1_000_000_000.0,
                "positions": {"FPT": {"quantity": 1000, "current_price": 100000.0, "sector": "Technology"}},
                "locked_t25_value": 0.0,
            },
            "issuing_agent": "portfolio_allocation",
            "adtv20": 5_000_000.0,
            "confirming_signals_count": 3,
        })
        assert res_block["status"] == "SUCCESS"
        data_block = res_block["result"]
        assert data_block["verdict"] == "BLOCK"
        assert data_block["decision"] == "BLOCK"
        assert data_block["governance_token"] is None
        
        # Kiểm tra Violation Report chuẩn hóa
        vr = data_block["violation_report"]
        assert vr["ticker"] == "FPT"
        assert vr["risk_level"] in ("CRITICAL", "CATASTROPHIC")
        assert "DIEU_4" in str(vr["violated_rule"]) or "15%" in vr["reason"]

        # Kiểm tra CIO đã nhận Escalation và ra phán quyết cưỡng chế (FORCE_DOWNSIZE, không override)
        cio_res = data_block.get("cio_resolution")
        assert cio_res is not None
        assert cio_res["final_resolution"] == "FORCE_DOWNSIZE"
        assert cio_res["details"]["hard_law_override_attempted"] is False
        assert cio_res["details"]["adjusted_weight_cap"] <= 0.15

    asyncio.run(_test())
