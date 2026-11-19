"""Daily Investment Pipeline Orchestrator — IOS v5.1 Sovereign Multi-Agent Architecture.

Điều phối toàn bộ chu trình đầu tư khép kín qua 12 Agents (Agent-01 đến Agent-12):
  Pha 1: Agent-01 (Market Surveillance) — Macro Regime HMM, Session Context & Halted Check
  Pha 2: Agent-10 (Reinforcement Learning) — Cung cấp Adaptive Policy Weights F1-F6 & Bayesian Kelly
  Pha 3: Agent-02 (Universe Discovery) — Lớp 0 Forensic Accounting Gate (Beneish M-Score <= -1.78) & ADTV20
  Pha 4: Agent-03 (Equity Research) — Đánh giá đa nhân tố F1-F6 + Business Quality -> Conviction Level (A+, A, B)
  Pha 5: Agent-04 (Investment Thesis) — Tổng hợp Luận đề Đầu tư & Kiểm định 3 Tín hiệu Độc lập (Điều 3)
  Pha 6: Agent-05 (Counter Thesis) — Phản biện Đa chiều (Devil's Advocate), CTS Score & Bẫy Thanh khoản
  Pha 7: Agent-12 (Strategy CIO) — Trọng tài Thể chế Tối cao phân định mâu thuẫn & áp trần weight_cap
  Pha 8: Agent-06 (Portfolio Allocation) — Định cỡ Sizing Kelly & Tuân thủ Điều 4 (Max 15% NAV/mã)
  Pha 9: Agent-07 (Portfolio Risk) — Cổng Thẩm định Rủi ro Tối cao: Điều 1 (Hard Stop 2% NAV), T+2.5 & Tail Risk
  Pha 10: Agent-08 (Trade Execution) — Lập kế hoạch thực thi EAE VWAP Slicing (LIVE Broker hoặc SHADOW Paper)
  Pha 11: Agent-09 (Position Monitoring) — Khởi tạo 4 Tầng Bảo vệ (Hard Stop, Breakeven Lock, Trailing Stop)
  Pha 12: Agent-11 (System Governance) — Thẩm định Tuân thủ Hiến pháp & Băm Sổ cái Bất biến SHA-256
"""

from __future__ import annotations

import logging
import os
from collections import Counter
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp và tự động đăng ký đủ 12 Agents vào Registry
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.rules.optimizer import PortfolioOptimizer
from app.domain.rules.portfolio.allocation_policy import (
    InvestmentPlaybook,
    PortfolioPolicy,
    resolve_portfolio_policy,
)
from app.domain.services.factor_service import FactorService
from app.infrastructure.database.pg_pool import get_conn

load_dotenv()
logger = logging.getLogger("ai_engine.pipeline.daily")


class ExecutionMode(str, Enum):
    LIVE = "LIVE"                  # Bắn lệnh thực tế ra sàn qua Broker API
    SHADOW_RUNNER = "SHADOW_RUNNER"# Chạy ngầm mô phỏng Paper Trading, ghi log CSDL
    DISABLED = "DISABLED"          # Tắt hoàn toàn không tính toán


