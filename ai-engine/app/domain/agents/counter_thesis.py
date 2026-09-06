"""AGENT-05: Counter Thesis Agent (IOS v5.1)

Mục đích:
- Chủ động tìm lý do để BÁC BỎ thesis. Devil's Advocate bắt buộc.
- Tính toán 3-Tier Counter-Thesis Score (Base CTS + ML Interaction + Regime Multiplier).
- Đánh giá ngoại lệ Bắt đáy Khoa học (Capitulation Entry - Bẫy 3).
- Phán quyết: PROCEED / CONDITIONAL (kèm execution_constraints) / BLOCK (kèm block_reasons).
- Bảng nghiệp vụ quản lý: counter_thesis_verdicts
- Bảng log audit: log_counter_thesis
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional
from datetime import date

from app.core.base_agent import BaseAgent
from app.domain.rules.counter_thesis import CounterThesisEngine, Verdict
from app.adapters.sag_connector import sag_connector
from app.domain.rules.beneish import beneish_engine

logger = logging.getLogger(__name__)


class CounterThesisAgent(BaseAgent):
    """
    AGENT-05: Chuyên viên Phản biện Luận điểm Đầu tư (Devil's Advocate).
    """

    def __init__(self):
        super().__init__(
            agent_name="counter_thesis",
            state_tables=["counter_thesis_verdicts"],
            log_table="log_counter_thesis",
            enabled=True,
        )
        self.counter_thesis_engine = CounterThesisEngine()

    async def process(self, event_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Xử lý phản biện toàn diện luận điểm đầu tư (Devil's Advocate):
        - event_data:
            - investment_thesis: Dict[str, Any] (tùy chọn, tự động nạp từ CSDL nếu thiếu)
            - ticker: str (bắt buộc nếu không có trong thesis)
            - market_data: Dict[str, Any] (tùy chọn, tự động hydrate nếu thiếu)
            - stock_data: Dict[str, Any] (tùy chọn, tự động hydrate nếu thiếu)
            - risk_overrides: Dict[str, float] (tùy chọn)
        """
        thesis = event_data.get("investment_thesis") or {}
        ticker = thesis.get("ticker") or event_data.get("ticker")
        if not ticker:
            raise ValueError("[CounterThesisAgent] Thiếu thông tin mã cổ phiếu (ticker) trong thesis hoặc event_data.")
        ticker = str(ticker).upper().strip()

        from app.domain.repositories.intelligence_repository import IntelligenceRepository
        intel_repo = IntelligenceRepository()

        # 0. Auto-hydration cho Investment Thesis & Khóa Ngoại Toàn vẹn
        if not thesis or not thesis.get("thesis_id"):
            latest_thesis = intel_repo.get_latest_investment_thesis(ticker)
            if latest_thesis:
                thesis = latest_thesis
            else:
                error_msg = f"Không tìm thấy Investment Thesis hợp lệ trong CSDL cho {ticker} để phản biện."
                logger.warning(f"[CounterThesisAgent] {error_msg}")
                return {
                    "status": "INELIGIBLE_NO_THESIS",
                    "error": error_msg,
                    "ticker": ticker,
                    "data": {
                        "ticker": ticker,
                        "thesis_id": None,
                        "verdict": "BLOCK",
                        "cts_score": 100.0,
                        "block_reasons": [error_msg],
                        "rationale": "Không thể tiến hành Devil's Advocate cho một luận điểm không tồn tại trong CSDL.",
                    }
                }

        thesis_id = thesis.get("thesis_id")
        market_data = event_data.get("market_data") or {}
        stock_data = event_data.get("stock_data") or {}
        risk_overrides = event_data.get("risk_overrides") or {}

        # 0.1. Auto-hydration cho Market Data (Regime, Breadth, CSAD)
        if not market_data or "current_regime" not in market_data:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                regime_info = m_repo.get_latest_market_regime()
                if regime_info:
                    market_data = {
                        "current_regime": regime_info.get("regime_label", "BULL_MARKET"),
                        "breadth_above_ma50_pct": float(regime_info.get("breadth_ma50", 0.5) * 100.0),
                        "foreign_net_flow": float(regime_info.get("net_foreign_flow_bil", 0.0) * 1e9),
                        **market_data,
                    }
            except Exception as e:
                logger.warning(f"Lỗi hydrate market_data cho {ticker}: {e}")

        # 0.2. Auto-hydration cho Stock Data (Giá thị trường, Định giá P/E & P/B, Volume, MA20)
        current_price = float(stock_data.get("current_price", 0.0))
        if current_price <= 0:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                px = m_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=True)
                if px and px > 0:
                    stock_data["current_price"] = float(px)
                    current_price = float(px)
            except Exception as e:
                logger.warning(f"Lỗi hydrate giá thị trường cho {ticker}: {e}")

        if "pe_ratio" not in stock_data or "pb_ratio" not in stock_data:
            try:
                from app.domain.repositories.financial_repository import FinancialRepository
                f_repo = FinancialRepository()
                ratios = f_repo.get_latest_ratios(ticker)
                if ratios:
                    stock_data.setdefault("pe_ratio", float(ratios.get("pe", 99.0)))
                    stock_data.setdefault("pb_ratio", float(ratios.get("pb", 99.0)))
            except Exception as e:
                logger.warning(f"Lỗi hydrate chỉ số định giá cho {ticker}: {e}")

        if "volume" not in stock_data or "vol_ma20" not in stock_data:
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                ohlcv_list = m_repo.get_ohlcv(ticker, limit=20)
                if ohlcv_list:
                    stock_data.setdefault("volume", float(ohlcv_list[0].get("volume", 0.0)))
                    vols = [float(x.get("volume", 0.0)) for x in ohlcv_list]
                    stock_data.setdefault("vol_ma20", sum(vols) / len(vols) if vols else 1.0)
            except Exception as e:
                logger.warning(f"Lỗi hydrate volume/vol_ma20 cho {ticker}: {e}")

        # 1. Truy vấn Dữ liệu Sở hữu chéo & Đồ thị GIL từ SAG Connector
        gil_flag = "PASS"
        ocr_score = 0.0
        cycles_detected = 0
        gil_error = None

        if "gil_info" in event_data or "gil_output" in event_data:
            gil_info = event_data.get("gil_info") or event_data.get("gil_output", {})
            gil_flag = str(gil_info.get("gil_flag", "PASS")).upper()
            ocr_score = float(gil_info.get("ocr_score", 0.0))
            cycles_detected = int(gil_info.get("cycles_detected", 0))
        elif "gil_status" in risk_overrides:
            gil_flag = str(risk_overrides["gil_status"]).upper()
            ocr_score = float(risk_overrides.get("ocr_score", 0.0))
        elif "gil_status" in market_data:
            gil_flag = str(market_data["gil_status"]).upper()
            ocr_score = float(market_data.get("ocr_score", 0.0))
        else:
            try:
                gil_info = await sag_connector.get_gil_relationships(ticker)
                status = str(gil_info.get("status", "")).upper()
                flag = str(gil_info.get("gil_flag", "PASS")).upper()
                if status == "FALLBACK" or flag == "DATA_ERROR":
                    gil_flag = "DATA_ERROR"
                    logger.warning(f"[CounterThesisAgent] Nhận trạng thái GIL FALLBACK/DATA_ERROR từ SAG cho {ticker}.")
                else:
                    gil_flag = flag
                    ocr_score = float(gil_info.get("ocr_score", 0.0))
                    cycles_detected = int(gil_info.get("cycles_detected", 0))
            except Exception as e:
                logger.warning(f"Lỗi truy vấn GIL từ SAG cho {ticker}: {e}")
                gil_flag = "DATA_ERROR"
                gil_error = str(e)

        # 2. Truy vấn Beneish M-Score & Phải thu từ BeneishEngine
        beneish_risk = 20.0
        receivable_spike = 20.0
        try:
            m_res = beneish_engine.calculate_m_score(ticker, date.today())
            m_score = m_res.get("m_score")
            if m_score is not None:
                if m_score > -1.78:
                    beneish_risk = 100.0  # Vùng rủi ro cao
                elif m_score >= -2.22:
                    beneish_risk = 50.0   # Vùng cảnh báo
                else:
                    beneish_risk = 10.0   # Vùng an toàn
                
                # Check DSRI (Days Sales in Receivables Index)
                dsri = m_res.get("variables", {}).get("dsri", 1.0)
                if dsri > 1.30:
                    receivable_spike = 80.0
                elif dsri > 1.15:
                    receivable_spike = 50.0
                else:
                    receivable_spike = 15.0
        except Exception as e:
            logger.warning(f"Lỗi tính toán M-Score cho {ticker}: {e}")
            beneish_risk = 40.0

        # 3. Chuẩn hóa Risk Features cho Base CTS (Đã loại bỏ Margin Tension)
        risk_features = {
            "gil_risk": 100.0 if gil_flag in ["CATASTROPHIC", "DATA_ERROR"] else (60.0 if gil_flag == "WARNING" else ocr_score),
            "gil_status": gil_flag,
            "beneish_risk": float(risk_overrides.get("beneish_risk", beneish_risk)),
            "receivable_spike": float(risk_overrides.get("receivable_spike", receivable_spike)),
            "graph_rpt_risk": float(risk_overrides.get("graph_rpt_risk", 75.0 if cycles_detected > 0 else 20.0)),
            "macro_headwind": float(risk_overrides.get("macro_headwind", 40.0 if "BEAR" in str(market_data.get("current_regime", "")).upper() else 20.0)),
            "liquidity_stress": float(risk_overrides.get("liquidity_stress", 60.0 if float(market_data.get("breadth_above_ma50_pct", 50.0)) < 30.0 else 25.0)),
            "missing_data": float(risk_overrides.get("missing_data", 80.0 if gil_flag == "DATA_ERROR" else 15.0)),
        }

        # 4. Chạy toàn bộ quy trình phản biện qua CounterThesisEngine
        report = await self.counter_thesis_engine.evaluate_counter_thesis(
            ticker=ticker,
            thesis_payload=thesis,
            risk_features=risk_features,
            market_data=market_data,
            stock_data=stock_data,
        )

        verdict_str = report.verdict.value if hasattr(report.verdict, "value") else str(report.verdict)

        verdict_output = {
            "thesis_id": report.thesis_id,
            "ticker": report.ticker,
            "base_cts": report.base_cts,
            "interaction_multiplier": report.interaction_multiplier,
            "regime_multiplier": report.regime_multiplier,
            "cts_score": report.final_cts,
            "verdict": verdict_str,
            "rule_of_three_passed": report.rule_of_three_passed,
            "is_capitulation_rebound": report.is_capitulation_rebound,
            "block_reasons": report.block_reasons,
            "holes": report.holes,
            "execution_constraints": report.execution_constraints,
            "rationale": report.rationale,
        }

        # 5. Lưu phán quyết vào CSDL qua IntelligenceRepository
        try:
            intel_repo.save_counter_thesis_verdict(verdict_output)
            
            # 5.1 Cập nhật trạng thái của Investment Thesis theo phán quyết Devil's Advocate
            new_thesis_status = "APPROVED_ACTIVE" if verdict_str == "PROCEED" else ("CONDITIONAL_APPROVED" if verdict_str == "CONDITIONAL" else "REJECTED")
            if thesis_id:
                intel_repo.update_thesis_status(thesis_id, new_thesis_status)
        except Exception as e:
            logger.warning(f"Không thể lưu counter_thesis_verdict cho {thesis_id}: {e}")

        # 6. Bắn sự kiện EventTopics.COUNTER_VERDICT lên RabbitMQ Event Bus
        try:
            from app.core.event_topics import EventTopics
            await self.publish_event(
                topic=EventTopics.COUNTER_VERDICT,
                payload={
                    "thesis_id": report.thesis_id,
                    "ticker": report.ticker,
                    "verdict": verdict_str,
                    "cts_score": report.final_cts,
                    "rule_of_three_passed": report.rule_of_three_passed,
                    "is_capitulation_rebound": report.is_capitulation_rebound,
                    "execution_constraints": report.execution_constraints,
                    "block_reasons": report.block_reasons,
                },
            )
        except Exception as e:
            logger.warning(f"Không thể phát sự kiện EventTopics.COUNTER_VERDICT cho {thesis_id}: {e}")

        trace = {
            "counter_thesis_engine": self.counter_thesis_engine.__class__.__name__,
            "gil_status": gil_flag,
            "base_cts": report.base_cts,
            "interaction_multiplier": report.interaction_multiplier,
            "regime_multiplier": report.regime_multiplier,
            "final_cts": report.final_cts,
            "verdict": verdict_str,
        }

        return {"data": verdict_output, "trace": trace}


counter_thesis_agent = CounterThesisAgent()
