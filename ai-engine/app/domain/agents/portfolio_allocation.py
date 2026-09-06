"""AGENT-07: Portfolio Allocation Agent (Institutional Sovereign Capital Allocator — IOS v5.1).

Kiến trúc 8 Engine Điều Phối Chuẩn Định Chế:
1. Eligibility Engine: Thẩm định Research, Thesis, Counter-Thesis (Không xét Risk để tránh Split-Brain với Agent-06).
2. Probability Engine: Nạp và hiệu chuẩn p_calibrated & payoff_ratio R từ ma trận RL (báo warning minh bạch nếu thiếu).
3. Position Sizing Engine: Định cỡ Quarter Kelly f* = 0.25 * (p - (1-p)/R) và hệ số co giãn Regime.
4. Portfolio Construction Engine: Ràng buộc trần ngành <= 35% NAV, trần CIO, kiểm soát tương quan cặp và rủi ro biên.
5. Dynamic Allocation Engine: Đệm tiền mặt chủ động theo Regime (Bull 10%, Choppy 30%, Bear 60%), co giãn số lượng vị thế N.
6. Liquidity Engine: Tuân thủ Điều 2 Hiến pháp HOSE (Phiên <= 15% ADTV20, Vị thế <= 25% ADTV20, Execution Horizon).
7. Rebalancing Engine: Ngưỡng Deadband >= 2.0% chống bào mòn phí, phân mảnh T+2.5 (Available vs Locked) và bộ nhớ chiến dịch gom/xả đa phiên.
8. Decision Output Engine: Đóng gói 4 nhóm Output (A: Decision, B: Allocation, C: Impact, D: Log) kèm tương thích ngược.

Đảm bảo:
- Tuyệt đối KHÔNG sử dụng dữ liệu Mock ngầm (giá mặc định 100.000đ...). Thiếu giá/NAV log lỗi và từ chối an toàn.
- Khắc phục triệt để 5 lỗ hổng: Portfolio Blindness, Dead Code Optimizer, Hệ thống 1 chiều, Tiền mặt thụ động, Output nghèo nàn.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.rules.portfolio.eligibility_engine import EligibilityEngine
from app.domain.rules.portfolio.probability_engine import ProbabilityEngine
from app.domain.rules.portfolio.kelly_engine import KellySizingEngine
from app.domain.rules.portfolio.construction_engine import PortfolioConstructionEngine
from app.domain.rules.portfolio.dynamic_allocation_engine import DynamicAllocationEngine
from app.domain.rules.portfolio.liquidity_engine import LiquidityEngine
from app.domain.rules.portfolio.rebalancing_engine import RebalancingEngine
from app.domain.rules.portfolio.decision_output_engine import DecisionOutputEngine

logger = logging.getLogger(__name__)


class PortfolioAllocationAgent(BaseAgent):
    """
    AGENT-07: Chuyên viên Phân bổ Vốn & Quản trị Danh mục Định chế (Chief Capital Allocator).
    Chịu trách nhiệm tối ưu hóa tỷ trọng vốn, tái cân bằng danh mục và quản trị thanh khoản thực thi.
    """

    def __init__(self, repository: Optional[PortfolioRepository] = None):
        super().__init__(
            agent_name="portfolio_allocation",
            state_tables=["portfolio_account", "positions", "portfolio_decisions", "portfolio_campaigns"],
            log_table="log_portfolio_allocation",
            enabled=True,
        )
        self.repository = repository or PortfolioRepository()

        # Khởi tạo 8 Engine nghiệp vụ chuyên trách
        self.eligibility_engine = EligibilityEngine(min_conviction="B", max_cts_score=70.0)
        self.probability_engine = ProbabilityEngine()
        self.kelly_engine = KellySizingEngine(fraction=0.25, max_single_stock_pct=0.15)
        self.construction_engine = PortfolioConstructionEngine(sector_limit_pct=0.35, correlation_limit=0.50)
        self.dynamic_engine = DynamicAllocationEngine()
        self.liquidity_engine = LiquidityEngine(max_session_participation_pct=0.15, max_cumulative_capacity_pct=0.25)
        self.rebalance_engine = RebalancingEngine(deadband_threshold_pct=0.02)
        self.output_engine = DecisionOutputEngine()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Quy trình thẩm định và phân bổ vốn qua 8 Engine chuẩn định chế:
        - event_data:
            - candidate: {ticker, conviction, price, sector, adtv20, ...}
            - total_nav: float (tùy chọn, tự động nạp từ CSDL nếu không truyền)
            - cash_balance: float (tùy chọn)
            - kelly_matrix: Dict (ma trận tỷ lệ thắng nạp từ Agent-10 RL)
            - weight_cap: float (trần phân bổ vĩ mô áp từ Agent-12 CIO)
            - regime: str (BULL_MARKET, BEAR_MARKET, RANGE_BOUND...)
            - research_report: Dict (hồ sơ nghiên cứu)
            - investment_thesis: Dict (luận điểm đầu tư)
            - counter_thesis: Dict (phán quyết phản biện)
        """
        candidate = event_data.get("candidate", {})
        ticker = candidate.get("ticker") or event_data.get("ticker")
        if not ticker:
            raise ValueError("[PortfolioAllocationAgent] Thiếu mã cổ phiếu (ticker) trong candidate/event_data.")
        ticker = str(ticker).upper().strip()

        conviction_raw = candidate.get("conviction") or event_data.get("conviction")
        conviction = str(conviction_raw).upper().strip() if conviction_raw else ""
        sector = str(candidate.get("sector") or event_data.get("sector", "General")).strip()
        regime_str = str(event_data.get("regime") or candidate.get("regime", "BULL_MARKET")).upper().strip()
        weight_cap = float(event_data.get("weight_cap", candidate.get("weight_cap", 0.15)))

        # =========================================================================
        # 0. NẠP DỮ LIỆU THẬT & KIỂM TRA TOÀN VẸN (NO MOCK DATA ENFORCEMENT)
        # =========================================================================
        # A. Xác định Giá thị trường (Không mock ngầm 100.000đ)
        price = float(candidate.get("price") or candidate.get("target_price") or event_data.get("price", 0.0))
        if price <= 0:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                price_lookup = m_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=True)
                if price_lookup and price_lookup > 0:
                    price = float(price_lookup)
            except Exception as e:
                logger.warning(f"Không thể tra cứu giá DNSE realtime/EOD từ MarketDataRepository cho {ticker}: {e}")

        if price <= 0:
            error_msg = f"[PortfolioAllocationAgent] KHÔNG TÌM THẤY GIÁ THỊ TRƯỜNG HỢP LỆ cho mã {ticker}. Từ chối dùng mock data ngầm."
            logger.error(error_msg)
            raise ValueError(error_msg)

        # B. Nạp Số dư Tài khoản & NAV thực tế từ PortfolioRepository
        account_state = self.repository.get_account_state()
        nav = float(event_data.get("total_nav", account_state.get("total_nav", 0.0)))
        if nav <= 0:
            error_msg = f"[PortfolioAllocationAgent] Tổng NAV tài khoản không hợp lệ ({nav} VND). Từ chối phân bổ vốn."
            logger.error(error_msg)
            raise ValueError(error_msg)

        cash_balance = float(event_data.get("cash_balance", account_state.get("cash_balance", nav)))

        # C. Nạp toàn bộ Vị thế Hiện Hữu (Khắc phục Portfolio Blindness)
        existing_positions = (
            event_data["positions"] if "positions" in event_data and event_data["positions"] is not None
            else (
                event_data["existing_positions"] if "existing_positions" in event_data and event_data["existing_positions"] is not None
                else self.repository.get_open_positions()
            )
        )
        pos_current = next((p for p in existing_positions if p["ticker"] == ticker), None)
        current_shares = int(pos_current.get("shares", 0)) if pos_current else 0
        available_shares = int(pos_current.get("available_shares", current_shares)) if pos_current else 0
        locked_t25_shares = int(pos_current.get("locked_t25_shares", 0)) if pos_current else 0
        current_weight = round((current_shares * price) / nav, 4) if nav > 0 else 0.0

        # D. Nạp ADTV20 thực tế
        adtv20 = float(candidate.get("adtv20") or event_data.get("adtv20", 0.0))
        if adtv20 <= 0:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                latest_m = m_repo.get_market_data_daily(ticker, limit=1)
                if latest_m and "adtv20_continuous" in latest_m[0] and latest_m[0]["adtv20_continuous"]:
                    adtv20 = float(latest_m[0]["adtv20_continuous"])
            except Exception:
                pass

        if adtv20 <= 0:
            logger.warning(f"Thiếu thanh khoản ADTV20 cho mã {ticker}. Áp dụng mức ước lượng cơ sở tối thiểu 500,000 cổ.")
            adtv20 = 500000.0

        # =========================================================================
        # 1. ENGINE 1: ELIGIBILITY ENGINE (VỚI AUTO-HYDRATION TỪ CSDL)
        # =========================================================================
        research_data = event_data.get("research_report") or candidate.get("research_report")
        thesis_data = event_data.get("investment_thesis") or candidate.get("investment_thesis")
        counter_data = event_data.get("counter_thesis") or candidate.get("counter_thesis")

        # Tự động tra cứu Luận điểm Đầu tư & Phản biện từ CSDL nếu chưa được truyền
        try:
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()

            if not thesis_data:
                db_thesis = intel_repo.get_latest_investment_thesis(ticker)
                if db_thesis:
                    thesis_data = db_thesis
                    logger.info(f"[PortfolioAllocationAgent] Tự động nạp Investment Thesis từ CSDL cho {ticker} ({thesis_data.get('thesis_id')})")

            if not counter_data and thesis_data:
                t_id = thesis_data.get("thesis_id")
                db_counter = intel_repo.get_latest_counter_thesis_verdict(ticker)
                if not db_counter and t_id:
                    db_counter = intel_repo.get_counter_thesis_verdict(t_id)
                if db_counter:
                    counter_data = db_counter
                    logger.info(f"[PortfolioAllocationAgent] Tự động nạp Counter Thesis Verdict từ CSDL cho {ticker} (Verdict: {counter_data.get('verdict')})")

            # Tự động cập nhật Conviction và Sector nếu payload bị thiếu
            if not conviction or conviction == "UNKNOWN":
                if thesis_data and thesis_data.get("conviction"):
                    conviction = str(thesis_data.get("conviction")).upper().strip()
                elif research_data and research_data.get("conviction"):
                    conviction = str(research_data.get("conviction")).upper().strip()
                else:
                    conviction = "B"

            candidate["conviction"] = conviction

            if sector == "General" and thesis_data and thesis_data.get("sector"):
                sector = str(thesis_data.get("sector")).strip()
            candidate["sector"] = sector
        except Exception as e_hydra:
            logger.debug(f"[PortfolioAllocationAgent] Lỗi khi auto-hydrate Thesis/Counter từ DB: {e_hydra}")
            if not conviction:
                conviction = "B"
            candidate["conviction"] = conviction

        eligibility_res = self.eligibility_engine.evaluate(
            ticker=ticker,
            candidate_data=candidate,
            research_data=research_data,
            thesis_data=thesis_data,
            counter_thesis_data=counter_data,
        )

        # Nếu không đủ điều kiện cơ bản, từ chối giải ngân và trả về HOLD
        if not eligibility_res.eligible:
            logger.info(f"[PortfolioAllocationAgent] Mã {ticker} không đủ điều kiện: {eligibility_res.rejection_reasons}")
            # Format output từ chối
            mock_prob = self.probability_engine.evaluate(conviction, regime_str)
            mock_kelly = self.kelly_engine.calculate_sizing(0.0, 0.0, regime_str, nav)
            mock_construct = self.construction_engine.construct(ticker, 0.0, sector, nav, existing_positions, weight_cap)
            mock_dynamic = self.dynamic_engine.evaluate_allocation(0.0, ticker, regime_str, nav, cash_balance, existing_positions)
            mock_liq = self.liquidity_engine.evaluate_liquidity(ticker, 0.0, price, nav, current_shares, adtv20)
            rebalance_reject = self.rebalance_engine.evaluate_rebalance(
                ticker=ticker,
                current_weight=current_weight,
                current_shares=current_shares,
                available_shares=available_shares,
                locked_t25_shares=locked_t25_shares,
                portfolio_target=0.0,
                executable_target=current_weight,
                executable_shares=current_shares,
                target_shares=current_shares,
                incremental_shares=0,
                price=price,
                total_nav=nav,
            )
            rebalance_reject.action = "HOLD"
            rebalance_reject.sub_action = "INELIGIBLE_HOLD"
            rebalance_reject.rebalance_reasons = eligibility_res.rejection_reasons
            output_reject = self.output_engine.format_output(
                ticker=ticker,
                price=price,
                total_nav=nav,
                conviction=conviction,
                eligibility_res=eligibility_res,
                prob_res=mock_prob,
                kelly_res=mock_kelly,
                construction_res=mock_construct,
                dynamic_res=mock_dynamic,
                liquidity_res=mock_liq,
                rebalance_res=rebalance_reject,
            )
            self.repository.save_decision(output_reject["data"])
            return output_reject

        # =========================================================================
        # 2. ENGINE 2: PROBABILITY ENGINE
        # =========================================================================
        kelly_matrix = event_data.get("kelly_matrix") or candidate.get("kelly_matrix")
        prob_res = self.probability_engine.evaluate(
            conviction=conviction,
            regime_str=regime_str,
            kelly_matrix=kelly_matrix,
            storage_adapter=self.repository.storage,
        )

        # =========================================================================
        # 3. ENGINE 3: POSITION SIZING ENGINE (QUARTER KELLY)
        # =========================================================================
        kelly_res = self.kelly_engine.calculate_sizing(
            prob_win=prob_res.prob_win,
            payoff_ratio=prob_res.payoff_ratio,
            regime_str=regime_str,
            total_nav=nav,
        )

        # =========================================================================
        # 4. ENGINE 4: PORTFOLIO CONSTRUCTION ENGINE
        # =========================================================================
        corr_matrix = event_data.get("corr_matrix")
        construction_res = self.construction_engine.construct(
            ticker=ticker,
            preliminary_target=kelly_res.preliminary_target,
            sector=sector,
            total_nav=nav,
            existing_positions=existing_positions,
            macro_weight_cap=weight_cap,
            corr_matrix=corr_matrix,
        )

        # =========================================================================
        # 5. ENGINE 5: DYNAMIC ALLOCATION ENGINE (CASH TARGET & DRAWDOWN HYDRATION)
        # =========================================================================
        cio_cash_override = float(event_data.get("cash_target_override", 0.0))
        drawdown_tier = str(event_data.get("drawdown_tier") or account_state.get("drawdown_tier", "GREEN"))

        # Tự động nạp khuyến nghị tiền mặt tối thiểu và Drawdown Tier từ Agent-06 (bảng risk_snapshots)
        if cio_cash_override <= 0 or "drawdown_tier" not in event_data:
            try:
                from app.domain.repositories.intelligence_repository import IntelligenceRepository
                intel_repo = IntelligenceRepository()
                risk_snap = intel_repo.get_latest_risk_snapshot()
                if risk_snap:
                    if "drawdown_tier" not in event_data and risk_snap.get("drawdown_tier"):
                        drawdown_tier = str(risk_snap.get("drawdown_tier"))
                    if cio_cash_override <= 0 and risk_snap.get("garch_cash_target"):
                        cio_cash_override = float(risk_snap.get("garch_cash_target"))
            except Exception as e_snap:
                logger.debug(f"[PortfolioAllocationAgent] Không thể nạp risk_snapshots từ DB: {e_snap}")

        dynamic_res = self.dynamic_engine.evaluate_allocation(
            portfolio_target=construction_res.portfolio_target,
            ticker=ticker,
            regime_str=regime_str,
            total_nav=nav,
            cash_balance=cash_balance,
            existing_positions=existing_positions,
            cio_cash_target_override=cio_cash_override,
            drawdown_tier=drawdown_tier,
        )

        # =========================================================================
        # 6. ENGINE 6: LIQUIDITY ENGINE (HOSE COMPLIANCE & HORIZON)
        # =========================================================================
        liquidity_res = self.liquidity_engine.evaluate_liquidity(
            ticker=ticker,
            portfolio_target=dynamic_res.adjusted_target,
            price=price,
            total_nav=nav,
            current_shares=current_shares,
            adtv20=adtv20,
        )

        # =========================================================================
        # 7. ENGINE 7: REBALANCING ENGINE (DEADBAND >= 2% & T+2.5 SPLIT)
        # =========================================================================
        active_campaign = self.repository.get_active_campaign(ticker)
        rebalance_res = self.rebalance_engine.evaluate_rebalance(
            ticker=ticker,
            current_weight=current_weight,
            current_shares=current_shares,
            available_shares=available_shares,
            locked_t25_shares=locked_t25_shares,
            portfolio_target=dynamic_res.adjusted_target,
            executable_target=liquidity_res.executable_target,
            executable_shares=liquidity_res.executable_shares,
            target_shares=liquidity_res.target_shares,
            incremental_shares=liquidity_res.incremental_shares,
            price=price,
            total_nav=nav,
            active_campaign=active_campaign,
        )

        # Cập nhật chiến dịch đa phiên vào CSDL
        if rebalance_res.campaign_info:
            self.repository.upsert_campaign(rebalance_res.campaign_info)

        # =========================================================================
        # 8. ENGINE 8: DECISION OUTPUT ENGINE (4 GROUPS)
        # =========================================================================
        output = self.output_engine.format_output(
            ticker=ticker,
            price=price,
            total_nav=nav,
            conviction=conviction,
            eligibility_res=eligibility_res,
            prob_res=prob_res,
            kelly_res=kelly_res,
            construction_res=construction_res,
            dynamic_res=dynamic_res,
            liquidity_res=liquidity_res,
            rebalance_res=rebalance_res,
        )

        # Tự động lưu quyết định phân bổ vốn vào CSDL PostgreSQL (bảng portfolio_decisions)
        self.repository.save_decision(output["data"])

        # Bắn sự kiện lên RabbitMQ Topic Exchange (EventTopics.ORDER_INSTRUCTION & REBALANCE_PLANNED)
        try:
            from app.core.event_topics import EventTopics
            out_data = output.get("data", {})
            inc_shares = out_data.get("capital_allocation", {}).get("incremental_shares", 0)
            act = str(out_data.get("action", "HOLD")).upper()

            if act in ("BUY", "SELL", "REBALANCE") and abs(inc_shares) > 0:
                await self.publish_event(
                    topic=EventTopics.ORDER_INSTRUCTION,
                    payload={
                        "decision_id": out_data.get("decision_id"),
                        "ticker": ticker,
                        "action": act,
                        "side": out_data.get("side", "HOLD"),
                        "shares": abs(inc_shares),
                        "target_shares": out_data.get("target_shares", 0),
                        "price": price,
                        "allocated_amount_vnd": out_data.get("allocated_amount_vnd", 0.0),
                        "allocated_weight_pct": out_data.get("allocated_weight_pct", 0.0),
                        "execution_urgency": out_data.get("portfolio_decision", {}).get("execution_urgency", "NORMAL"),
                        "rationale": out_data.get("rationale", ""),
                        "campaign": out_data.get("campaign"),
                        "timestamp": out_data.get("timestamp"),
                    },
                )
            elif act == "REBALANCE":
                await self.publish_event(
                    topic=EventTopics.REBALANCE_PLANNED,
                    payload={
                        "decision_id": out_data.get("decision_id"),
                        "ticker": ticker,
                        "portfolio_target": out_data.get("capital_allocation", {}).get("portfolio_target", 0.0),
                        "executable_target": out_data.get("capital_allocation", {}).get("executable_target", 0.0),
                        "incremental_shares": inc_shares,
                        "rebalance_reasons": out_data.get("decision_log", {}).get("reason", []),
                        "timestamp": out_data.get("timestamp"),
                    },
                )
        except Exception as e_pub:
            logger.warning(f"[PortfolioAllocationAgent] Không thể phát sự kiện phân bổ vốn lên RabbitMQ ({e_pub})")

        # Đảm bảo ghi audit log vào log_portfolio_allocation ngay cả khi gọi trực tiếp qua process()
        if not event_data.get("_from_run_event"):
            try:
                await self._log_audit_trace(
                    event_data=event_data,
                    computation_trace=output.get("trace", {}),
                    output_data=output.get("data", output),
                    status="SUCCESS",
                )
            except Exception as e_log:
                logger.debug(f"[PortfolioAllocationAgent] Không thể ghi log_portfolio_allocation ({e_log})")

        return output
