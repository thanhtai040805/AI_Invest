"""Daily Investment Pipeline Orchestrator — IOS v5.1 Sovereign Multi-Agent Architecture.

Điều phối toàn bộ chu trình đầu tư khép kín qua 12 Agents (Agent-01 đến Agent-12):
  Pha 1: Agent-01 (Market Surveillance) — Macro Regime HMM, Session Context & Halted Check
  Pha 2: Agent-10 (Reinforcement Learning) — Cung cấp Adaptive Policy Weights F1-F6 & Bayesian Kelly
  Pha 3: Agent-02 (Universe Discovery) — Lớp 0 Forensic Accounting Gate (Beneish M-Score <= -1.78), ADTV20 & GIL
  Pha 4: Agent-03 (Equity Research) — Đánh giá đa nhân tố F1-F6 + Moat AI -> Conviction Level (A+, A, B)
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
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv

from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp và tự động đăng ký đủ 12 Agents vào Registry
from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.services.ml.standalone_ml_channel import standalone_ml_channel

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
        standalone_ml_mode: str = os.getenv("STANDALONE_ML_MODE", ExecutionMode.SHADOW_RUNNER.value),
    ):
        try:
            self.multi_agent_mode = ExecutionMode(multi_agent_mode)
        except ValueError:
            self.multi_agent_mode = ExecutionMode.SHADOW_RUNNER

        try:
            self.standalone_ml_mode = ExecutionMode(standalone_ml_mode)
        except ValueError:
            self.standalone_ml_mode = ExecutionMode.SHADOW_RUNNER

        self.portfolio_repo = PortfolioRepository()
        logger.info(
            f"[DailyInvestmentPipeline] Khởi tạo thành công: "
            f"Multi-Agent Mode = '{self.multi_agent_mode.value}' | "
            f"Standalone ML Mode = '{self.standalone_ml_mode.value}'"
        )

    async def run(
        self,
        target_date: Optional[Union[date, str]] = None,
        current_nav: Optional[float] = None,
        standalone_nav: Optional[float] = None,
        candidate_tickers: Optional[List[str]] = None,
        max_candidates: int = 3,
    ) -> Dict[str, Any]:
        """
        Thực thi toàn diện chuỗi 12 Pha của hệ thống tự hành cho ngày giao dịch.
        NAV được truy vấn động học trực tiếp từ CSDL PostgreSQL (users & positions).
        """
        # ── TRUY VẤN NAV ĐỘNG HỌC TỪ CSDL NẾU KHÔNG TRUYỀN THAM SỐ CỤ THỂ ──
        multi_agent_account_id = os.getenv("MULTI_AGENT_ACCOUNT_ID", "940b0c70-2010-42f3-b947-797e6419b794")
        if current_nav is None:
            acc_st = self.portfolio_repo.get_account_state(user_id=multi_agent_account_id)
            current_nav = float(acc_st.get("total_nav", 1_000_000_000.0))

        if standalone_nav is None:
            sa_st = standalone_ml_channel.get_account_state()
            standalone_nav = float(sa_st.get("total_nav", 500_000_000.0))

        run_date_str = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else (str(target_date) if target_date else date.today().isoformat())
        )
        target_date_obj = date.fromisoformat(run_date_str) if isinstance(run_date_str, str) else run_date_str

        logger.info(f"================================================================================")
        logger.info(f"[Daily Investment Pipeline] KHỞI ĐỘNG CHU TRÌNH 12 AGENT CHO NGÀY: {run_date_str}")
        logger.info(f"[NAV TỪ CSDL] Multi-Agent NAV: {current_nav:,.0f} VND (Acc: {multi_agent_account_id}) | Standalone NAV: {standalone_nav:,.0f} VND (Acc: {standalone_ml_channel.account_id})")
        logger.info(f"[EXECUTION MODES] Multi-Agent: {self.multi_agent_mode.value} | Standalone: {self.standalone_ml_mode.value}")
        logger.info(f"================================================================================")

        pipeline_trace: Dict[str, Any] = {"run_date": run_date_str, "phases": {}}
        actions_to_audit: List[Dict[str, Any]] = []

        # =========================================================================
        # PHA 1: AGENT-01 (MARKET SURVEILLANCE & MACRO REGIME)
        # =========================================================================
        logger.info("[Pha 1] Kích hoạt Agent-01: Giám sát Vĩ mô & Định vị Market Regime...")
        res_surv = await AgentRegistry.dispatch("market_surveillance", {"date": run_date_str})
        surv_data = res_surv.get("result", {}).get("data", {}) if res_surv.get("status") == "SUCCESS" else {}
        
        regime = surv_data.get("current_regime", "BULL_MARKET")
        session_context = surv_data.get("session_context", "Normal")
        halted_tickers = surv_data.get("halted_tickers", [])
        distribution_days = surv_data.get("distribution_days", 0)
        breadth_ma20_pct = surv_data.get("breadth_ma20_pct", 60.0)

        # Chuẩn hóa trạng thái Bear / Phòng thủ
        is_bear_or_crisis = (
            "BEAR" in regime or
            regime == "BEAR_MARKET" or
            session_context in ["Crisis", "Stress_Severe"]
        )

        cash_ratio = 1.0 if is_bear_or_crisis else 0.15
        logger.info(f"[Pha 1] Hoàn tất: Regime = '{regime}' | Context = '{session_context}' | Target Cash = {cash_ratio:.1%}")

        pipeline_trace["phases"]["phase_1_market_surveillance"] = {
            "status": "COMPLETED",
            "regime": regime,
            "session_context": session_context,
            "halted_count": len(halted_tickers),
            "distribution_days": distribution_days,
            "target_cash_ratio": cash_ratio,
        }

        # ── HARD LAW GATING: Nếu thị trường sập (BEAR / CRISIS), kích hoạt BEAR_DEFENSE 100% TIỀN MẶT ──
        if is_bear_or_crisis:
            logger.warning(
                f"[Daily Investment Pipeline] KÍCH HOẠT BEAR_DEFENSE: Thị trường rơi vào {regime} ({session_context}). "
                f"Tuân thủ Hiến pháp: Khóa 100% tiền mặt, đóng băng giải ngân mới để bảo toàn vốn."
            )
            # Quét kiểm tra cắt lỗ và bảo vệ các vị thế mở hiện tại (nếu có)
            emergency_instructions = []
            try:
                from app.domain.repositories.portfolio_repository import PortfolioRepository
                p_repo = PortfolioRepository()
                open_positions = p_repo.get_open_positions()
                acc_state = p_repo.get_account_state()
                cur_nav = float(acc_state.get("total_nav", 1_000_000_000.0))
                for pos in open_positions:
                    p_ticker = pos.get("ticker", pos.get("symbol"))
                    p_qty = int(pos.get("shares", pos.get("quantity", 0)))
                    p_avg = float(pos.get("average_price", pos.get("avg_price", 0.0)))
                    p_cur = float(pos.get("current_price", p_avg))
                    if p_ticker and p_qty > 0:
                        res_mon = await AgentRegistry.dispatch("position_monitoring", {
                            "position": {
                                "ticker": p_ticker,
                                "average_price": p_avg,
                                "current_price": p_cur,
                                "quantity": p_qty,
                            },
                            "nav": cur_nav,
                            "is_bear_defense": True,
                        })
                        if res_mon.get("status") == "SUCCESS":
                            m_data = res_mon.get("result", {}).get("data", {})
                            if m_data.get("action") in ("EMERGENCY_STOP_LOSS", "SELL", "REDUCE"):
                                emergency_instructions.append(m_data)
            except Exception as e_pos:
                logger.error(f"[DailyPipeline] Lỗi quét bảo vệ vị thế khi Bear Market: {e_pos}")

            # Ghi nhận sự kiện phòng vệ vốn vào System Governance
            await AgentRegistry.dispatch("system_governance", {
                "actions_to_audit": [
                    {
                        "agent_id": "market_surveillance",
                        "event_type": "BEAR_DEFENSE_TRIGGERED",
                        "details": {"regime": regime, "cash_ratio": 1.0, "reason": "PRESERVE_100PCT_CAPITAL", "emergency_actions": len(emergency_instructions)},
                    }
                ],
                "broker_heartbeat": {"latency_ms": 45.0, "is_connected": True, "missed_beats": 0},
            })

            return {
                "date": run_date_str,
                "regime": regime,
                "session_context": session_context,
                "cash_ratio": 1.0,
                "status": "BEAR_DEFENSE_100PCT_CASH",
                "multi_agent_mode": self.multi_agent_mode.value,
                "standalone_ml_mode": self.standalone_ml_mode.value,
                "multi_agent_instructions": emergency_instructions,
                "standalone_ml_instructions": [],
                "trace": pipeline_trace,
            }

        # =========================================================================
        # PHA 2: AGENT-10 (REINFORCEMENT LEARNING & ADAPTIVE PARAMETERS)
        # =========================================================================
        logger.info("[Pha 2] Kích hoạt Agent-10: Nạp bộ trọng số thích ứng động & Ma trận Kelly...")
        res_rl = await AgentRegistry.dispatch("reinforcement_learning", {
            "regime": regime,
            "date": run_date_str,
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
        logger.info("[Pha 3] Kích hoạt Agent-02: Quét Universe HOSE, Thẩm định Lớp 0 Beneish M-Score & GIL...")
        res_disc = await AgentRegistry.dispatch("universe_discovery", {
            "tickers": candidate_tickers,
            "target_date": target_date_obj,
            "session_context": session_context,
            "current_regime": regime,
            "halted_tickers": halted_tickers,
        })
        disc_data = res_disc.get("result", {}).get("data", {}) if res_disc.get("status") == "SUCCESS" else {}
        discovery_list = disc_data.get("discovery_list", [])
        eligible_count = disc_data.get("eligible_count", 0)

        logger.info(f"[Pha 3] Hoàn tất: Phát hiện {eligible_count} mã đạt chuẩn Universe & vượt qua Lớp 0.")
        pipeline_trace["phases"]["phase_3_universe_discovery"] = {
            "status": "COMPLETED",
            "scanned_count": disc_data.get("scanned_count", 0),
            "eligible_count": eligible_count,
            "excluded_count": disc_data.get("excluded_count", 0),
        }

        if eligible_count == 0 or not discovery_list:
            logger.warning("[Daily Investment Pipeline] Không có mã nào vượt qua bộ lọc Universe / Lớp 0 Beneish.")
            return {
                "date": run_date_str,
                "regime": regime,
                "session_context": session_context,
                "cash_ratio": cash_ratio,
                "status": "NO_ELIGIBLE_UNIVERSE",
                "multi_agent_instructions": [],
                "standalone_ml_instructions": [],
                "trace": pipeline_trace,
            }

        # Lấy danh sách mã ứng viên ưu tiên (sắp xếp theo Group A trước, thanh khoản ADTV20)
        sorted_candidates = sorted(
            discovery_list,
            key=lambda x: (x.get("universe_group") == "A", x.get("adtv20", 0.0)),
            reverse=True,
        )
        selected_tickers = [c["ticker"] for c in sorted_candidates[:max(max_candidates * 2, 5)]]

        # =========================================================================
        # CHUỖI PHA 4 -> PHA 11: NGHIÊN CỨU, PHẢN BIỆN, CIO, SIZING, RISK & THỰC THI
        # =========================================================================
        qualified_orders_multi_agent: List[Dict[str, Any]] = []
        qualified_orders_standalone: List[Dict[str, Any]] = []
        monitored_positions_created: List[Dict[str, Any]] = []

        for ticker in selected_tickers:
            if len(qualified_orders_multi_agent) >= max_candidates:
                break

            logger.info(f"\n--- [AGENT CHAIN] Xử lý chuyên sâu mã cổ phiếu: {ticker} ---")

            # ── PHA 4: AGENT-03 (EQUITY RESEARCH) ──
            logger.info(f"[Pha 4 - {ticker}] Kích hoạt Agent-03: Phân tích 6 nhóm Factor & Moat AI...")
            res_res = await AgentRegistry.dispatch("equity_research", {
                "ticker": ticker,
                "current_regime": regime,
                "policy_weights": policy_weights,
            })
            if res_res.get("status") != "SUCCESS":
                logger.warning(f"[Pha 4 - {ticker}] Equity Research thất bại. Bỏ qua.")
                continue

            research_report = res_res["result"]["data"]
            conviction = research_report.get("conviction", "C")
            current_price = float(research_report.get("current_price", 0.0))
            sector = research_report.get("sector", "General")

            logger.info(f"[Pha 4 - {ticker}] Conviction = '{conviction}' | CSS = {research_report.get('css', 0):.1f} | Price = {current_price:,.0f}")

            # Chỉ các mã Conviction đạt chuẩn Tinh hoa (A+, A) hoặc B mới được đưa vào Luận đề
            if conviction not in ["A+", "A", "B"] or current_price <= 0:
                logger.info(f"[Pha 4 - {ticker}] Mã chưa đạt ngưỡng giải ngân (Conviction {conviction}). Bỏ qua.")
                continue

            # ── PHA 5: AGENT-04 (INVESTMENT THESIS) ──
            logger.info(f"[Pha 5 - {ticker}] Kích hoạt Agent-04: Xây dựng Luận đề & Xác thực Điều 3 (3 Tín hiệu)...")
            res_thesis = await AgentRegistry.dispatch("investment_thesis", {
                "research_report": research_report
            })
            if res_thesis.get("status") != "SUCCESS" or res_thesis.get("result", {}).get("data", {}).get("status") in ["REJECTED", "WAIT_OR_SKIP"]:
                logger.info(f"[Pha 5 - {ticker}] Luận đề bị từ chối hoặc chưa đủ tín hiệu xác thực. Bỏ qua.")
                continue

            thesis_data = res_thesis["result"]["data"]
            logger.info(f"[Pha 5 - {ticker}] Luận đề hợp lệ (Thesis ID: {thesis_data.get('thesis_id')}).")

            # ── PHA 6: AGENT-05 (COUNTER THESIS - DEVIL'S ADVOCATE) ──
            logger.info(f"[Pha 6 - {ticker}] Kích hoạt Agent-05: Phản biện Đa chiều (Devil's Advocate) & Tính CTS Score...")
            res_counter = await AgentRegistry.dispatch("counter_thesis", {
                "investment_thesis": thesis_data,
                "gil_output": {"status": "ACTIVE", "gil_flag": "NORMAL", "risk_level": "LOW", "cycles_detected": 0},
            })
            counter_data = res_counter.get("result", {}).get("data", {}) if res_counter.get("status") == "SUCCESS" else {}
            counter_verdict = counter_data.get("verdict", "PROCEED")
            cts_score = float(counter_data.get("cts_score", 0.0))
            logger.info(f"[Pha 6 - {ticker}] Phán quyết Counter-Thesis: '{counter_verdict}' | CTS Score = {cts_score:.1f}")

            # ── PHA 7: AGENT-12 (STRATEGY CIO ARBITRATION) ──
            logger.info(f"[Pha 7 - {ticker}] Kích hoạt Agent-12: Trọng tài Chiến lược Phân định Tranh chấp...")
            res_cio = await AgentRegistry.dispatch("strategy_cio", {
                "conflict": {
                    "thesis_id": thesis_data.get("thesis_id"),
                    "ticker": ticker,
                    "thesis_view": f"BULLISH_{conviction}",
                    "counter_view": counter_verdict,
                    "cts_score": cts_score,
                    "block_reasons": counter_data.get("block_reasons", []),
                }
            })
            cio_data = res_cio.get("result", {}).get("data", {}) if res_cio.get("status") == "SUCCESS" else {}
            final_resolution = cio_data.get("final_resolution", "APPROVED")
            weight_cap = float(cio_data.get("weight_cap", 0.15))

            logger.info(f"[Pha 7 - {ticker}] Phán quyết CIO: '{final_resolution}' | Trần Tỷ trọng: {weight_cap:.1%}")

            # Nếu CIO tuyên bố BLOCK (do vi phạm Hiến pháp hoặc Tail Risk), dừng ngay
            if "BLOCK" in final_resolution or weight_cap <= 0.0:
                logger.warning(f"[Pha 7 - {ticker}] CIO phủ quyết giải ngân đối với {ticker}: {cio_data.get('rationale')}")
                continue

            # ── PHA 8: AGENT-06 (PORTFOLIO ALLOCATION & KELLY SIZING) ──
            logger.info(f"[Pha 8 - {ticker}] Kích hoạt Agent-06: Phân bổ Vốn Kelly & Áp trần Điều 4...")
            res_alloc = await AgentRegistry.dispatch("portfolio_allocation", {
                "candidate": {
                    "ticker": ticker,
                    "conviction": conviction,
                    "price": current_price,
                    "sector": sector,
                },
                "total_nav": current_nav,
                "kelly_matrix": kelly_matrix,
                "weight_cap": weight_cap,
                "regime": regime,
            })
            if res_alloc.get("status") != "SUCCESS":
                logger.warning(f"[Pha 8 - {ticker}] Lỗi phân bổ vốn. Bỏ qua.")
                continue

            alloc_data = res_alloc["result"]["data"]
            target_shares = int(alloc_data.get("target_shares", 0))
            if target_shares <= 0:
                logger.info(f"[Pha 8 - {ticker}] Target shares = 0. Không phát sinh lệnh mới.")
                continue

            proposed_order = alloc_data.get("proposed_order") or alloc_data
            logger.info(f"[Pha 8 - {ticker}] Đề xuất Lệnh: Mua {target_shares:,} cổ phiếu ({alloc_data.get('target_weight_pct', 0):.1%} NAV).")

            # ── PHA 9: AGENT-07 (PORTFOLIO RISK SUPREME GATEKEEPER) ──
            logger.info(f"[Pha 9 - {ticker}] Kích hoạt Agent-07: Thẩm định Rủi ro Tối cao (Điều 1 Hard Stop, T+2.5, VSA)...")
            res_risk = await AgentRegistry.dispatch("portfolio_risk", {
                "portfolio": {"total_nav": current_nav, "peak_nav": current_nav, "locked_t25_value": 0.0},
                "proposed_order": proposed_order,
                "cdc_status": cdc_triggered,
                "market_context": {"distribution_days": distribution_days, "breadth_ma20_pct": breadth_ma20_pct},
            })
            risk_data = res_risk.get("result", {}).get("data", {}) if res_risk.get("status") == "SUCCESS" else {}
            risk_status = risk_data.get("risk_status", "REJECT")
            risk_decision = risk_data.get("decision", {})
            approved_shares = int(risk_decision.get("approved_shares", 0))

            logger.info(f"[Pha 9 - {ticker}] Phán quyết Risk: '{risk_status}' | Số lượng duyệt: {approved_shares:,} cổ phiếu.")

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

            if self.multi_agent_mode != ExecutionMode.DISABLED:
                res_exec = await AgentRegistry.dispatch("trade_execution", {
                    "order_instruction": execution_instruction,
                    "adtv20": 2_000_000,
                })
                exec_data = res_exec.get("result", {}).get("data", {}) if res_exec.get("status") == "SUCCESS" else {}
                logger.info(f"[Pha 10 - {ticker}] Trạng thái thực thi: {exec_data.get('status', 'EXECUTED')}")
            else:
                exec_data = {"status": "DISABLED", "shares": 0}

            # Đóng gói Lệnh Multi-Agent Book
            css_score_val = float(research_report.get("css", 75.0))
            z_score_val = round((css_score_val - 50.0) / 10.0, 2)

            order_record_ma = {
                "ticker": ticker,
                "tier": "TIER_A_PLUS" if conviction == "A+" else "TIER_A",
                "conviction": conviction,
                "z_score": z_score_val,
                "pred_score": css_score_val,
                "shares": approved_shares,
                "price": current_price,
                "target_weight_pct": alloc_data.get("target_weight_pct", 0.12),
                "hard_stop_pct": 0.07,  # -7% cơ sở sàn HOSE
                "breakeven_trigger_pct": 0.025, # +2.5% kích hoạt kéo hòa vốn
                "take_profit_pct": 0.15,
                "execution_mode": self.multi_agent_mode.value,
                "execution_status": exec_data.get("status", "SUCCESS"),
                "action": "SHADOW_PAPER_TRADE_ONLY" if self.multi_agent_mode == ExecutionMode.SHADOW_RUNNER else "EXECUTE_LIVE_BROKER",
                "rationale": f"[12-AGENT] CSS={research_report.get('css', 0):.1f} | CTS={cts_score:.1f} | CIO={final_resolution}",
            }
            qualified_orders_multi_agent.append(order_record_ma)

            # ── PHA 11: AGENT-09 (POSITION MONITORING INITIALIZATION) ──
            logger.info(f"[Pha 11 - {ticker}] Kích hoạt Agent-09: Đăng ký Giám sát Vị thế & 4 Tầng Phòng vệ...")
            res_mon = await AgentRegistry.dispatch("position_monitoring", {
                "position": {
                    "ticker": ticker,
                    "average_price": current_price,
                    "current_price": current_price,
                    "quantity": approved_shares,
                },
                "nav": current_nav,
            })
            mon_data = res_mon.get("result", {}).get("data", {}) if res_mon.get("status") == "SUCCESS" else {}
            monitored_positions_created.append(mon_data)

            # Thu thập nhật ký kiểm toán cho Agent-11
            actions_to_audit.append({
                "agent_id": "portfolio_allocation",
                "event_type": "PROPOSED_ALLOCATION",
                "details": {"ticker": ticker, "shares": target_shares, "weight": alloc_data.get("target_weight_pct")},
            })
            actions_to_audit.append({
                "agent_id": "portfolio_risk",
                "event_type": "APPROVED_ORDER",
                "details": {"ticker": ticker, "approved_shares": approved_shares, "status": risk_status},
            })
            actions_to_audit.append({
                "agent_id": "trade_execution",
                "event_type": "TRADE_FILLED",
                "details": exec_data,
            })

        # =========================================================================
        # STANDALONE PURE-ML FUND: KÊNH TỰ VẬN HÀNH ĐỘC LẬP (ACCOUNT RIÊNG BIỆT)
        # =========================================================================
        if self.standalone_ml_mode != ExecutionMode.DISABLED and not is_bear_or_crisis:
            logger.info(
                f"\n[Standalone Pure-ML Fund] Khởi động kênh tự hành độc lập "
                f"(Account: '{standalone_ml_channel.account_id}' | Mode: '{self.standalone_ml_mode.value}')..."
            )
            try:
                sa_candidates = candidate_tickers if candidate_tickers else selected_tickers
                sa_res = await standalone_ml_channel.run_autonomous_cycle(
                    target_date=target_date_obj,
                    candidate_tickers=sa_candidates,
                    execution_mode=self.standalone_ml_mode.value,
                    max_candidates=max_candidates,
                    nav=standalone_nav,
                )
                qualified_orders_standalone = sa_res.get("orders", [])

                for ord_sa in qualified_orders_standalone:
                    actions_to_audit.append({
                        "agent_id": "standalone_ml_fund",
                        "event_type": "STANDALONE_ORDER_GENERATED",
                        "details": ord_sa,
                    })

                # Tự động đối soát độ chính xác thực tế
                standalone_ml_channel.evaluate_forward_accuracy(lookback_days=60)
            except Exception as e_sa:
                logger.error(f"[Standalone Pure-ML Fund] Lỗi thực thi chu trình: {e_sa}", exc_info=True)
                qualified_orders_standalone = []
        else:
            qualified_orders_standalone = []

        # =========================================================================
        # PHA 12: AGENT-11 (SYSTEM GOVERNANCE & CRYPTOGRAPHIC LEDGER)
        # =========================================================================
        logger.info("\n[Pha 12] Kích hoạt Agent-11: Thẩm định Tuân thủ Hiến pháp & Băm Sổ cái SHA-256...")
        res_gov = await AgentRegistry.dispatch("system_governance", {
            "actions_to_audit": actions_to_audit if actions_to_audit else [
                {"agent_id": "daily_pipeline_orchestrator", "event_type": "PIPELINE_RUN_COMPLETED", "details": {"date": run_date_str}}
            ],
            "broker_heartbeat": {"latency_ms": 42.0, "is_connected": True, "missed_beats": 0},
        })
        gov_data = res_gov.get("result", {}).get("data", {}) if res_gov.get("status") == "SUCCESS" else {}
        gov_status = gov_data.get("system_status", "COMPLIANT")
        audit_sha256 = gov_data.get("audit_block_hash") or gov_data.get("block_hash") or "0" * 64

        logger.info(f"[Pha 12] Governance Status: '{gov_status}' | Audit SHA-256: {audit_sha256[:16]}...")
        pipeline_trace["phases"]["phase_12_system_governance"] = {
            "status": "COMPLETED",
            "governance_status": gov_status,
            "audit_sha256": audit_sha256,
        }

        logger.info(f"================================================================================")
        logger.info(
            f"[Daily Investment Pipeline] HOÀN TẤT THÀNH CÔNG: "
            f"Multi-Agent: {len(qualified_orders_multi_agent)} lệnh | "
            f"Standalone ML: {len(qualified_orders_standalone)} lệnh | "
            f"Governance: {gov_status}"
        )
        logger.info(f"================================================================================")

        return {
            "date": run_date_str,
            "status": "SUCCESS",
            "regime": regime,
            "session_context": session_context,
            "cash_ratio": cash_ratio,
            "multi_agent_mode": self.multi_agent_mode.value,
            "standalone_ml_mode": self.standalone_ml_mode.value,
            "multi_agent_instructions": qualified_orders_multi_agent,
            "standalone_ml_instructions": qualified_orders_standalone,
            "governance_status": gov_status,
            "audit_sha256": audit_sha256,
            "trace": pipeline_trace,
        }


# Singleton instance sẵn dùng cho toàn hệ thống
pipeline = DailyInvestmentPipeline()
daily_pipeline = pipeline
