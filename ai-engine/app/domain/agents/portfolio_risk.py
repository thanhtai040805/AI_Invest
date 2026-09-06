"""AGENT-06: Portfolio Risk Agent (Vietnamized Institutional Risk Engine — vNext)
HOSE Spot Equity Sovereign Pre-Trade Risk Gateway (100% Cổ phiếu cơ sở — Không phái sinh).

Chức năng & Trách nhiệm:
- Nắm quyền phủ quyết tối cao (Sovereign Gatekeeper) trước khi lệnh được bắn ra thị trường.
- 5 Lớp thẩm định độc lập:
    1. Lớp 1: Hard Laws thể chế (Single Stock <= 15%, Sector <= 35%, ADTV <= 25%, T+2.5 Loss <= 2% NAV).
    2. Lớp 2: Quản trị rủi ro kẹt hàng T+2.5 (Locked Exposure <= 35% NAV, Đệm rủi ro 2 cây sàn -13.51%).
    3. Lớp 3: Cảm biến Dị thường Giá & Khối lượng (Tape Anomaly VSA: Churning, Upthrust, Breakdown).
    4. Lớp 4: Đo lường Tail Risk & Drawdown Protocol (EGARCH-t Student-t, Hist ES, De-risk nhanh / Re-risk chậm).
    5. Lớp 5: Giám sát Suy thoái Mô hình (CDC Tiers: IC Decay + Persistence >= 5 phiên + Slippage Spike).
- Quyết định thể chế trả về: PASS, REDUCE, BLOCK, INCREASE CASH, FREEZE NEW RISK, ACTIVATE CDC.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from app.core.base_agent import BaseAgent
from app.domain.rules.hard_laws import HardLawEngine, ProposedOrder, PortfolioState, HardLawCheck
from app.domain.rules.risk.tape_anomaly_detector import (
    TapeAnomalyDetector,
    TapeAnomalyResult,
    TapeAnomalySeverity,
    AnomalyType,
)
from app.domain.rules.risk.t25_exposure_manager import T25ExposureManager, T25CapacityCheck
from app.domain.rules.risk.breadth_risk_engine import BreadthRiskEngine, BreadthRiskEvaluation, BreadthHealthTier
from app.domain.rules.risk.tail_risk_engine import TailRiskEngine, TailRiskSnapshot
from app.domain.rules.risk.drawdown_recovery_protocol import (
    DrawdownRecoveryProtocol,
    DrawdownEvaluation,
    DrawdownTier,
)
from app.domain.rules.risk.cdc_controller import CDCController, CDCEvaluation, CDCTier

logger = logging.getLogger(__name__)


class PortfolioRiskAgent(BaseAgent):
    """
    AGENT-06: Chuyên viên Quản trị Rủi ro Danh mục Định chế (Chief Risk Officer Engine).
    Cổng kiểm soát rủi ro thực thi (Pre-trade Risk Gateway) cho 100% cổ phiếu cơ sở sàn HOSE.
    """

    def __init__(self):
        super().__init__(
            agent_name="portfolio_risk",
            state_tables=["risk_snapshots", "risk_limits"],
            log_table="log_portfolio_risk",
            enabled=True,
        )
        self.hard_law_engine = HardLawEngine()
        self.tape_anomaly_detector = TapeAnomalyDetector()
        self.t25_manager = T25ExposureManager()
        self.breadth_engine = BreadthRiskEngine()
        self.tail_risk_engine = TailRiskEngine()
        self.drawdown_protocol = DrawdownRecoveryProtocol()
        self.cdc_controller = CDCController()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Thẩm định rủi ro thể chế và phê duyệt/điều chỉnh/hủy lệnh đề xuất:
        - event_data:
            - portfolio: {total_nav, peak_nav, cash_vnd, positions, sector_exposure, locked_t25_value, returns_series} (tùy chọn, tự động nạp DB nếu thiếu)
            - proposed_order: {ticker, side, quantity/target_shares, price, stop_loss_price, sector, adtv20, candle, ma20_volume} (tùy chọn)
            - market_context: {distribution_days, breadth_ma20_pct, vnindex_change_pct, market_beta} (tùy chọn)
            - model_risk: {ic_decay_pct, persistence_sessions, actual_slippage_pct} (tùy chọn)
        """
        # =========================================================================
        # 0. NẠP DỮ LIỆU THỰC TẾ (PORTFOLIO, RISK LIMITS, MARKET CONTEXT)
        # =========================================================================
        user_id = event_data.get("user_id")
        portfolio_input = event_data.get("portfolio")

        # 0.1 Nạp hạn mức rủi ro thể chế từ bảng risk_limits CSDL
        risk_limits: Dict[str, float] = {
            "limit_type": "HOSE_EQUITY",
            "max_single_stock_pct": 15.0,
            "max_sector_pct": 35.0,
            "hard_stop_loss_pct": 2.0,
        }
        try:
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()
            db_limits = intel_repo.get_risk_limits("HOSE_EQUITY")
            if db_limits:
                risk_limits.update(db_limits)
        except Exception as e_lim:
            logger.debug(f"[PortfolioRiskAgent] Lỗi khi nạp risk_limits từ DB ({e_lim}), sử dụng mặc định IOS v5.1")

        # 0.2 Hydrate Portfolio: Triệt tiêu bẫy Phantom Portfolio (Portfolio Blindness)
        nav: float = 1000000000.0
        peak_nav: float = 1000000000.0
        cash_vnd: float = 1000000000.0
        positions: Dict[str, Dict[str, Any]] = {}
        sector_exposure: Dict[str, float] = {}
        locked_t25_value: float = 0.0
        returns_series: List[float] = []

        is_estimated_nav: bool = False
        if not portfolio_input or not isinstance(portfolio_input, dict) or "total_nav" not in portfolio_input:
            try:
                from app.domain.repositories.portfolio_repository import PortfolioRepository
                portfolio_repo = PortfolioRepository()
                acc_state = portfolio_repo.get_account_state(user_id=user_id)
                if acc_state and "total_nav" in acc_state and float(acc_state.get("total_nav", 0)) > 0:
                    nav = float(acc_state["total_nav"])
                    peak_nav = float(acc_state.get("peak_nav", nav))
                    cash_vnd = float(acc_state.get("cash_balance", nav))
                else:
                    is_estimated_nav = True
                    logger.critical(
                        "[PortfolioRiskAgent] CRITICAL: DB Account State trống và không có portfolio_input. "
                        "Sử dụng NAV mặc định 1,000,000,000 VND (ESTIMATED mode)."
                    )
            except Exception as e_acc:
                is_estimated_nav = True
                logger.critical(
                    f"[PortfolioRiskAgent] CRITICAL: Không thể nạp Account State ({e_acc}). "
                    "Sử dụng NAV mặc định 1,000,000,000 VND (ESTIMATED mode)."
                )

                open_positions = portfolio_repo.get_open_positions(user_id=user_id)
                for pos in open_positions:
                    sym = str(pos.get("ticker", pos.get("symbol", ""))).upper().strip()
                    if not sym:
                        continue
                    qty = int(pos.get("shares", pos.get("quantity", 0)))
                    cur_p = float(pos.get("current_price", pos.get("average_price", 0.0)))
                    avg_p = float(pos.get("average_price", cur_p))
                    locked_shares = int(pos.get("locked_t25_shares", 0))
                    sector = str(pos.get("sector", "Unknown"))

                    positions[sym] = {
                        "quantity": qty,
                        "shares": qty,
                        "current_price": cur_p,
                        "price": cur_p,
                        "average_price": avg_p,
                        "locked_t25_shares": locked_shares,
                        "sector": sector,
                    }
                    sector_exposure[sector] = sector_exposure.get(sector, 0.0) + (qty * cur_p)
                    locked_t25_value += locked_shares * cur_p

                logger.info(
                    f"[PortfolioRiskAgent] Tự động nạp tài khoản thực tế: NAV={nav:,.0f} VND, "
                    f"Cash={cash_vnd:,.0f} VND, {len(positions)} vị thế nắm giữ."
                )
            except Exception as e_port:
                logger.warning(f"[PortfolioRiskAgent] Không thể nạp tài khoản từ DB ({e_port}), fallback in-memory")
                if portfolio_input and isinstance(portfolio_input, dict):
                    nav = float(portfolio_input.get("total_nav", 1000000000.0))
                    peak_nav = float(portfolio_input.get("peak_nav", nav))
                    positions = dict(portfolio_input.get("positions", {}))
                    sector_exposure = dict(portfolio_input.get("sector_exposure", {}))
                    locked_t25_value = float(portfolio_input.get("locked_t25_value", 0.0))
                    returns_series = list(portfolio_input.get("returns_series", []))
        else:
            nav = float(portfolio_input.get("total_nav", 1000000000.0))
            peak_nav = float(portfolio_input.get("peak_nav", nav))
            cash_vnd = float(portfolio_input.get("cash_vnd", 1000000000.0))
            positions_raw = portfolio_input.get("positions", {})
            if isinstance(positions_raw, list):
                for p in positions_raw:
                    if isinstance(p, dict):
                        sym = str(p.get("ticker", p.get("symbol", ""))).upper().strip()
                        if sym:
                            positions[sym] = dict(p)
            elif isinstance(positions_raw, dict):
                positions = dict(positions_raw)

            sector_exposure = dict(portfolio_input.get("sector_exposure", {}))
            locked_t25_value = float(portfolio_input.get("locked_t25_value", 0.0))
            returns_series = list(portfolio_input.get("returns_series", []))

        # 0.3 Nạp chuỗi returns lịch sử nếu chưa có (phục vụ Tail Risk EGARCH-t & ES 97.5%)
        if not returns_series:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                market_repo = MarketDataRepository()
                vnindex_bars = market_repo.get_ohlcv("VNINDEX", limit=60)
                if vnindex_bars and len(vnindex_bars) > 1:
                    # Chuẩn hóa về chuỗi thời gian tăng dần (chronological ASC) trước khi tính lợi suất
                    bars_asc = list(reversed(vnindex_bars)) if (
                        "time" in vnindex_bars[0] and "time" in vnindex_bars[-1]
                        and str(vnindex_bars[0].get("time")) > str(vnindex_bars[-1].get("time"))
                    ) else list(vnindex_bars)
                    closes = [float(b.get("close", 0.0)) for b in bars_asc]
                    returns_series = [
                        (closes[i] - closes[i - 1]) / closes[i - 1]
                        for i in range(1, len(closes))
                        if closes[i - 1] > 0
                    ]
            except Exception as e_ret:
                logger.debug(f"[PortfolioRiskAgent] Không thể nạp returns_series từ VNINDEX: {e_ret}")

        # 0.4 Nạp Market Context (Breadth, Regime)
        market_ctx = event_data.get("market_context", {})
        if not market_ctx:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                market_repo = MarketDataRepository()
                regime_data = market_repo.get_latest_market_regime()
                if regime_data:
                    b_ratio = float(regime_data.get("breadth_ratio", 0.55))
                    market_ctx = {
                        "distribution_days": 0,
                        "breadth_ma20_pct": round(b_ratio * 100, 1),
                        "vnindex_change_pct": 0.0,
                        "market_beta": 1.10,
                        "regime": regime_data.get("regime", "UNKNOWN"),
                    }
            except Exception as e_mkt:
                logger.debug(f"[PortfolioRiskAgent] Không thể nạp market_regime: {e_mkt}")

        distribution_days = int(market_ctx.get("distribution_days", 0))
        breadth_ma20_pct = float(market_ctx.get("breadth_ma20_pct", 55.0))
        vnindex_change_pct = float(market_ctx.get("vnindex_change_pct", 0.0))
        market_beta = float(market_ctx.get("market_beta", 1.10))

        # 0.5 Giám sát Model Risk & CDC
        model_risk = event_data.get("model_risk", {})
        ic_decay_pct = float(model_risk.get("ic_decay_pct", 0.0))
        if "cdc_status" in event_data and event_data["cdc_status"]:
            ic_decay_pct = max(ic_decay_pct, 0.55)  # Hỗ trợ backward compatibility
        persistence_sessions = int(model_risk.get("persistence_sessions", 0))
        actual_slippage_pct = float(model_risk.get("actual_slippage_pct", 0.003))

        # Tự động tra cứu CSDL factor_ic_history nếu event_data không truyền model_risk/cdc_status
        if not model_risk and "cdc_status" not in event_data:
            try:
                from app.adapters.postgres_adapter import PostgresAdapter
                storage = PostgresAdapter()
                rows_ic = storage.fetch_all(
                    """
                    SELECT cdc_decay_flag, rolling_20d_ic, rolling_60d_ic
                    FROM factor_ic_history
                    ORDER BY date DESC LIMIT 6
                    """
                )
                if rows_ic:
                    decay_flags = [bool(r[0]) for r in rows_ic if r[0] is not None]
                    if any(decay_flags):
                        ic_decay_pct = 0.55
                        persistence_sessions = sum(1 for f in decay_flags if f)
                        logger.warning(
                            f"[PortfolioRiskAgent] Tự động phát hiện cdc_decay_flag = True từ DB factor_ic_history! "
                            f"Kích hoạt CDC Guard ({persistence_sessions} factors cảnh báo)."
                        )
            except Exception as e_ic:
                logger.debug(f"Không thể tra cứu factor_ic_history từ CSDL: {e_ic}")

        observation_days = int(event_data.get("observation_days_below_threshold", 2))

        # =========================================================================
        # 1. LỚP ĐO LƯỜNG TỔNG THỂ (TAIL RISK, BREADTH, DRAWDOWN, CDC)
        # =========================================================================
        tail_snapshot: TailRiskSnapshot = self.tail_risk_engine.evaluate_tail_risk(
            returns_series=returns_series,
            portfolio_positions=positions,
            market_beta=market_beta,
        )

        breadth_eval: BreadthRiskEvaluation = self.breadth_engine.evaluate_market_breadth(
            distribution_days=distribution_days,
            breadth_ma20_pct=breadth_ma20_pct,
            vnindex_change_pct=vnindex_change_pct,
        )

        dd_eval: DrawdownEvaluation = self.drawdown_protocol.evaluate_drawdown(
            current_nav=nav,
            peak_nav=peak_nav,
            observation_days_below_threshold=observation_days,
            tail_risk_safe=(tail_snapshot.tail_risk_verdict == "SAFE"),
            breadth_healthy=(breadth_eval.health_tier == BreadthHealthTier.HEALTHY),
        )

        cdc_eval: CDCEvaluation = self.cdc_controller.evaluate_model_health(
            ic_decay_pct=ic_decay_pct,
            persistence_sessions=persistence_sessions,
            actual_slippage_pct=actual_slippage_pct,
        )

        # =========================================================================
        # 2. KIỂM ĐỊNH LỆNH ĐỀ XUẤT (NẾU CÓ PROPOSED ORDER)
        # =========================================================================
        proposed_order_raw = event_data.get("proposed_order")
        if not proposed_order_raw and "candidate" in event_data:
            proposed_order_raw = event_data["candidate"]

        action = "PASS"
        approved_shares = 0
        original_shares = 0
        order_ticker = "UNKNOWN"
        order_price = 0.0
        reasons_list: List[str] = []

        hard_law_status_map = {
            "single_stock": "PASS",
            "sector": "PASS",
            "position_risk": "PASS",
            "liquidity_limit": "PASS",
            "t25_capacity": "PASS",
            "all_passed": True,
        }

        tape_anomaly_map = {
            "detected": False,
            "anomaly_type": "NONE",
            "severity": "NONE",
            "reason": "Chưa có dữ liệu nến hoặc không phát hiện dị thường.",
        }

        if proposed_order_raw:
            order_ticker = str(proposed_order_raw.get("ticker", "UNKNOWN")).upper().strip()
            order_price = float(proposed_order_raw.get("price", proposed_order_raw.get("target_price", 0.0)))
            original_shares = int(proposed_order_raw.get("quantity", proposed_order_raw.get("target_shares", 0)))
            stop_loss_p = proposed_order_raw.get("stop_loss_price")
            if stop_loss_p is not None:
                stop_loss_p = float(stop_loss_p)
            else:
                stop_loss_p = order_price * 0.93 if order_price > 0 else None  # Default 7% stop loss
            order_sector = proposed_order_raw.get("sector", "Unknown")
            adtv20 = float(proposed_order_raw.get("adtv20", 2000000.0))

            p_state = PortfolioState(
                nav=nav,
                positions=positions,
                sector_exposure=sector_exposure,
                locked_t25_value=locked_t25_value,
            )
            p_order = ProposedOrder(
                ticker=order_ticker,
                side=proposed_order_raw.get("side", "BUY"),
                quantity=original_shares,
                price=order_price,
                stop_loss_price=stop_loss_p,
                sector=order_sector,
            )

            # A. Kiểm tra Hard Laws (Lớp 1) kèm Dynamic risk_limits
            hl_check: HardLawCheck = self.hard_law_engine.check_order(
                p_order, p_state, adtv20, risk_limits=risk_limits
            )
            if not hl_check.passed:
                hard_law_status_map["all_passed"] = False
                reason_lower = hl_check.reason.lower()
                law_str = str(hl_check.violated_law).lower()

                if "cổ phiếu" in reason_lower or "single" in law_str or "single_stock" in reason_lower:
                    hard_law_status_map["single_stock"] = "BLOCK"
                elif "ngành" in reason_lower or "sector" in law_str or "sector" in reason_lower:
                    hard_law_status_map["sector"] = "BLOCK"
                elif "thanh khoản" in law_str or "adtv" in reason_lower:
                    hard_law_status_map["liquidity_limit"] = "BLOCK"
                else:
                    hard_law_status_map["position_risk"] = "BLOCK"
                reasons_list.append(f"HARD LAW VIOLATION: {hl_check.reason}")

            # B. Kiểm tra Sức chứa T+2.5 và Đệm rủi ro 2 cây sàn (Lớp 2)
            proposed_val = order_price * original_shares
            t25_check: T25CapacityCheck = self.t25_manager.check_t25_capacity(
                nav=nav,
                locked_t25_value=locked_t25_value,
                proposed_order_value=proposed_val,
                price=order_price,
                stop_loss_price=stop_loss_p,
            )
            if not t25_check.passed:
                hard_law_status_map["t25_capacity"] = "BLOCK" if t25_check.max_safe_shares == 0 else "WARNING"
                reasons_list.append(f"T+2.5 RISK: {t25_check.reason}")

            # C. Kiểm tra Cảm biến Dị thường Giá & Volume VSA (Lớp 3) - Tự động nạp CSDL nếu thiếu
            candle_data = proposed_order_raw.get("candle") or proposed_order_raw.get("current_candle")
            ma20_vol = float(proposed_order_raw.get("ma20_volume", adtv20))
            ma20_pr = float(proposed_order_raw.get("ma20_price", 0.0)) or None
            swing_low_pr = float(proposed_order_raw.get("swing_low_price", 0.0)) or None

            if not candle_data and order_ticker and order_ticker != "UNKNOWN":
                try:
                    from app.domain.repositories.market_data_repository import MarketDataRepository
                    market_repo = MarketDataRepository()
                    ohlcv_bars = market_repo.get_ohlcv(order_ticker, limit=20)
                    if ohlcv_bars and len(ohlcv_bars) > 0:
                        # Nhận diện nến gần nhất: Nếu DB trả về time DESC (mới nhất ở [0]), lấy [0]
                        # Nếu mock/list không có time hoặc time ASC, lấy [-1]
                        if len(ohlcv_bars) > 1 and "time" in ohlcv_bars[0] and "time" in ohlcv_bars[-1]:
                            if str(ohlcv_bars[0].get("time")) > str(ohlcv_bars[-1].get("time")):
                                last_bar = ohlcv_bars[0]
                            else:
                                last_bar = ohlcv_bars[-1]
                        else:
                            last_bar = ohlcv_bars[-1]
                        candle_data = {
                            "open": float(last_bar.get("open", order_price)),
                            "high": float(last_bar.get("high", order_price)),
                            "low": float(last_bar.get("low", order_price)),
                            "close": float(last_bar.get("close", order_price)),
                            "volume": float(last_bar.get("volume", 0.0)),
                        }
                        vols = [float(b.get("volume", 0.0)) for b in ohlcv_bars if b.get("volume")]
                        if vols:
                            ma20_vol = sum(vols) / len(vols)
                        closes = [float(b.get("close", 0.0)) for b in ohlcv_bars if b.get("close")]
                        if closes:
                            ma20_pr = sum(closes) / len(closes)
                        lows = [float(b.get("low", 0.0)) for b in ohlcv_bars if b.get("low")]
                        if lows:
                            swing_low_pr = min(lows)
                except Exception as e_tape_db:
                    logger.debug(f"[PortfolioRiskAgent] Không thể nạp OHLCV cho {order_ticker}: {e_tape_db}")

            if candle_data:
                tape_res: TapeAnomalyResult = self.tape_anomaly_detector.analyze_candle(
                    candle=candle_data,
                    ma20_volume=ma20_vol,
                    ma20_price=ma20_pr,
                    swing_low_price=swing_low_pr,
                )
                tape_anomaly_map = {
                    "detected": tape_res.has_anomaly,
                    "anomaly_type": tape_res.anomaly_type.value,
                    "severity": tape_res.severity.value,
                    "reason": tape_res.reason,
                }
                if tape_res.severity == TapeAnomalySeverity.CRITICAL:
                    reasons_list.append(f"TAPE ANOMALY: {tape_res.reason}")

            # =========================================================================
            # 3. TỔNG HỢP PHÁN QUYẾT THỂ CHẾ (POLICY DECISION MATRIX)
            # =========================================================================
            # Điều kiện BLOCK Tuyệt đối:
            is_blocked = (
                (not hl_check.passed)
                or (hard_law_status_map["t25_capacity"] == "BLOCK")
                or (tape_anomaly_map["severity"] == "CRITICAL")
                or (dd_eval.tier == DrawdownTier.RED)
                or (cdc_eval.tier == CDCTier.RED)
                or (breadth_eval.action_recommended == "BLOCK_BUY")
            )

            if is_blocked:
                action = "BLOCK"
                approved_shares = 0
            else:
                # Tính toán hệ số co giãn quy mô vị thế (Scaling Factor)
                scale_factors = [
                    dd_eval.exposure_scale_factor,
                    cdc_eval.sizing_scale_factor,
                ]
                if breadth_eval.action_recommended == "REDUCE_SIZE":
                    scale_factors.append(0.50)
                if tape_anomaly_map["severity"] == TapeAnomalySeverity.WARNING:
                    scale_factors.append(0.70)

                combined_scale = min(scale_factors)
                calculated_shares = int(original_shares * combined_scale)

                if t25_check.max_safe_shares > 0:
                    calculated_shares = min(calculated_shares, t25_check.max_safe_shares)

                # Làm tròn lô 100 cổ phiếu sàn HOSE
                approved_shares = (calculated_shares // 100) * 100 if calculated_shares >= 100 else 0

                if approved_shares <= 0:
                    action = "BLOCK"
                    reasons_list.append("Sau khi áp dụng hệ số co giãn rủi ro, số lượng cổ phiếu khả dụng < 100 cổ.")
                elif approved_shares < original_shares:
                    action = "REDUCE"
                    reduction_pct = round((1.0 - (approved_shares / original_shares)) * 100, 1)
                    reasons_list.append(f"Tự động cắt giảm {reduction_pct}% quy mô do điều kiện phòng thủ.")
                else:
                    action = "PASS"
                    reasons_list.append("Thỏa mãn 100% tiêu chuẩn an toàn vốn thể chế.")

        decision_id = f"RSK-{datetime.now().strftime('%Y%m%d')}-{str(uuid.uuid4())[:8].upper()}"
        min_cash_target = max(
            dd_eval.min_cash_target_pct,
            breadth_eval.recommended_min_cash_pct,
            cdc_eval.recommended_min_cash_pct,
        )

        final_rationale = " | ".join(reasons_list) if reasons_list else "Danh mục hoạt động trong hạn mức an toàn."

        risk_output = {
            "risk_status": action,
            "decision_id": decision_id,
            "timestamp": datetime.now().isoformat(),
            "ticker": order_ticker,
            "hard_laws": hard_law_status_map,
            "tape_anomaly": tape_anomaly_map,
            "tail_risk": {
                "historical_es_97_5": tail_snapshot.historical_es_97_5,
                "egarch_student_t_es": tail_snapshot.egarch_student_t_es,
                "stress_es": tail_snapshot.stress_es,
                "data_status": tail_snapshot.data_status,
                "tail_risk_verdict": tail_snapshot.tail_risk_verdict,
            },
            "concentration": {
                "stock_weight_post": round((order_price * approved_shares) / nav, 4) if nav > 0 else 0.0,
                "sector_weight_post": round(sector_exposure.get(proposed_order_raw.get("sector", "") if proposed_order_raw else "", 0.0) / nav, 4) if nav > 0 else 0.0,
                "avg_correlation": 0.44,
            },
            "market_breadth": {
                "distribution_days": breadth_eval.distribution_days_count,
                "breadth_ma20_pct": breadth_eval.breadth_ma20_pct,
                "health_tier": breadth_eval.health_tier.value,
                "is_divergence": breadth_eval.is_divergence_green_index_red_breadth,
            },
            "drawdown": {
                "current_drawdown_pct": dd_eval.current_drawdown_pct,
                "tier": dd_eval.tier.value,
                "re_risking_state": dd_eval.re_risking_state,
            },
            "cdc": {
                "tier": cdc_eval.tier.value,
                "is_cdc_active": cdc_eval.is_cdc_active,
                "ic_decay_pct": cdc_eval.ic_decay_pct,
            },
            "decision": {
                "decision_id": decision_id,
                "ticker": order_ticker,
                "action": action,
                "side": "BUY" if approved_shares > 0 else "HOLD",
                "price": order_price,
                "target_price": order_price,
                "original_shares": original_shares,
                "approved_shares": approved_shares,
                "target_shares": approved_shares,
                "shares": approved_shares,
                "approved_weight_pct": round(((approved_shares * order_price) / nav) * 100, 2) if nav > 0 else 0.0,
                "exposure_reduction_pct": round((1.0 - (approved_shares / original_shares)) * 100, 1) if original_shares > 0 else 0.0,
                "min_cash_target_pct": min_cash_target,
                "rationale": final_rationale,
            },
            # Backward compatibility fields for legacy callers
            "drawdown_tier": dd_eval.tier.value,
            "max_drawdown_pct": dd_eval.current_drawdown_pct,
            "drawdown_action": dd_eval.action_description,
            "garch_cash_target_pct": min_cash_target,
            "macro_risk_score": 50.0,
            "es_97_5_pct": tail_snapshot.historical_es_97_5 * 100,
            "cdc_active": cdc_eval.is_cdc_active,
            "proposed_order_check": {
                "passed": (action != "BLOCK"),
                "violated_law": None if action != "BLOCK" else "HARD_LAW_OR_RISK_GATE",
                "reason": final_rationale,
            },
            "governance": {
                "model_version": "VIETNAM_INSTITUTIONAL_RISK_vNext",
                "policy_version": "HOSE_SPOT_EQUITY_RISK_POLICY_2026",
                "asset_scope": "100% SPOT EQUITY HOSE (NO DERIVATIVES)",
                "risk_limits": risk_limits,
                "is_estimated_nav": is_estimated_nav,
            },
        }

        # Tự động lưu risk snapshot vào CSDL risk_snapshots
        try:
            from datetime import date as dt_date
            from app.domain.repositories.intelligence_repository import IntelligenceRepository
            intel_repo = IntelligenceRepository()
            intel_repo.save_risk_snapshot({
                "date": dt_date.today(),
                "es_97_5": risk_output["es_97_5_pct"],
                "garch_cash_target": min_cash_target,
                "drawdown_tier": dd_eval.tier.value,
                "max_drawdown_from_peak": dd_eval.current_drawdown_pct,
                "cdc_active": cdc_eval.is_cdc_active,
            })
        except Exception as e_snap:
            logger.warning(f"Không thể lưu risk_snapshot vào DB: {e_snap}")

        # Bắn sự kiện lên RabbitMQ Event Bus
        try:
            from app.core.event_topics import EventTopics
            # 1. Sự kiện Drawdown Tier thay đổi nếu phát hiện drawdown cấp thiết
            if dd_eval.tier in (DrawdownTier.YELLOW, DrawdownTier.ORANGE, DrawdownTier.RED):
                await self.publish_event(
                    topic=EventTopics.DRAWDOWN_TIER_CHANGED,
                    payload={
                        "decision_id": decision_id,
                        "tier": dd_eval.tier.value,
                        "current_drawdown_pct": dd_eval.current_drawdown_pct,
                        "action_description": dd_eval.action_description,
                        "min_cash_target_pct": min_cash_target,
                        "timestamp": datetime.now().isoformat(),
                    },
                )

            # 2. Sự kiện lệnh được Phê duyệt / Điều chỉnh quy mô (PASS / REDUCE) hoặc Cảnh báo Vi phạm (BLOCK)
            if action in ("PASS", "REDUCE") and approved_shares > 0:
                await self.publish_event(
                    topic=EventTopics.RISK_APPROVED,
                    payload={
                        "decision_id": decision_id,
                        "ticker": order_ticker,
                        "risk_status": action,
                        "side": "BUY",
                        "approved_shares": approved_shares,
                        "approved_price": order_price,
                        "approved_weight_pct": risk_output["decision"]["approved_weight_pct"],
                        "min_cash_target_pct": min_cash_target,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            elif action == "BLOCK" and proposed_order_raw:
                await self.publish_event(
                    topic=EventTopics.RISK_BREACH_ALERT,
                    payload={
                        "decision_id": decision_id,
                        "ticker": order_ticker,
                        "risk_status": action,
                        "side": proposed_order_raw.get("side", "BUY"),
                        "requested_shares": original_shares,
                        "reasons": reasons_list,
                        "hard_laws": hard_law_status_map,
                        "timestamp": datetime.now().isoformat(),
                    },
                )
        except Exception as e_pub:
            logger.warning(f"[PortfolioRiskAgent] Không thể phát sự kiện Risk lên RabbitMQ ({e_pub})")

        trace = {
            "risk_gateway": "Sovereign Institutional Pre-Trade Gateway",
            "layers_evaluated": [
                "1. Hard Laws (Single Stock, Sector, ADTV Capacity, T+2.5 Floor Loss)",
                "2. T+2.5 Exposure Lock Manager",
                "3. Tape Anomaly Detector (VSA Churning, Upthrust, Breakdown)",
                "4. Tail Risk Engine (Historical ES 97.5%, EGARCH-t, Stress Matrix)",
                "5. Drawdown Phased Recovery State Machine",
                "6. Capital Degradation Controller (CDC)",
            ],
            "asset_scope": "100% Cổ phiếu cơ sở giao ngay (Spot Equity)",
            "risk_limits": risk_limits,
        }

        return {"data": risk_output, "trace": trace}
