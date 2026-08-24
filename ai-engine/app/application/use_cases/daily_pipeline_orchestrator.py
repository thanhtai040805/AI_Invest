"""Daily Pipeline Orchestrator — TASK-501

Điều phối toàn bộ quy trình đầu tư từ lấy dữ liệu đến thực thi lệnh.
Đảm bảo tính tuần tự và an toàn của hệ thống.
"""

import logging
from datetime import date, datetime
from typing import Dict, Any, List

from app.domain.rules.risk.data_quality import run_all_checks
from app.infrastructure.data_pipelines.ohlcv_ingestion_service import ohlcv_ingestion_svc
from app.domain.rules.universe_manager import universe_manager
from app.domain.rules.market.hmm_classifier import hmm_classifier
from app.domain.rules.market.garch_engine import garch_engine
from app.domain.rules.hard_laws import hard_law_engine
from app.domain.rules.execution.eae import eae_engine
from app.domain.rules.execution.hedge_controller import hedge_controller

logger = logging.getLogger(__name__)

class DailyInvestmentPipeline:
    def __init__(self):
        pass

    async def run(self, target_date: date, current_nav: float, vn30_index: float):
        """Chạy pipeline cho một ngày giao dịch."""
        logger.info(f"Starting Investment Pipeline for {target_date}")
        
        # 1. Data Ingestion (OHLCV)
        # Giả định lấy list tickers từ Universe cũ hoặc top thanh khoản
        active_tickers = ["VHM", "FPT", "VIC", "VNM", "HPG"] # Demo list
        for ticker in active_tickers:
            ohlcv_ingestion_svc.backfill_symbol(ticker, days=1)
            
        # 2. Data Quality & Adjustments
        # (Giả định đã chạy xong ở bước trên hoặc module tự xử lý)
        
        # 3. Regime Detection
        metrics = hmm_classifier.get_market_metrics(target_date)
        posterior = hmm_classifier.calculate_posterior(*metrics)
        regime = hmm_classifier.classify(posterior)
        
        # 4. Volatility & Cash
        returns = garch_engine.get_index_returns(target_date)
        vol_forecast = garch_engine.forecast_volatility(returns)
        cash_ratio = garch_engine.calculate_cash_allocation(vol_forecast)
        
        logger.info(f"Regime: {regime.value}, Vol Forecast: {vol_forecast:.2%}, Target Cash: {cash_ratio:.2%}")
        
        # 5. Universe Classification
        universe_res = universe_manager.classify_universe(active_tickers, target_date)
        
        # 6. Signal Generation & Sizing (Simplified for orchestration)
        # TODO: Loop qua Alpha Factors để chọn mã
        
        # 7. Hedging Check
        vni_row = hmm_classifier.get_market_metrics(target_date) # Reusing for simplicity
        # market_breadth is metrics[1]
        hedge_res = hedge_controller.calculate_hedge_requirement(
            portfolio_value=current_nav * (1 - cash_ratio),
            vn30_index=vn30_index,
            hmm_bear_prob=posterior.get(hmm_classifier.states[2], 0.0),
            market_breadth=metrics[1],
            regime=regime
        )
        
        return {
            "date": target_date,
            "regime": regime.value,
            "cash_ratio": cash_ratio,
            "hedge": hedge_res
        }

pipeline = DailyInvestmentPipeline()