class DailyInvestmentPipeline:
    """
    Bộ Điều Phối Chu Trình Đầu Tư Tự Hành Đầy Đủ 12 Agents (IOS v5.1 Institutional Sovereign Architecture).
    """

    def __init__(
        self,
        multi_agent_mode: str = os.getenv("MULTI_AGENT_MODE", ExecutionMode.SHADOW_RUNNER.value),
    ):
        try:
            self.multi_agent_mode = ExecutionMode(multi_agent_mode)
        except ValueError:
            self.multi_agent_mode = ExecutionMode.SHADOW_RUNNER

        if self.multi_agent_mode == ExecutionMode.LIVE:
            logger.critical("LIVE multi-agent execution is disabled: no broker order gateway is implemented")
            self.multi_agent_mode = ExecutionMode.DISABLED

        self.portfolio_repo = PortfolioRepository()
        logger.info(
            f"[DailyInvestmentPipeline] Khởi tạo thành công: "
            f"Multi-Agent Mode = '{self.multi_agent_mode.value}'"
        )

    def _load_current_portfolio_state(self, user_id: str, current_nav: Optional[float]) -> Dict[str, Any]:
        account = self.portfolio_repo.get_account_state(user_id=user_id)
        positions = self.portfolio_repo.get_open_positions(user_id=user_id)
        nav = float(current_nav if current_nav is not None else account.get("total_nav", 1_000_000_000.0))
        sector_exposure: Dict[str, float] = {}
        locked_t25_value = 0.0
        for position in positions:
            shares = int(position.get("shares", position.get("quantity", 0)))
            current_price = float(position.get("current_price", position.get("average_price", 0.0)))
            sector = str(position.get("sector") or "").strip()
            market_value = float(position.get("market_value", shares * current_price))
            if sector:
                sector_exposure[sector] = sector_exposure.get(sector, 0.0) + market_value
            locked_t25_value += int(position.get("locked_t25_shares", 0)) * current_price
        return {
            "total_nav": nav,
            "peak_nav": float(account.get("peak_nav", nav)),
            "cash_vnd": float(account.get("cash_balance", nav)),
            "positions": positions,
            "sector_exposure": sector_exposure,
            "locked_t25_value": locked_t25_value,
            "returns_series": [],
        }

    async def _monitor_open_positions(
        self, portfolio_state: Dict[str, Any], is_bear_defense: bool = False
    ) -> List[Dict[str, Any]]:
        """Keep exits/risk monitoring alive when new analysis is unavailable."""
        positions = []
        for position in portfolio_state.get("positions", []):
            ticker = position.get("ticker", position.get("symbol"))
            quantity = int(position.get("shares", position.get("quantity", 0)))
            if not ticker or quantity <= 0:
                continue
            positions.append({
                **position,
                "ticker": ticker,
                "entry_price": float(position.get("average_price", position.get("avg_price", 0.0))),
                "quantity": quantity,
                "available_shares": int(position.get("available_shares", quantity)),
                "locked_t25_shares": int(position.get("locked_t25_shares", 0)),
                "current_price": None,
            })
        if not positions:
            return []

        try:
            result = await AgentRegistry.dispatch("position_monitoring", {
                "positions": positions,
                "nav": float(portfolio_state.get("total_nav", 0.0)),
                "is_bear_defense": is_bear_defense,
            })
            if result.get("status") == "SUCCESS":
                data = result.get("result", {}).get("data", {})
                return data.get("stop_loss_orders", [])
        except Exception as error:
            logger.error("[DailyPipeline] Existing position monitoring failed: %s", error)
        return []

    async def run(
        self,
        target_date: Optional[Union[date, str]] = None,
        current_nav: Optional[float] = None,
        candidate_tickers: Optional[List[str]] = None,
        max_candidates: int = 3,
        replay_portfolio: Optional[Dict[str, Any]] = None,
        replay_account_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Thực thi toàn diện chuỗi 12 Pha của hệ thống tự hành cho ngày giao dịch.
        NAV được truy vấn động học trực tiếp từ CSDL PostgreSQL (users & positions).
        """
        tz_vn = ZoneInfo("Asia/Ho_Chi_Minh")
        now_vn = datetime.now(tz_vn)
        run_date_str = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else (str(target_date) if target_date else now_vn.date().isoformat())
        )
        target_date_obj = date.fromisoformat(run_date_str) if isinstance(run_date_str, str) else run_date_str
        is_replay = replay_portfolio is not None or replay_account_id is not None or target_date_obj < now_vn.date()
        replay_at = datetime.combine(
            target_date_obj, time(9, 45), tzinfo=tz_vn
        ) if is_replay else None
        market_data_date = target_date_obj - timedelta(days=1) if replay_at else target_date_obj
        multi_agent_account_id = replay_account_id or os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794")

        replay_context = {
            "target_date": run_date_str,
            "market_data_date": market_data_date.isoformat(),
            "replay_account_id": multi_agent_account_id,
            "is_replay": replay_at is not None,
            **({"current_time": replay_at.isoformat()} if replay_at else {}),
        }
        pipeline_trace: Dict[str, Any] = {"run_date": run_date_str, "phases": {}}

        # Nếu là historical run: nạp NAV và positions tính đến as-of
        if replay_at:
            if replay_portfolio:
                current_nav = float(replay_portfolio.get("total_nav", current_nav or 1_000_000_000.0))
                replay_positions = replay_portfolio.get("positions", [])
                replay_cash = float(replay_portfolio.get("cash_vnd", current_nav))
            else:
                account = self.portfolio_repo.record_replay_mark(user_id=multi_agent_account_id, mark_as_of=market_data_date)
                replay_positions = self.portfolio_repo.get_open_positions(
                    user_id=multi_agent_account_id, as_of=market_data_date, as_of_time=replay_at
                )
                current_nav = float(account.get("total_nav", 1_000_000_000.0))
                replay_cash = float(account.get("cash_balance", current_nav))
                replay_portfolio = {
                    "total_nav": current_nav,
                    "peak_nav": float(account.get("peak_nav", current_nav)),
                    "cash_vnd": replay_cash,
                    "positions": replay_positions,
                    "sector_exposure": {},
                    "locked_t25_value": sum(
                        float(p.get("current_price", 0)) * int(p.get("locked_t25_shares", 0))
                        for p in replay_positions
                    ),
                    "returns_series": [],
                }
            portfolio_state = replay_portfolio
        else:
            portfolio_state = self._load_current_portfolio_state(multi_agent_account_id, current_nav)
            current_nav = float(portfolio_state["total_nav"])
            replay_positions = portfolio_state["positions"]
            replay_cash = float(portfolio_state["cash_vnd"])

        logger.info(f"================================================================================")
        logger.info(f"[Daily Investment Pipeline] KHỞI ĐỘNG CHU TRÌNH 12 AGENT CHO NGÀY: {run_date_str}")
        logger.info(f"[NAV TỪ CSDL] Multi-Agent NAV: {current_nav:,.0f} VND (Acc: {multi_agent_account_id})")
        logger.info(f"[EXECUTION MODE] Multi-Agent: {self.multi_agent_mode.value}")
        logger.info(f"================================================================================")

        actions_to_audit: List[Dict[str, Any]] = []

        # =========================================================================
        # PHA 1: AGENT-01 (MARKET SURVEILLANCE & MACRO REGIME)
        # =========================================================================
        logger.info("[Pha 1] Kích hoạt Agent-01: Giám sát Vĩ mô & Định vị Market Regime...")
        res_surv = await AgentRegistry.dispatch(
            "market_surveillance",
            {
                **replay_context,
                **({"is_realtime": True} if replay_at is None else {}),
            },
        )
        surv_data = res_surv.get("result", {}).get("data", {}) if res_surv.get("status") == "SUCCESS" else {}
        if res_surv.get("status") != "SUCCESS" or not surv_data.get("current_regime"):
            pipeline_trace["phases"]["phase_1_market_surveillance"] = {"status": "FAILED_OR_INCOMPLETE"}
            return {
                "date": run_date_str,
                "status": "MARKET_SURVEILLANCE_UNAVAILABLE",
                "multi_agent_instructions": [],
                "trace": pipeline_trace,
            }
        
        regime = surv_data.get("current_regime", "BULL_MARKET")
        session_context = surv_data.get("session_context", "Normal")
        halted_tickers = surv_data.get("halted_tickers", [])
        distribution_days = surv_data.get("distribution_days")
        breadth_ma20_pct = surv_data.get("breadth_above_ma20_pct")
        breadth_ma50_pct = surv_data.get("breadth_above_ma50_pct")
        csad_score = surv_data.get("csad_score")
        adv_decl_ratio = surv_data.get("adv_decl_ratio")

        # Xác định Policy động theo Regime + Context + Bậc quy mô NAV (AUM-Tiered Playbook)
        policy: PortfolioPolicy = resolve_portfolio_policy(
            regime_str=regime,
            total_nav=current_nav,
            session_context=session_context,
        )
        playbook = policy.playbook
        is_crisis_lockdown = playbook == InvestmentPlaybook.CRISIS_LOCKDOWN
        cash_ratio = policy.min_cash_target

        logger.info(
            f"[Pha 1] Hoàn tất: Regime = '{regime}' | Context = '{session_context}' | "
            f"Playbook = '{playbook.value}' | AUM = '{policy.aum_tier.value}' | "
            f"Max Equity = {policy.max_equity_exposure:.0%} | Target Cash = {cash_ratio:.0%} | "
            f"Max Positions = {policy.max_positions}"
        )

        pipeline_trace["phases"]["phase_1_market_surveillance"] = {
            "status": "COMPLETED",
            "regime": regime,
            "session_context": session_context,
            "playbook": playbook.value,
            "aum_tier": policy.aum_tier.value,
            "policy": policy.to_dict(),
            "halted_count": len(halted_tickers),
            "distribution_days": distribution_days,
            "breadth_ma20_pct": breadth_ma20_pct,
            "breadth_ma50_pct": breadth_ma50_pct,
            "target_cash_ratio": cash_ratio,
        }

        # ── HARD LAW GATING: Chỉ khóa 100% tiền mặt ngay ở Pha 1 khi rơi vào CRISIS / STRESS_SEVERE ──
        if is_crisis_lockdown:
            logger.warning(
                f"[Daily Investment Pipeline] KÍCH HOẠT CRISIS_LOCKDOWN (BEAR_DEFENSE): Thị trường rơi vào {regime} ({session_context}). "
                f"Khóa 100% tiền mặt, đóng băng giải ngân mới để bảo toàn vốn."
            )
            emergency_instructions = []
            if not replay_at:
                try:
                    emergency_instructions = await self._monitor_open_positions(
                        portfolio_state, is_bear_defense=True
                    )
                except Exception as e_pos:
                    logger.error(f"[DailyPipeline] Lỗi quét bảo vệ vị thế khi Crisis: {e_pos}")

            if not replay_at:
                await AgentRegistry.dispatch("system_governance", {
                    "execution_mode": self.multi_agent_mode.value,
                    "actions_to_audit": [
                        {
                            "agent_id": "market_surveillance",
                            "event_type": "BEAR_DEFENSE_TRIGGERED",
                            "details": {"regime": regime, "session_context": session_context, "cash_ratio": 1.0, "reason": "PRESERVE_100PCT_CAPITAL", "emergency_actions": len(emergency_instructions)},
                        }
                    ],
                })

            return {
                "date": run_date_str,
                "regime": regime,
                "session_context": session_context,
                "playbook": playbook.value,
                "cash_ratio": 1.0,
                "status": "BEAR_DEFENSE_100PCT_CASH",
                "multi_agent_mode": self.multi_agent_mode.value,
                "multi_agent_instructions": emergency_instructions,
                "trace": pipeline_trace,
            }

        # =========================================================================
        # PHA 2: AGENT-10 (REINFORCEMENT LEARNING & ADAPTIVE PARAMETERS)
        # =========================================================================
        logger.info("[Pha 2] Kích hoạt Agent-10: Nạp bộ trọng số thích ứng động & Ma trận Kelly...")
        res_rl = await AgentRegistry.dispatch("reinforcement_learning", {
            "regime": regime,
            **replay_context,
        })
        rl_data = res_rl.get("result", {}).get("data", {}) if res_rl.get("status") == "SUCCESS" else {}
        policy_weights = rl_data.get("policy_weights", {})
        kelly_matrix = rl_data.get("kelly_matrix", {})
        cdc_triggered = rl_data.get("cdc_triggered", False)

        logger.info(
            f"[Pha 2] Hoàn tất: Nạp {len(policy_weights)} Factor Weights (F1-F6) | "
            f"Kelly Matrix: {list(kelly_matrix.keys())} | CDC: {cdc_triggered}"
        )
        pipeline_trace["phases"]["phase_2_reinforcement_learning"] = {
            "status": "COMPLETED",
            "policy_weights": policy_weights,
            "kelly_tiers": list(kelly_matrix.keys()),
            "cdc_triggered": cdc_triggered,
        }

        # =========================================================================
        # PHA 3: AGENT-02 (UNIVERSE DISCOVERY & LỚP 0 BENEISH M-SCORE)
        # =========================================================================
        logger.info("[Pha 3] Kích hoạt Agent-02: Quét Universe HOSE, Thẩm định Lớp 0 Beneish M-Score...")
        res_disc = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": candidate_tickers,
            "target_date": target_date_obj,
            "market_data_date": market_data_date,
            "session_context": session_context,
            "current_regime": regime,
            "halted_tickers": halted_tickers,
            **replay_context,
        })
        disc_data = res_disc.get("result", {}).get("data", {}) if res_disc.get("status") == "SUCCESS" else {}
        discovery_list = disc_data.get("discovery_list", [])
        eligible_count = disc_data.get("eligible_count", 0)

        logger.info(f"[Pha 3] Hoàn tất: Phát hiện {eligible_count} mã đạt chuẩn Universe & vượt qua Lớp 0.")
        pipeline_trace["phases"]["phase_3_universe_discovery"] = {
            "status": disc_data.get("status", "COMPLETED"),
            "scanned_count": disc_data.get("scanned_count", 0),
            "eligible_count": eligible_count,
            "excluded_count": disc_data.get("excluded_count", 0),
        }

        if eligible_count == 0 or not discovery_list:
            hold_instructions = [] if replay_at else await self._monitor_open_positions(portfolio_state)
            logger.warning("[Daily Investment Pipeline] Không có mã nào vượt qua bộ lọc Universe / Lớp 0 Beneish.")
            return {
                "date": run_date_str,
                "regime": regime,
                "session_context": session_context,
                "cash_ratio": cash_ratio,
                "status": "NO_ELIGIBLE_UNIVERSE",
                "multi_agent_instructions": hold_instructions,
                "trace": pipeline_trace,
            }

        # Lấy danh sách mã ứng viên nền (giữ thứ tự ổn định cho Vòng 1 Research; xếp hạng thực sự diễn ra sau Vòng 1)
        sorted_candidates = sorted(
            discovery_list,
            key=lambda x: (x.get("universe_group") == "A", x.get("adtv20", 0.0)),
            reverse=True,
        )
        candidate_metadata = {str(c["ticker"]).upper(): c for c in sorted_candidates}
        selected_tickers = [c["ticker"] for c in sorted_candidates]

        # =========================================================================
        # ĐẤU TRƯỜNG 2 VÒNG (TWO-STAGE TOURNAMENT) & XẾP HẠNG CHÉO THEO PLAYBOOK
        # =========================================================================
        qualified_orders_multi_agent: List[Dict[str, Any]] = []
        monitored_positions_created: List[Dict[str, Any]] = []
        research_gate_decisions: List[Dict[str, Any]] = []
        thesis_gate_decisions: List[Dict[str, Any]] = []
        allocation_risk_decisions: List[Dict[str, Any]] = []
        portfolio_refresh_failed = False

        # Làm bản sao an toàn của danh mục hiện hữu để theo dõi đặt chỗ vốn/slot trong phiên
        working_positions: List[Dict[str, Any]] = [dict(p) for p in (replay_positions or [])]
        working_cash: float = float(replay_cash)
        working_portfolio_state: Dict[str, Any] = {
            **portfolio_state,
            "positions": working_positions,
            "sector_exposure": dict(portfolio_state.get("sector_exposure") or {}),
            "cash_vnd": working_cash,
        }
        # Đồng bộ sector_exposure nếu replay_portfolio truyền dict rỗng nhưng có positions
        if not working_portfolio_state["sector_exposure"] and working_positions:
            for pos in working_positions:
                sec = str(pos.get("sector") or "").strip()
                if sec:
                    mv = float(pos.get("market_value", 0.0)) or (
                        int(pos.get("shares", pos.get("quantity", 0)) or 0)
                        * float(pos.get("current_price", pos.get("average_price", 0.0)) or 0.0)
                    )
                    working_portfolio_state["sector_exposure"][sec] = (
                        working_portfolio_state["sector_exposure"].get(sec, 0.0) + mv
                    )

        peak_nav = float(working_portfolio_state.get("peak_nav", current_nav) or current_nav)
        dd_pct = max(0.0, (peak_nav - current_nav) / peak_nav * 100.0) if peak_nav > 0 else 0.0
        current_drawdown_tier = (
            "RED" if dd_pct >= 15.0 else ("ORANGE" if dd_pct >= 10.0 else ("YELLOW" if dd_pct >= 5.0 else ("ALERT" if dd_pct >= 3.0 else "GREEN")))
        )

        # ── VÒNG 1: BATCH LEADERSHIP METRICS & QUÉT AGENT-03 TOÀN BỘ UNIVERSE (KHÔNG BREAK SỚM) ──
        leadership_by_ticker: Dict[str, Dict[str, Any]] = {}
        corr_matrix = None
        try:
            factor_svc = FactorService()
            held_for_corr = [
                str(p.get("ticker") or p.get("symbol") or "").upper()
                for p in working_positions
                if int(p.get("shares", p.get("quantity", 0)) or 0) > 0
            ]
            leadership_by_ticker, corr_matrix = factor_svc.compute_universe_leadership_metrics(
                tickers=list(set(selected_tickers) | set(held_for_corr)),
                as_of_date=market_data_date,
            )
        except Exception as e_lead:
            logger.warning(f"[Vòng 1] Không thể tính batch leadership metrics: {e_lead}")

        stage1_passed: List[Dict[str, Any]] = []
        stage1_reports: List[Dict[str, Any]] = []

        for ticker in selected_tickers:
            meta = candidate_metadata.get(str(ticker).upper(), {})
            lead_metrics = leadership_by_ticker.get(str(ticker).upper())
            res_res = await AgentRegistry.dispatch("equity_research", {
                "ticker": ticker,
                "current_regime": regime,
                "policy_weights": policy_weights,
                "playbook": playbook.value,
                "universe_group": meta.get("universe_group", "B"),
                **({"leadership_metrics": lead_metrics} if lead_metrics else {}),
                **replay_context,
            })
            if res_res.get("status") != "SUCCESS":
                research_gate_decisions.append({
                    "ticker": ticker,
                    "status": "RESEARCH_FAILED",
                    "reason_codes": ["EQUITY_RESEARCH_FAILED"],
                })
                continue

            research_report = res_res["result"]["data"]
            conviction = research_report.get("conviction", "C")
            current_price = float(research_report.get("current_price", 0.0))
            sector = research_report.get("sector", "General")
            portfolio_sector = meta.get("portfolio_sector") or sector
            playbook_score = float(research_report.get("playbook_score", research_report.get("css", 0.0)))

            reason_codes = research_report.get("eligibility_reasons")
            if not isinstance(reason_codes, list):
                reason_codes = ["RESEARCH_GATE_DECISION_MISSING"]
            if not research_report.get("eligible_for_thesis") and not reason_codes:
                reason_codes = ["RESEARCH_GATE_FAILED_WITHOUT_REASON"]

            # Lọc thanh khoản theo bậc NAV (trừ khi user truyền candidate_tickers cụ thể)
            adtv20_vnd = float(meta.get("adtv20_vnd", meta.get("adtv20", 0.0)) or 0.0)
            if candidate_tickers is None and adtv20_vnd > 0 and adtv20_vnd < policy.min_adtv20_vnd:
                reason_codes = list(reason_codes) + [f"ADTV20_BELOW_AUM_TIER_FLOOR({policy.aum_tier.value})"]

            passed_stage1 = bool(research_report.get("eligible_for_thesis")) and not reason_codes
            research_gate_decisions.append({
                "ticker": ticker,
                "status": "PASS" if passed_stage1 else "EXCLUDED",
                "reason_codes": reason_codes,
                "conviction": conviction,
                "css": research_report.get("css"),
                "playbook_score": playbook_score,
                "rs_20d": research_report.get("rs_20d"),
                "vsa_swing_score": research_report.get("vsa_swing_score"),
                "current_price": current_price,
            })
            stage1_reports.append(research_report)
            if passed_stage1:
                stage1_passed.append({
                    "ticker": ticker,
                    "research_report": research_report,
                    "conviction": conviction,
                    "current_price": current_price,
                    "sector": sector,
                    "portfolio_sector": portfolio_sector,
                    "playbook_score": playbook_score,
                    "css": float(research_report.get("css", 0.0)),
                    "rs_score": float(research_report.get("rs_score", 50.0)),
                    "sector_leadership_score": float(research_report.get("sector_leadership_score", 50.0)),
                    "leadership_metrics": lead_metrics or research_report.get("leadership_metrics") or {},
                })

        # Xếp hạng Vòng 1 giảm dần theo Playbook Score -> CSS -> RS Score (không theo thứ tự ADTV20!)
        stage1_passed.sort(
            key=lambda x: (x["playbook_score"], x["css"], x["rs_score"]),
            reverse=True,
        )

        # Kiểm tra sức chứa slot & ngân sách danh mục trước khi gọi LLM Vòng 2
        held_tickers_now = {
            str(p.get("ticker") or p.get("symbol") or "").upper()
            for p in working_positions
            if int(p.get("shares", p.get("quantity", 0)) or 0) > 0
        }
        current_equity_val = sum(
            float(p.get("market_value", 0.0)) or (
                int(p.get("shares", p.get("quantity", 0)) or 0)
                * float(p.get("current_price", p.get("average_price", 0.0)) or 0.0)
            )
            for p in working_positions
            if int(p.get("shares", p.get("quantity", 0)) or 0) > 0
        )
        current_equity_pct = (current_equity_val / current_nav) if current_nav > 0 else 0.0
        remaining_equity_budget = max(0.0, policy.max_equity_exposure - current_equity_pct)
        available_slots_start = max(0, policy.max_positions - len(held_tickers_now))
        budget_allows_new_entry = (
            available_slots_start > 0
            and remaining_equity_budget >= policy.min_entry_weight - 1e-4
            and current_drawdown_tier != "RED"
        )

        # Lọc các mã chưa nắm giữ và cắt Shortlist Top-M (mã mạnh nhất sàn) cho Vòng 2
        shortlist_limit = min(policy.shortlist_size, max(6, available_slots_start * 4)) if available_slots_start > 0 else 0
        shortlist = [
            item for item in stage1_passed
            if str(item["ticker"]).upper() not in held_tickers_now
        ][:shortlist_limit]

        logger.info(
            f"[Vòng 1 Tournament] Quét {len(selected_tickers)} mã | Đạt Research={len(stage1_passed)} | "
            f"Shortlist Top-{len(shortlist)}: {[f'{x['ticker']}({x['playbook_score']:.1f})' for x in shortlist[:6]]} | "
            f"Slots trống={available_slots_start}/{policy.max_positions} | Ngân sách còn={remaining_equity_budget:.1%}"
        )

        # ── VÒNG 2: THẨM ĐỊNH SÂU PHA 5 → PHA 7 (THESIS → COUNTER-THESIS → CIO) TRÊN SHORTLIST ──
        finalists: List[Dict[str, Any]] = []
        if budget_allows_new_entry and shortlist:
            for item in shortlist:
                ticker = item["ticker"]
                research_report = item["research_report"]
                conviction = item["conviction"]
                current_price = item["current_price"]
                portfolio_sector = item["portfolio_sector"]
                lead_metrics = item["leadership_metrics"]

                logger.info(
                    f"\n--- [VÒNG 2 DEEP REVIEW] {ticker} | Playbook={playbook.value} ({item['playbook_score']:.1f}) | "
                    f"CSS={item['css']:.1f} ({conviction}) | RS20d={research_report.get('rs_20d', 0)*100:+.1f}% ---"
                )

                # ── PHA 5: AGENT-04 (INVESTMENT THESIS) ──
                res_thesis = await AgentRegistry.dispatch("investment_thesis", {
                    "research_report": research_report,
                    "playbook": playbook.value,
                    "timeline_months": policy.timeline_months,
                    "market_context": {
                        "current_regime": regime,
                        "session_context": session_context,
                        "playbook": playbook.value,
                        "leadership_metrics": lead_metrics,
                    },
                    **replay_context,
                })
                thesis_result_data = res_thesis.get("result", {}).get("data", {}) if res_thesis.get("status") == "SUCCESS" else {}
                thesis_status = thesis_result_data.get("status", "AGENT_FAILED")
                thesis_reason_codes = thesis_result_data.get("reason_codes") or (
                    ["INVESTMENT_THESIS_FAILED"] if thesis_status == "AGENT_FAILED" else []
                )
                thesis_gate_decisions.append({
                    "ticker": ticker,
                    "status": thesis_status,
                    "reason_codes": thesis_reason_codes,
                    "reason": thesis_result_data.get("reason"),
                    "playbook_score": item["playbook_score"],
                })
                if res_thesis.get("status") != "SUCCESS" or thesis_status in ["REJECTED", "WAIT_OR_SKIP", "DEFERRED"]:
                    logger.info(
                        f"[Pha 5 - {ticker}] Thesis={thesis_status} | reason_codes={thesis_reason_codes} | "
                        f"reason={thesis_result_data.get('reason', 'agent dispatch failed')}"
                    )
                    continue

                thesis_data = thesis_result_data
                logger.info(f"[Pha 5 - {ticker}] Luận đề hợp lệ (Thesis ID: {thesis_data.get('thesis_id')}).")

                # ── PHA 6: AGENT-05 (COUNTER THESIS - BƠM ĐẦY ĐỦ DỮ LIỆU TỪ AGENT-01 CHO CAPITULATION) ──
                res_counter = await AgentRegistry.dispatch("counter_thesis", {
                    "investment_thesis": thesis_data,
                    "market_data": {
                        "current_regime": regime,
                        "session_context": session_context,
                        "csad_score": float(csad_score or 0.0),
                        "breadth_above_ma20_pct": float(breadth_ma20_pct) if breadth_ma20_pct is not None else 50.0,
                        "breadth_above_ma50_pct": float(breadth_ma50_pct) if breadth_ma50_pct is not None else 50.0,
                        "adv_decl_ratio": float(adv_decl_ratio or 1.0),
                        "foreign_net_flow": float(surv_data.get("foreign_net_flow", 0.0) or 0.0),
                    },
                    "stock_data": {
                        "current_price": current_price,
                        "floor_absorbed": bool(lead_metrics.get("has_spring") or lead_metrics.get("has_absorption")),
                    },
                    **replay_context,
                })
                counter_data = res_counter.get("result", {}).get("data", {}) if res_counter.get("status") == "SUCCESS" else {}
                counter_verdict = counter_data.get("verdict")
                if res_counter.get("status") != "SUCCESS" or counter_verdict not in {"PROCEED", "CONDITIONAL", "BLOCK"}:
                    logger.warning(f"[Pha 6 - {ticker}] Counter Thesis response missing or invalid; rejecting candidate.")
                    continue
                cts_score = float(counter_data.get("cts_score", 0.0))
                logger.info(f"[Pha 6 - {ticker}] Phán quyết Counter-Thesis: '{counter_verdict}' | CTS Score = {cts_score:.1f}")

                # ── PHA 7: AGENT-12 (STRATEGY CIO ARBITRATION THEO POLICY CAP) ──
                res_cio = await AgentRegistry.dispatch("strategy_cio", {
                    "conflict": {
                        "thesis_id": thesis_data.get("thesis_id"),
                        "ticker": ticker,
                        "thesis_view": f"BULLISH_{conviction}",
                        "counter_view": counter_verdict,
                        "cts_score": cts_score,
                        "block_reasons": counter_data.get("block_reasons", []),
                        "max_single_stock_cap": policy.single_stock_cap_for_conviction(conviction),
                        "min_entry_weight": policy.min_entry_weight,
                    },
                    **replay_context,
                })
                cio_data = res_cio.get("result", {}).get("data", {}) if res_cio.get("status") == "SUCCESS" else {}
                final_resolution = cio_data.get("final_resolution")
                weight_cap = float(cio_data.get("weight_cap", 0.0))

                logger.info(f"[Pha 7 - {ticker}] Phán quyết CIO: '{final_resolution}' | Trần Tỷ trọng: {weight_cap:.1%}")
                if res_cio.get("status") != "SUCCESS" or final_resolution not in {"PROCEED_WITH_PENALTY", "APPROVE_CONDITIONAL"} or weight_cap <= 0.0:
                    logger.warning(f"[Pha 7 - {ticker}] CIO phủ quyết giải ngân đối với {ticker}: {cio_data.get('rationale')}")
                    continue

                thesis_body = thesis_data.get("thesis_body") or {}
                price_target = thesis_body.get("price_target") or {}
                finalists.append({
                    **item,
                    "sector": portfolio_sector,
                    "thesis_data": thesis_data,
                    "counter_data": counter_data,
                    "counter_verdict": counter_verdict,
                    "cts_score": cts_score,
                    "cio_data": cio_data,
                    "final_resolution": final_resolution,
                    "weight_cap": weight_cap,
                    "base_case_target_price": float(price_target.get("base_case") or 0.0),
                })

        # ── VÒNG 2B: XẾP HẠNG CHUNG KẾT CHÉO TẤT CẢ FINALISTS TRƯỚC KHI CẤP NAV ──
        optimizer = PortfolioOptimizer(min_positions=policy.min_positions, max_positions=policy.max_positions)
        selected_finalists, finalist_leaderboard = optimizer.rank_and_select_finalists(
            finalists=finalists,
            existing_positions=working_positions,
            total_nav=current_nav,
            max_positions=policy.max_positions,
            max_sector_weight=policy.max_sector_weight,
            min_slot_weight=policy.min_entry_weight,
            corr_matrix=corr_matrix,
            correlation_limit=0.65,
        )

        pipeline_trace["phases"]["phase_7_5_tournament_ranking"] = {
            "playbook": playbook.value,
            "aum_tier": policy.aum_tier.value,
            "available_slots_start": available_slots_start,
            "remaining_equity_budget_start": round(remaining_equity_budget, 4),
            "budget_allows_new_entry": budget_allows_new_entry,
            "stage1_passed_count": len(stage1_passed),
            "stage1_shortlist": [
                {
                    "ticker": x["ticker"],
                    "conviction": x["conviction"],
                    "css": x["css"],
                    "playbook_score": x["playbook_score"],
                    "rs_20d": x["research_report"].get("rs_20d"),
                    "vsa_swing_score": x["research_report"].get("vsa_swing_score"),
                    "sector_leadership_score": x["sector_leadership_score"],
                }
                for x in shortlist
            ],
            "finalist_leaderboard": finalist_leaderboard,
        }

        # ── PHA 8 → PHA 11: PHÂN BỔ VỐN, THẨM ĐỊNH RỦI RO & THỰC THI TỪ HẠNG #1 TRỞ XUỐNG ──
        effective_max_orders = min(max_candidates, max(1, policy.max_positions))
        for winner in selected_finalists:
            if len(qualified_orders_multi_agent) >= effective_max_orders:
                break

            ticker = winner["ticker"]
            research_report = winner["research_report"]
            thesis_data = winner["thesis_data"]
            counter_data = winner["counter_data"]
            counter_verdict = winner["counter_verdict"]
            cts_score = winner["cts_score"]
            cio_data = winner["cio_data"]
            final_resolution = winner["final_resolution"]
            weight_cap = winner["weight_cap"]
            conviction = winner["conviction"]
            current_price = winner["current_price"]
            portfolio_sector = winner["portfolio_sector"]
            final_rank_score = float(winner.get("final_rank_score", winner["playbook_score"]))
            rank_position = int(winner.get("rank", 1))

            # ── PHA 8: AGENT-06 (PORTFOLIO ALLOCATION THEO BIÊN ĐỘ LINH HOẠT & BẬC NAV) ──
            logger.info(
                f"[Pha 8 - #{rank_position} {ticker}] Phân bổ vốn (FinalScore={final_rank_score:.1f}, "
                f"Playbook={playbook.value}, Cap={weight_cap:.1%})..."
            )
            res_alloc = await AgentRegistry.dispatch("portfolio_allocation", {
                "candidate": {
                    "ticker": ticker,
                    "conviction": conviction,
                    "price": current_price,
                    "sector": portfolio_sector,
                    "adtv20": candidate_metadata.get(ticker, {}).get("adtv20", 0.0),
                    "adtv20_vnd": candidate_metadata.get(ticker, {}).get("adtv20_vnd", 0.0),
                    "adtv20_shares": candidate_metadata.get(ticker, {}).get("adtv20_shares", 0.0),
                },
                "research_report": research_report,
                "investment_thesis": thesis_data,
                "counter_thesis": counter_data,
                "total_nav": current_nav,
                "cash_balance": working_cash,
                "positions": working_positions,
                "kelly_matrix": kelly_matrix,
                "weight_cap": weight_cap,
                "regime": regime,
                "session_context": session_context,
                "policy": policy,
                "final_rank_score": final_rank_score,
                "corr_matrix": corr_matrix,
                "drawdown_tier": current_drawdown_tier,
                "defer_order_event": True,
                **replay_context,
            })
            if res_alloc.get("status") != "SUCCESS":
                logger.warning(f"[Pha 8 - {ticker}] Lỗi phân bổ vốn. Bỏ qua.")
                continue

            alloc_data = res_alloc["result"]["data"]
            target_shares = int(alloc_data.get("target_shares", 0))
            alloc_weight_pct = float(alloc_data.get("allocated_weight_pct", 0.0))
            if alloc_data.get("action") == "HOLD" or target_shares <= 0:
                logger.info(f"[Pha 8 - {ticker}] {alloc_data.get('action')}: {alloc_data.get('rationale')}")
                allocation_risk_decisions.append({
                    "rank": rank_position,
                    "ticker": ticker,
                    "stage": "ALLOCATION_HOLD",
                    "action": alloc_data.get("action"),
                    "weight_pct": alloc_weight_pct,
                    "rationale": alloc_data.get("rationale"),
                })
                continue

            proposed_order = alloc_data.get("proposed_order") or alloc_data
            logger.info(f"[Pha 8 - {ticker}] Đề xuất Lệnh: Mua {target_shares:,} cổ phiếu ({alloc_weight_pct:.2f}% NAV).")

            # ── PHA 9: AGENT-07 (PORTFOLIO RISK SUPREME GATEKEEPER) ──
            logger.info(f"[Pha 9 - {ticker}] Kích hoạt Agent-07: Thẩm định Rủi ro Tối cao...")
            res_risk = await AgentRegistry.dispatch("portfolio_risk", {
                "user_id": multi_agent_account_id,
                "proposed_order": proposed_order,
                "cdc_status": cdc_triggered,
                "policy": policy,
                "min_entry_weight": policy.min_entry_weight,
                "market_context": {
                    "distribution_days": distribution_days,
                    "breadth_ma20_pct": breadth_ma20_pct,
                    "regime": regime,
                },
                "portfolio": working_portfolio_state,
                **replay_context,
            })
            risk_data = res_risk.get("result", {}).get("data", {}) if res_risk.get("status") == "SUCCESS" else {}
            risk_status = risk_data.get("risk_status", "REJECT")
            risk_decision = risk_data.get("decision", {})
            approved_shares = int(risk_decision.get("approved_shares", 0))
            approved_weight_pct = float(risk_decision.get("approved_weight_pct", 0.0))
            thesis_body = thesis_data.get("thesis_body") or {}
            price_target = thesis_body.get("price_target") or {}
            decision_context = {
                "source_agent": "daily_pipeline_orchestrator",
                "playbook": playbook.value,
                "aum_tier": policy.aum_tier.value,
                "tournament_rank": rank_position,
                "final_rank_score": final_rank_score,
                "research": {
                    "conviction": conviction,
                    "css": research_report.get("css"),
                    "playbook_score": winner["playbook_score"],
                    "rs_20d": research_report.get("rs_20d"),
                    "vsa_swing_score": research_report.get("vsa_swing_score"),
                    "sector_leadership_score": winner["sector_leadership_score"],
                    "current_price": current_price,
                    "cts_score": cts_score,
                    "thesis_id": thesis_data.get("thesis_id"),
                    "thesis_status": thesis_data.get("status"),
                    "thesis": {
                        "why_now": thesis_body.get("why_now"),
                        "why_this_stock": thesis_body.get("why_this_stock"),
                        "catalyst": (thesis_body.get("catalyst") or {}).get("description"),
                        "base_case_target_price": price_target.get("base_case"),
                        "valuation_method": price_target.get("valuation_method"),
                        "independent_signals": (thesis_data.get("input_validation") or {}).get("independent_signals"),
                    },
                    "counter_thesis": {
                        "verdict": counter_data.get("verdict"),
                        "rationale": counter_data.get("rationale"),
                        "block_reasons": counter_data.get("block_reasons"),
                        "holes": counter_data.get("holes"),
                    },
                    "cio": {
                        "resolution": final_resolution,
                        "rationale": cio_data.get("executive_rationale") or cio_data.get("rationale"),
                        "conditions": cio_data.get("conditions"),
                        "weight_cap": weight_cap,
                    },
                },
                "allocation": {
                    "requested_shares": target_shares,
                    "target_weight_pct": alloc_data.get("target_weight_pct"),
                    "allocated_weight_pct": alloc_weight_pct,
                    "rationale": alloc_data.get("rationale"),
                },
                "risk": {
                    "decision_id": risk_data.get("decision_id"),
                    "status": risk_status,
                    "requested_shares": risk_decision.get("original_shares", target_shares),
                    "approved_shares": approved_shares,
                    "approved_weight_pct": approved_weight_pct,
                    "rationale": risk_decision.get("rationale"),
                    "sizing_trace": risk_decision.get("sizing_trace"),
                    "hard_laws": risk_data.get("hard_laws"),
                    "tape_anomaly": risk_data.get("tape_anomaly"),
                },
            }

            logger.info(
                f"[Pha 9 - {ticker}] Phán quyết Risk: '{risk_status}' | "
                f"Duyệt: {approved_shares:,} CP ({approved_weight_pct:.2f}% NAV) | {risk_decision.get('rationale', '')}"
            )

            reconcile_fn = getattr(self.portfolio_repo, "reconcile_decision_after_risk", None)
            if callable(reconcile_fn):
                reconcile_fn(
                    decision_id=proposed_order.get("decision_id") or alloc_data.get("decision_id"),
                    ticker=ticker,
                    risk_status=risk_status,
                    approved_shares=approved_shares,
                    approved_weight_pct=risk_decision.get("approved_weight_pct"),
                    risk_rationale=str(risk_decision.get("rationale") or ""),
                )

            allocation_risk_decisions.append({
                "rank": rank_position,
                "ticker": ticker,
                "stage": f"RISK_{risk_status}",
                "requested_shares": target_shares,
                "approved_shares": approved_shares,
                "approved_weight_pct": approved_weight_pct,
                "rationale": risk_decision.get("rationale"),
            })

            if approved_shares <= 0 or risk_status not in ["PASS", "REDUCE"]:
                logger.warning(f"[Pha 9 - {ticker}] Lệnh bị Risk Agent chặn/từ chối.")
                continue

            # ── PHA 10: AGENT-08 (TRADE EXECUTION VIA EAE VWAP SLICING) ──
            logger.info(f"[Pha 10 - {ticker}] Kích hoạt Agent-08: Thực thi Lệnh ({self.multi_agent_mode.value})...")
            execution_instruction = risk_decision.copy()
            execution_instruction["action"] = "BUY"
            execution_instruction["ticker"] = ticker
            execution_instruction["approved_shares"] = approved_shares
            execution_instruction["target_price"] = current_price
            execution_instruction["sector"] = proposed_order.get("sector", portfolio_sector)
            execution_instruction["decision_context"] = decision_context
            execution_instruction["risk_limits"] = policy.to_risk_limits(conviction=conviction)

            if replay_at:
                exec_data = {
                    "status": "APPROVED_PAPER_TRADING_PENDING_RUNNER_FILL",
                    "shares": approved_shares,
                    "execution_mode": "PAPER_TRADING",
                }
            elif self.multi_agent_mode != ExecutionMode.DISABLED:
                from app.infrastructure.external_api.market_data_service import market_data_svc
                orderbook = await market_data_svc.get_order_book(ticker)
                res_exec = await AgentRegistry.dispatch("trade_execution", {
                    "order_instruction": execution_instruction,
                    "adtv20_shares": candidate_metadata.get(ticker, {}).get("adtv20_shares", 0.0),
                    "orderbook": orderbook,
                    "execution_mode": self.multi_agent_mode.value,
                })
                exec_data = res_exec.get("result", {}).get("data", {}) if res_exec.get("status") == "SUCCESS" else {}
                logger.info(f"[Pha 10 - {ticker}] Trạng thái thực thi: {exec_data.get('status', 'UNKNOWN')}")
            else:
                exec_data = {"status": "DISABLED", "shares": 0}

            # Cập nhật ngân sách/slot NGAY TRONG PHIÊN (cho cả EXECUTED, PENDING_SHADOW và Replay Pending)
            if exec_data.get("status") == "EXECUTED" and not replay_at:
                try:
                    portfolio_state = self._load_current_portfolio_state(multi_agent_account_id, None)
                    current_nav = float(portfolio_state["total_nav"])
                    working_positions = [dict(p) for p in portfolio_state["positions"]]
                    working_cash = float(portfolio_state["cash_vnd"])
                    working_portfolio_state = {
                        **portfolio_state,
                        "positions": working_positions,
                        "sector_exposure": dict(portfolio_state.get("sector_exposure") or {}),
                        "cash_vnd": working_cash,
                    }
                except Exception:
                    logger.exception("[Pha 10 - %s] Could not refresh portfolio after execution; stopping further multi-agent orders.", ticker)
                    portfolio_refresh_failed = True
            elif exec_data.get("status") in {
                "PENDING_SHADOW",
                "APPROVED_PAPER_TRADING_PENDING_RUNNER_FILL",
                "DISABLED",
            }:
                reserved_val = float(approved_shares) * float(current_price)
                reserved_fee = max(reserved_val * 0.001, 10_000.0)
                working_cash = max(0.0, working_cash - reserved_val - reserved_fee)
                working_positions.append({
                    "ticker": ticker,
                    "symbol": ticker,
                    "shares": approved_shares,
                    "quantity": approved_shares,
                    "current_price": current_price,
                    "average_price": current_price,
                    "market_value": reserved_val,
                    "sector": portfolio_sector,
                    "locked_t25_shares": approved_shares,
                    "available_shares": 0,
                    "in_flight_pending": True,
                })
                working_portfolio_state["cash_vnd"] = working_cash
                working_portfolio_state["positions"] = working_positions
                working_portfolio_state["sector_exposure"][portfolio_sector] = (
                    working_portfolio_state["sector_exposure"].get(portfolio_sector, 0.0) + reserved_val
                )
                working_portfolio_state["locked_t25_value"] = (
                    float(working_portfolio_state.get("locked_t25_value", 0.0)) + reserved_val
                )

            # Đóng gói Lệnh Multi-Agent Book
            css_score_val = float(research_report.get("css", 75.0))
            z_score_val = round((css_score_val - 50.0) / 10.0, 2)
            approved_weight_frac = round((approved_shares * current_price) / current_nav, 4) if current_nav > 0 else 0.0

            order_record_ma = {
                "ticker": ticker,
                "tier": "TIER_A_PLUS" if conviction == "A+" else f"TIER_{conviction}",
                "conviction": conviction,
                "playbook": playbook.value,
                "tournament_rank": rank_position,
                "final_rank_score": final_rank_score,
                "z_score": z_score_val,
                "pred_score": css_score_val,
                "shares": int(exec_data.get("shares", 0)),
                "approved_shares": approved_shares,
                "price": float(exec_data.get("executed_price", 0.0)),
                "target_weight_pct": approved_weight_frac,
                "approved_weight_pct": approved_weight_pct,
                "hard_stop_pct": 0.07,
                "breakeven_trigger_pct": 0.025,
                "take_profit_pct": 0.15,
                "execution_mode": exec_data.get("execution_mode", self.multi_agent_mode.value),
                "execution_status": exec_data.get("status", "UNKNOWN"),
                "entry_type": playbook.value,
                "action": "SHADOW_PAPER_TRADE_ONLY",
                "rationale": (
                    f"[{playbook.value} #{rank_position}] Final={final_rank_score:.1f} | "
                    f"PB={winner['playbook_score']:.1f} | CSS={css_score_val:.1f} | "
                    f"CTS={cts_score:.1f} | Weight={approved_weight_pct:.2f}% NAV | CIO={final_resolution}"
                ),
                "decision_context": decision_context,
            }
            qualified_orders_multi_agent.append(order_record_ma)

            # ── PHA 11: AGENT-09 (POSITION MONITORING INITIALIZATION) ──
            if exec_data.get("status") == "EXECUTED":
                logger.info(f"[Pha 11 - {ticker}] Kích hoạt Agent-09: Đăng ký Giám sát Vị thế & 4 Tầng Phòng vệ...")
                res_mon = await AgentRegistry.dispatch("position_monitoring", {
                    "position": {
                        "ticker": ticker,
                        "average_price": exec_data["executed_price"],
                        "current_price": exec_data["executed_price"],
                        "quantity": exec_data["shares"],
                    },
                    "nav": current_nav,
                    **replay_context,
                })
                mon_data = res_mon.get("result", {}).get("data", {}) if res_mon.get("status") == "SUCCESS" else {}
                monitored_positions_created.append(mon_data)

            # Thu thập nhật ký kiểm toán cho Agent-11
            actions_to_audit.append({
                "agent_id": "portfolio_allocation",
                "event_type": "PROPOSED_ALLOCATION",
                "details": {"ticker": ticker, "shares": target_shares, "weight": approved_weight_frac},
            })
            actions_to_audit.append({
                "agent_id": "portfolio_risk",
                "event_type": "APPROVED_ORDER",
                "details": {"ticker": ticker, "approved_shares": approved_shares, "status": risk_status},
            })
            actions_to_audit.append({
                "agent_id": "trade_execution",
                "event_type": "TRADE_FILLED" if exec_data.get("status") == "EXECUTED" else "TRADE_NOT_FILLED",
                "details": exec_data,
            })
            if portfolio_refresh_failed:
                break

        pipeline_trace["phases"]["phase_8_9_allocation_risk"] = allocation_risk_decisions

        research_status_counts = Counter(item["status"] for item in research_gate_decisions)
        research_reason_counts = Counter(
            code for item in research_gate_decisions for code in item.get("reason_codes", [])
        )
        thesis_status_counts = Counter(item["status"] for item in thesis_gate_decisions)
        thesis_reason_counts = Counter(
            code for item in thesis_gate_decisions for code in item.get("reason_codes", [])
        )
        pipeline_trace["phases"]["phase_4_equity_research_gate"] = {
            "selected_count": len(selected_tickers),
            "evaluated_count": len(research_gate_decisions),
            "not_evaluated_count": max(0, len(selected_tickers) - len(research_gate_decisions)),
            "status_counts": dict(research_status_counts),
            "reason_counts": dict(research_reason_counts),
            "candidate_decisions": research_gate_decisions,
        }
        pipeline_trace["phases"]["phase_5_investment_thesis_gate"] = {
            "evaluated_count": len(thesis_gate_decisions),
            "status_counts": dict(thesis_status_counts),
            "reason_counts": dict(thesis_reason_counts),
            "candidate_decisions": thesis_gate_decisions,
        }
        logger.info(
            "[Gate Summary] Research evaluated=%d selected=%d statuses=%s reason_codes=%s; "
            "Thesis evaluated=%d statuses=%s reason_codes=%s",
            len(research_gate_decisions), len(selected_tickers), dict(research_status_counts),
            dict(research_reason_counts), len(thesis_gate_decisions), dict(thesis_status_counts),
            dict(thesis_reason_counts),
        )

        # =========================================================================
        # PHA 12: AGENT-11 (SYSTEM GOVERNANCE & CRYPTOGRAPHIC LEDGER)
        # =========================================================================
        logger.info("\n[Pha 12] Kích hoạt Agent-11: Thẩm định Tuân thủ Hiến pháp & Băm Sổ cái SHA-256...")
        res_gov = await AgentRegistry.dispatch("system_governance", {
            "execution_mode": self.multi_agent_mode.value,
            "actions_to_audit": actions_to_audit if actions_to_audit else [
                {"agent_id": "daily_pipeline_orchestrator", "event_type": "PIPELINE_RUN_COMPLETED", "details": {"date": run_date_str}}
            ],
            **replay_context,
        })
        gov_data = res_gov.get("result", {}).get("data", {}) if res_gov and res_gov.get("status") == "SUCCESS" else {}
        gov_status = gov_data.get("system_status", "COMPLIANT")
        audit_sha256 = gov_data.get("audit_block_hash") or gov_data.get("block_hash") or "0" * 64

        logger.info(f"[Pha 12] Governance Status: '{gov_status}' | Audit SHA-256: {audit_sha256[:16]}...")
        pipeline_trace["phases"]["phase_12_system_governance"] = {
            "status": gov_status,
            "governance_status": gov_status,
            "audit_sha256": audit_sha256,
        }

        logger.info(f"================================================================================")
        logger.info(
            f"[Daily Investment Pipeline] HOÀN TẤT THÀNH CÔNG: "
            f"Multi-Agent: {len(qualified_orders_multi_agent)} lệnh | "
            f"Governance: {gov_status}"
        )
        logger.info(f"================================================================================")

        if portfolio_refresh_failed:
            pipeline_trace["portfolio_state_refresh"] = {"status": "FAILED_AFTER_EXECUTION"}

        return {
            "date": run_date_str,
            "status": "FAILED" if portfolio_refresh_failed else "SUCCESS",
            "regime": regime,
            "session_context": session_context,
            "playbook": playbook.value,
            "policy": policy.to_dict(),
            "cash_ratio": cash_ratio,
            "multi_agent_mode": self.multi_agent_mode.value,
            "multi_agent_instructions": qualified_orders_multi_agent,
            "governance_status": gov_status,
            "audit_sha256": audit_sha256,
            "trace": pipeline_trace,
        }


# Singleton instance sẵn dùng cho toàn hệ thống
pipeline = DailyInvestmentPipeline()
daily_pipeline = pipeline
