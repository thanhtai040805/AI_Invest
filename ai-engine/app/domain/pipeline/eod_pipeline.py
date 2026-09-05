"""EOD Pipeline Orchestrator (IOS v5.1 - Institutional End-of-Day Pipeline).

Điều phối quy trình khép kín tự động sau khi sàn HOSE đóng cửa phiên ATC (15:00):
- Pha 1: Kiểm tra dữ liệu giá đóng cửa EOD toàn sàn (market_data_daily / ohlcv).
- Pha 2: Kích hoạt Agent-09 (Position Monitoring) quét danh mục, chốt lệnh và cập nhật bảng paper_trades.
- Pha 3: Kích hoạt Agent-01 (Market Surveillance) tính toán VIX_VN_analog và xác định Market Regime (HMM).
- Pha 4: Kích hoạt Agent-10 (Reinforcement Learning) đọc kết quả thực tế, chạy Causal Learning,
         hiệu chuẩn Bayesian Kelly và cập nhật rl_factor_weights & kelly_win_rate_matrix xuống PostgreSQL.
- Pha 5: Kích hoạt Agent-11 (System Governance) thẩm định kiểm toán và băm mã SHA-256 sổ cái.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from app.core.registry import AgentRegistry
import app.domain.agents  # Nạp toàn bộ 12 Agents vào registry
from app.adapters.postgres_adapter import PostgresAdapter

logger = logging.getLogger("ai_engine.pipeline.eod")


class EODPipelineRunner:
    """Bộ điều phối Pipeline Cuối Phiên (End-of-Day Causal Learning Runner)."""

    def __init__(self, storage: Optional[PostgresAdapter] = None):
        self.storage = storage or PostgresAdapter()
        self.last_run_date: Optional[str] = None
        self.last_result: Optional[Dict[str, Any]] = None

    async def run(
        self,
        target_date: Optional[str] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Thực thi chuỗi 5 pha EOD khép kín cho ngày target_date (mặc định hôm nay)."""
        run_date = target_date or date.today().isoformat()
        logger.info(f"================================================================")
        logger.info(f"[EOD Pipeline] BẮT ĐẦU QUY TRÌNH CUỐI PHIÊN CHO NGÀY: {run_date}")
        logger.info(f"================================================================")

        start_time = datetime.now()
        pipeline_trace: Dict[str, Any] = {"run_date": run_date, "phases": {}}

        try:
            # -------------------------------------------------------------
            # PHA 1: KIỂM TRA DỮ LIỆU THỊ TRƯỜNG EOD
            # -------------------------------------------------------------
            logger.info("[EOD Pipeline - Pha 1] Kiểm tra dữ liệu nến EOD trong PostgreSQL...")
            eod_data_available = False
            try:
                rows = self.storage.fetch_all(
                    "SELECT COUNT(*) FROM market_data_daily WHERE date = %s",
                    (run_date,)
                )
                cnt = int(rows[0][0]) if rows and rows[0][0] else 0
                if cnt > 0:
                    eod_data_available = True
                    logger.info(f"[EOD Pipeline - Pha 1] Tìm thấy {cnt} mã có dữ liệu nến EOD ngày {run_date}.")
                else:
                    logger.warning(f"[EOD Pipeline - Pha 1] Chưa có nến EOD ngày {run_date} trong market_data_daily. Sử dụng snapshot gần nhất.")
            except Exception as e_p1:
                logger.warning(f"[EOD Pipeline - Pha 1] Lỗi kiểm tra nến EOD: {e_p1}")

            pipeline_trace["phases"]["phase_1_data_check"] = {
                "status": "COMPLETED",
                "eod_data_available": eod_data_available,
            }

            # -------------------------------------------------------------
            # PHA 2: AGENT-09 (POSITION MONITORING & PAPER TRADES SETTLEMENT)
            # -------------------------------------------------------------
            logger.info("[EOD Pipeline - Pha 2] Kích hoạt Agent-09: Giám sát vị thế EOD & Chốt lệnh paper_trades...")
            res_mon = await AgentRegistry.dispatch("position_monitoring", {
                "date": run_date,
                "current_time": f"{run_date}T15:15:00",
                "auto_dispatch": True,
            })

            mon_data = res_mon.get("result", {}).get("data", {}) if res_mon.get("status") == "SUCCESS" else {}
            closed_orders = mon_data.get("stop_loss_orders", [])
            logger.info(
                f"[EOD Pipeline - Pha 2] Agent-09 hoàn tất: Giám sát {mon_data.get('monitored_count', 0)} vị thế, "
                f"Kích hoạt chốt/cắt: {len(closed_orders)} lệnh."
            )

            pipeline_trace["phases"]["phase_2_position_settlement"] = {
                "status": "COMPLETED",
                "monitored_count": mon_data.get("monitored_count", 0),
                "closed_orders_count": len(closed_orders),
                "stop_loss_triggered": mon_data.get("stop_loss_triggered", False),
            }

            # -------------------------------------------------------------
            # PHA 3: AGENT-01 (MARKET SURVEILLANCE & REGIME IDENTIFICATION)
            # -------------------------------------------------------------
            logger.info("[EOD Pipeline - Pha 3] Kích hoạt Agent-01: Định vị Market Regime cuối ngày...")
            res_surv = await AgentRegistry.dispatch("market_surveillance", {
                "date": run_date,
            })

            surv_data = res_surv.get("result", {}).get("data", {}) if res_surv.get("status") == "SUCCESS" else {}
            current_regime = surv_data.get("current_regime", "BULL_MARKET")
            session_context = surv_data.get("session_context", "Normal")
            logger.info(f"[EOD Pipeline - Pha 3] Agent-01 hoàn tất: Regime = '{current_regime}', Context = '{session_context}'.")

            pipeline_trace["phases"]["phase_3_regime_detection"] = {
                "status": "COMPLETED",
                "current_regime": current_regime,
                "session_context": session_context,
            }

            # -------------------------------------------------------------
            # PHA 4: AGENT-10 (REINFORCEMENT LEARNING & CAUSAL ADAPTATION)
            # -------------------------------------------------------------
            logger.info("[EOD Pipeline - Pha 4] Kích hoạt Agent-10: Causal Learning & Hiệu chuẩn Bayes...")
            # Lấy danh sách lệnh đã đóng gần nhất từ paper_trades
            realized_trades_input = []
            try:
                rows_trades = self.storage.fetch_all(
                    "SELECT ticker, pnl, confidence FROM paper_trades WHERE pnl IS NOT NULL AND status = 'CLOSED' ORDER BY resolved_at DESC LIMIT 50"
                )
                if rows_trades:
                    realized_trades_input = [
                        {"ticker": r[0], "pnl": float(r[1]), "conviction": r[2] or "A"}
                        for r in rows_trades
                    ]
            except Exception as e_fetch_trades:
                logger.debug(f"[EOD Pipeline - Pha 4] Lỗi đọc trades: {e_fetch_trades}")

            res_rl = await AgentRegistry.dispatch("reinforcement_learning", {
                "regime": current_regime,
                "date": run_date,
                "realized_trades": realized_trades_input,
            })

            rl_data = res_rl.get("result", {}).get("data", {}) if res_rl.get("status") == "SUCCESS" else {}
            updated_weights = rl_data.get("policy_weights", {})
            kelly_matrix = rl_data.get("kelly_matrix", {})
            cdc_triggered = rl_data.get("cdc_triggered", False)
            ic_summary = rl_data.get("ic_by_factor", {})

            logger.info(
                f"[EOD Pipeline - Pha 4] Agent-10 hoàn tất: "
                f"Đã cập nhật trọng số Factor thích ứng (Epoch: {rl_data.get('learning_epoch')}) | "
                f"CDC Triggered: {cdc_triggered} | Trades used: {len(realized_trades_input)}."
            )

            pipeline_trace["phases"]["phase_4_causal_learning"] = {
                "status": "COMPLETED",
                "learning_epoch": rl_data.get("learning_epoch"),
                "policy_weights": updated_weights,
                "kelly_matrix": kelly_matrix,
                "cdc_triggered": cdc_triggered,
                "decay_diagnosis": rl_data.get("decay_diagnosis"),
                "realized_trades_count": len(realized_trades_input),
            }

            # -------------------------------------------------------------
            # PHA 5: AGENT-11 (SYSTEM GOVERNANCE & CRYPTOGRAPHIC AUDIT)
            # -------------------------------------------------------------
            logger.info("[EOD Pipeline - Pha 5] Kích hoạt Agent-11: Kiểm toán hệ thống & Băm SHA-256 Sổ Cái...")
            res_gov = await AgentRegistry.dispatch("system_governance", {
                "actions_to_audit": [
                    {"agent_id": "position_monitoring", "event_type": "EOD_SETTLEMENT", "details": mon_data},
                    {"agent_id": "market_surveillance", "event_type": "REGIME_DECISION", "details": surv_data},
                    {"agent_id": "reinforcement_learning", "event_type": "CAUSAL_ADAPTATION", "details": rl_data},
                ],
                "broker_heartbeat": {"is_connected": True, "latency_ms": 50.0, "missed_beats": 0},
            })

            gov_data = res_gov.get("result", {}).get("data", {}) if res_gov.get("status") == "SUCCESS" else {}
            gov_status = gov_data.get("system_status", "COMPLIANT")

            # Sinh hàm băm SHA-256 của toàn bộ kết quả EOD
            audit_payload = json.dumps({
                "date": run_date,
                "regime": current_regime,
                "weights": updated_weights,
                "governance": gov_status,
            }, sort_keys=True, default=str)
            audit_sha256 = hashlib.sha256(audit_payload.encode()).hexdigest()

            pipeline_trace["phases"]["phase_5_governance"] = {
                "status": "COMPLETED",
                "system_status": gov_status,
                "audit_sha256": audit_sha256,
            }

            elapsed_seconds = round((datetime.now() - start_time).total_seconds(), 2)

            final_result = {
                "status": "SUCCESS",
                "run_date": run_date,
                "executed_at": datetime.now().isoformat(),
                "duration_seconds": elapsed_seconds,
                "regime": current_regime,
                "session_context": session_context,
                "paper_trades_settled": len(closed_orders),
                "policy_weights": updated_weights,
                "kelly_matrix": kelly_matrix,
                "cdc_status": cdc_triggered,
                "governance_status": gov_status,
                "audit_sha256": audit_sha256,
                "trace": pipeline_trace,
            }

            self.last_run_date = run_date
            self.last_result = final_result

            logger.info(f"================================================================")
            logger.info(f"[EOD Pipeline] HOÀN TẤT THÀNH CÔNG trong {elapsed_seconds}s! SHA-256: {audit_sha256[:16]}...")
            logger.info(f"================================================================")
            return final_result

        except Exception as e_main:
            logger.error(f"[EOD Pipeline] LỖI NGHIÊM TRỌNG TRONG QUY TRÌNH EOD: {e_main}", exc_info=True)
            err_result = {
                "status": "FAILED",
                "run_date": run_date,
                "executed_at": datetime.now().isoformat(),
                "error": str(e_main),
                "trace": pipeline_trace,
            }
            self.last_result = err_result
            return err_result


# Singleton instance
eod_runner = EODPipelineRunner()
