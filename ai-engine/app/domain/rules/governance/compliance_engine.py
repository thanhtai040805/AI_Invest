"""Governance Compliance Engine — Institutional Sovereign Gatekeeper (IOS v5.1).

Nhiệm vụ thể chế:
1. Thẩm định 6 Hard Laws bất khả xâm phạm (Điều 1 -> Điều 6). Không ai có quyền Override.
2. Kiểm tra Ma trận Thẩm quyền (Authority Matrix) phân quyền tác vụ giữa các Agent.
3. Kiểm tra Chính sách Vi cấu trúc Sở Giao dịch Chứng khoán TP.HCM (HOSE Policy).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from app.domain.rules.hard_laws import HardLawEngine, ProposedOrder, PortfolioState, HardLawCheck, HardLaw

logger = logging.getLogger(__name__)


class ComplianceVerdict(str, Enum):
    PASS = "PASS"
    BLOCK = "BLOCK"
    CONDITIONAL = "CONDITIONAL"


class RiskSeverity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    CRITICAL = "CRITICAL"
    CATASTROPHIC = "CATASTROPHIC"


@dataclass
class ComplianceResult:
    is_compliant: bool
    verdict: ComplianceVerdict
    violated_rule: Optional[str] = None
    risk_level: RiskSeverity = RiskSeverity.NONE
    reason: str = ""
    details: Dict[str, Any] = field(default_factory=dict)


class GovernanceComplianceEngine:
    """
    Trụ Cột 1 (COMPLIANCE): Trọng tài Thể chế & Gác cổng Pháp chế / Hiến pháp.
    """

    # Ma trận thẩm quyền phân công giữa các Agent
    ALLOWED_AUTHORITY_MAP: Dict[str, List[str]] = {
        "portfolio_allocation": ["BUY", "SELL", "REBALANCE", "NEW_BUY", "REBALANCE_BUY", "REBALANCE_SELL"],
        "position_monitoring": ["EMERGENCY_STOP_LOSS", "TAKE_PROFIT", "THESIS_EXIT", "STOP_LOSS"],
        "trade_execution": ["EXECUTE_VWAP", "EXECUTE_LIMIT", "SLICE"],
        "strategy_cio": ["ARBITRATE_CONFLICT", "RESIZE_COMPLIANT", "MACRO_ALLOCATION"],
        "system_governance": ["KILL_SWITCH_HALT", "EMERGENCY_FREEZE"],
    }

    def __init__(self):
        self.hard_law_engine = HardLawEngine()

    def validate_authority(self, issuing_agent: str, order_intent: str) -> Tuple[bool, str]:
        """Kiểm tra thẩm quyền phát lệnh của Agent."""
        agent_clean = str(issuing_agent).strip().lower()
        intent_clean = str(order_intent).strip().upper()

        allowed_intents = self.ALLOWED_AUTHORITY_MAP.get(agent_clean)
        if not allowed_intents:
            return False, f"Agent '{issuing_agent}' không nằm trong Danh bạ Thẩm quyền (Authority Matrix) được phép phát lệnh."

        if intent_clean not in allowed_intents:
            return False, f"Agent '{issuing_agent}' KHÔNG CÓ THẨM QUYỀN phát lệnh loại '{order_intent}'. Thẩm quyền cho phép: {allowed_intents}."

        return True, "Hợp lệ thẩm quyền."

    def validate_hose_microstructure(self, ticker: str, quantity: int, price: float) -> Tuple[bool, str]:
        """Kiểm tra chính sách vi cấu trúc sàn HOSE: lô 100, trần 500k cổ phiếu, bước giá."""
        if quantity <= 0:
            return False, f"Khối lượng đặt lệnh ({quantity}) phải lớn hơn 0."

        if quantity % 100 != 0:
            return False, f"Khối lượng đặt lệnh ({quantity}) vi phạm quy chế Lô chẵn 100 của sàn HOSE."

        if quantity > 500_000:
            return False, f"Khối lượng đặt lệnh ({quantity:,.0f}) vượt trần 500,000 cổ/lệnh đơn của sàn HOSE. Bắt buộc phải chia nhỏ lệnh (Slicing)."

        if price <= 0:
            return False, f"Giá đặt lệnh ({price}) không hợp lệ."

        # Kiểm tra bước giá HOSE
        if price < 10_000:
            tick = 10.0
        elif price < 50_000:
            tick = 50.0
        else:
            tick = 100.0

        remainder = round(price % tick, 2)
        if remainder > 1e-4 and abs(remainder - tick) > 1e-4:
            return False, f"Giá {price:,.1f} không khớp bước giá {tick:.0f} VND của sàn HOSE."

        return True, "Thỏa mãn quy chế vi cấu trúc HOSE."

    def evaluate_order(
        self,
        order: ProposedOrder,
        portfolio: PortfolioState,
        adtv20_continuous: float,
        issuing_agent: str,
        order_intent: str = "BUY",
        confirming_signals_count: int = 3,
        beneish_passed: bool = True,
        gil_ocr_score: float = 0.0,
        available_shares: Optional[int] = None,
    ) -> ComplianceResult:
        """
        Thẩm tra toàn diện một lệnh trước khi thực thi (Pre-Trade Full Compliance Check).
        """
        ticker_clean = str(order.ticker).upper().strip()

        # 1. Authority Check
        auth_ok, auth_reason = self.validate_authority(issuing_agent, order_intent)
        if not auth_ok:
            return ComplianceResult(
                is_compliant=False,
                verdict=ComplianceVerdict.BLOCK,
                violated_rule="AUTHORITY_VIOLATION",
                risk_level=RiskSeverity.CRITICAL,
                reason=auth_reason,
                details={"issuing_agent": issuing_agent, "order_intent": order_intent},
            )

        # 2. Hard Law 5: Cổng Lớp 0 Beneish M-Score (Gian lận báo cáo tài chính)
        if not beneish_passed:
            return ComplianceResult(
                is_compliant=False,
                verdict=ComplianceVerdict.BLOCK,
                violated_rule=HardLaw.DIEU_5.name if hasattr(HardLaw, "DIEU_5") else "DIEU_5_BENEISH_GATE",
                risk_level=RiskSeverity.CATASTROPHIC,
                reason=f"Mã {ticker_clean} vi phạm Điều 5: Cổng Gian lận Kế toán Beneish M-Score (> -1.78). Cấm mua tuyệt đối.",
                details={"ticker": ticker_clean, "beneish_passed": False},
            )

        # 3. Hard Law 6: Cổng GIL Catastrophic (Sở hữu chéo & Gian lận Tập đoàn)
        if gil_ocr_score > 0.85:
            return ComplianceResult(
                is_compliant=False,
                verdict=ComplianceVerdict.BLOCK,
                violated_rule=HardLaw.DIEU_6.name if hasattr(HardLaw, "DIEU_6") else "DIEU_6_GIL_CATASTROPHIC",
                risk_level=RiskSeverity.CATASTROPHIC,
                reason=f"Mã {ticker_clean} vi phạm Điều 6: GIL CATASTROPHIC (OCR Score={gil_ocr_score:.2f} > 0.85). Zero tolerance.",
                details={"ticker": ticker_clean, "gil_ocr_score": gil_ocr_score},
            )

        # 4. Hard Law 3: Tối thiểu 3 tín hiệu độc lập xác nhận cho lệnh BUY
        if order.side == "BUY" and order_intent not in ("EMERGENCY_STOP_LOSS", "TAKE_PROFIT"):
            if confirming_signals_count < 3:
                return ComplianceResult(
                    is_compliant=False,
                    verdict=ComplianceVerdict.BLOCK,
                    violated_rule=HardLaw.DIEU_3.name if hasattr(HardLaw, "DIEU_3") else "DIEU_3_RULE_OF_THREE_SIGNALS",
                    risk_level=RiskSeverity.CRITICAL,
                    reason=f"Mã {ticker_clean} vi phạm Điều 3: Hiến pháp yêu cầu tối thiểu 3 tín hiệu độc lập xác nhận (Hiện có {confirming_signals_count}).",
                    details={"ticker": ticker_clean, "confirming_signals_count": confirming_signals_count},
                )

        # 5. Hard Laws 1, 2, 4 (Tồn tại T+2.5, Thanh khoản ADTV20, Tập trung vị thế 15% / 35%)
        hl_check: HardLawCheck = self.hard_law_engine.check_order(order, portfolio, adtv20_continuous)
        if not hl_check.passed:
            return ComplianceResult(
                is_compliant=False,
                verdict=ComplianceVerdict.BLOCK,
                violated_rule=hl_check.violated_law.name if hl_check.violated_law else "HARD_LAW_BREACH",
                risk_level=RiskSeverity.CRITICAL,
                reason=hl_check.reason,
                details={"ticker": ticker_clean, "violated_law": str(hl_check.violated_law)},
            )

        # 6. Kiểm tra Chính sách Vi cấu trúc HOSE
        hose_ok, hose_reason = self.validate_hose_microstructure(ticker_clean, order.quantity, order.price)
        if not hose_ok:
            return ComplianceResult(
                is_compliant=False,
                verdict=ComplianceVerdict.BLOCK,
                violated_rule="HOSE_MICROSTRUCTURE_POLICY",
                risk_level=RiskSeverity.MEDIUM,
                reason=hose_reason,
                details={"ticker": ticker_clean, "quantity": order.quantity, "price": order.price},
            )

        # 7. Kiểm tra Bán khống (Short Selling Prevention) với lệnh SELL
        if order.side == "SELL":
            if available_shares is not None and order.quantity > available_shares:
                return ComplianceResult(
                    is_compliant=False,
                    verdict=ComplianceVerdict.BLOCK,
                    violated_rule="HOSE_SHORT_SELLING_PROHIBITION",
                    risk_level=RiskSeverity.CRITICAL,
                    reason=f"Khối lượng bán đề xuất ({order.quantity:,.0f}) vượt số cổ phiếu khả dụng ({available_shares:,.0f}). Cấm bán khống cổ phiếu T+2.5 chưa về tài khoản.",
                    details={"ticker": ticker_clean, "requested_shares": order.quantity, "available_shares": available_shares},
                )

        return ComplianceResult(
            is_compliant=True,
            verdict=ComplianceVerdict.PASS,
            risk_level=RiskSeverity.NONE,
            reason="Thỏa mãn 100% Hiến pháp Đầu tư IOS v5.1 và Quy chế Vi cấu trúc HOSE.",
            details={"ticker": ticker_clean, "approved_quantity": order.quantity},
        )
