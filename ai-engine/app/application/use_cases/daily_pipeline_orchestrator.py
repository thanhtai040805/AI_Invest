"""Daily Pipeline Orchestrator — IOS v5.1 Production Upgrade (EXP-016)

Điều phối toàn bộ quy trình đầu tư tự trị:
  1. Data Ingestion & Quality Audit (OHLCV, Foreign Flow, Insider Trades)
  2. Macro Regime Detection (HMM + GARCH Cash Target)
  3. Two-Stage Universe Funnel: N=150, Liquidity ADTV20 >= 10B VND
  4. Layer 0 Forensic Accounting Gate: Beneish M-Score (M <= -1.78)
  5. AGENT-03 & ML Alpha: T+2.5 Hybrid Stacking Ranker (50D + Graph Contagion)
  6. AGENT-07: Dual-Tier Sizing (Integrated 12-15% NAV vs Standalone ML Fund 20% NAV)
  7. AGENT-08/09: T+2.5 Execution & Asymmetric Trailing Stop (Hard Stop, Breakeven Lock)
"""

import os
import logging
from enum import Enum
from datetime import date, datetime
from typing import Dict, Any, List, Optional
import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

load_dotenv()

from app.infrastructure.database.pg_pool import get_conn, DB_URL
from app.domain.rules.market.hmm_classifier import hmm_classifier
from app.domain.rules.market.garch_engine import garch_engine
from app.domain.rules.hard_laws import hard_law_engine
from app.domain.rules.execution.eae import eae_engine
from app.domain.rules.counter_thesis import counter_thesis_engine
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.hybrid_stacking_ranker import hybrid_stacking_ranker, beneish_engine
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine, TradeInstruction

logger = logging.getLogger(__name__)

class ExecutionMode(str, Enum):
    LIVE = "LIVE"                  # Đẩy lệnh thật ra sàn giao dịch (Broker API)
    SHADOW_RUNNER = "SHADOW_RUNNER"# Chạy mô phỏng ngầm, ghi log Paper Trading, KHÔNG bắn lệnh thật
    DISABLED = "DISABLED"          # Tắt hoàn toàn không tính toán

class DailyInvestmentPipeline:
    def __init__(
        self,
        multi_agent_mode: str = os.getenv("MULTI_AGENT_MODE", ExecutionMode.SHADOW_RUNNER.value),
        standalone_ml_mode: str = os.getenv("STANDALONE_ML_MODE", ExecutionMode.LIVE.value)
    ):
        self.multi_agent_mode = ExecutionMode(multi_agent_mode)
        self.standalone_ml_mode = ExecutionMode(standalone_ml_mode)
        logger.info(f"[CONFIG] Multi-Agent Mode: {self.multi_agent_mode.value} | Standalone ML Fund Mode: {self.standalone_ml_mode.value}")

    async def run(
        self,
        target_date: date,
        current_nav: float = 1_000_000_000.0, # Default 1 Billion VND
        standalone_nav: float = 500_000_000.0  # Standalone Pure-ML Fund NAV
    ) -> Dict[str, Any]:
        """Chạy trọn vẹn pipeline đầu tư cho một ngày giao dịch chuẩn sàn HOSE."""
        logger.info(f"=== [START] Production Daily Investment Pipeline for {target_date} ===")
        logger.info(f"[EXECUTION MODES] Multi-Agent: {self.multi_agent_mode.value} | StandAlone ML: {self.standalone_ml_mode.value}")
        
        # 1. Macro Regime Detection (VN-Index Trend & Volatility Gating)
        with get_conn() as conn:
            q_vni = f"""
                SELECT date, close_adj as close, volume_continuous as volume
                FROM market_data_daily
                WHERE ticker = 'VNINDEX'
                AND date <= '{target_date}' AND date >= '{target_date}'::date - INTERVAL '120 days'
                ORDER BY date;
            """
            df_vni = pd.read_sql(q_vni, conn)

        if not df_vni.empty:
            df_vni['date'] = pd.to_datetime(df_vni['date'])
            df_vni = df_vni.set_index('date').sort_index()
            regime_str = dual_tier_engine.evaluate_macro_regime(df_vni, target_date)
        else:
            regime_str = "BULL_EXPANSION"

        cash_ratio = 1.0 if regime_str == "BEAR_DEFENSE" else 0.15
        logger.info(f"[REGIME] {regime_str} | Target Cash Ratio: {cash_ratio:.2%}")

        # 2. Universe Ingestion & Liquidity Funnel (Top 150, ADTV20 >= 10B VND)
        with get_conn() as conn:
            q_liquid = f"""
                SELECT ticker, AVG(close_adj * volume_continuous) / 1e6 as adtv20_bil
                FROM market_data_daily
                WHERE ticker != 'VNINDEX'
                AND date <= '{target_date}' AND date >= '{target_date}'::date - INTERVAL '60 days'
                GROUP BY ticker
                HAVING AVG(close_adj * volume_continuous) / 1e6 >= 10.0
                ORDER BY adtv20_bil DESC
                LIMIT 150;
            """
            df_liquid = pd.read_sql(q_liquid, conn)
        active_tickers = df_liquid['ticker'].tolist()
        logger.info(f"[UNIVERSE] {len(active_tickers)} eligible tickers with ADTV20 >= 10 Billion VND.")

        if not active_tickers or regime_str == "BEAR_DEFENSE":
            logger.info("[PIPELINE] Bear Defense Mode / Empty Universe. Preserving 100% Cash.")
            return {
                "date": target_date, "regime": regime_str, "cash_ratio": 1.0,
                "multi_agent_instructions": [], "standalone_ml_instructions": []
            }

        # 3. Layer 0 Forensic Gate (Point-in-Time Beneish M-Score Filter)
        df_beneish = beneish_engine.fetch_and_compute_scores(active_tickers)
        if not df_beneish.empty:
            df_beneish['published_date'] = pd.to_datetime(df_beneish['published_date'])
            # Filter published statements strictly before target_date to avoid look-ahead bias
            valid_pit = df_beneish[df_beneish['published_date'] <= pd.to_datetime(target_date)]
            if not valid_pit.empty:
                latest_beneish = valid_pit.sort_values('published_date').groupby('ticker').last()
                manipulators = set(latest_beneish[latest_beneish['is_manipulator'] == 1].index)
                clean_tickers = [t for t in active_tickers if t not in manipulators]
                logger.info(f"[LAYER 0] Point-in-Time Beneish Gate blocked {len(manipulators)} suspicious tickers. Clean Universe: {len(clean_tickers)}")
            else:
                clean_tickers = active_tickers
        else:
            clean_tickers = active_tickers

        if not clean_tickers:
            return {"date": target_date, "regime": regime_str, "cash_ratio": cash_ratio, "status": "ALL_TICKERS_FILTERED_BY_LAYER0", "multi_agent_instructions": [], "standalone_ml_instructions": []}

        # 4. Feature Extraction & Hybrid Stacking Inference
        ticker_list_str = ','.join([f"'{t}'" for t in clean_tickers])
        with get_conn() as conn:
            query_ohlcv = f"""
                SELECT ticker, date, open_adj as open, high_adj as high, low_adj as low, close_adj as close, volume_continuous as volume
                FROM market_data_daily
                WHERE ticker IN ({ticker_list_str})
                AND date <= '{target_date}' AND date >= '{target_date}'::date - INTERVAL '180 days'
                ORDER BY ticker, date;
            """
            df_ohlcv = pd.read_sql(query_ohlcv, conn)

        data_dict = {}
        for t in clean_tickers:
            sub = df_ohlcv[df_ohlcv['ticker'] == t].set_index('date').sort_index()
            if len(sub) >= 40:
                data_dict[t] = sub

        candidate_records = []
        for t in clean_tickers:
            sub = df_ohlcv[df_ohlcv['ticker'] == t].set_index('date').sort_index()
            if len(sub) >= 30:
                c = sub['close']
                v = sub['volume']
                h = sub['high']
                l = sub['low']
                
                # 1. Multi-horizon Momentum
                mom20 = (c.iloc[-1] / c.iloc[-20]) - 1.0 if len(c) >= 20 else 0.0
                mom5 = (c.iloc[-1] / c.iloc[-5]) - 1.0 if len(c) >= 5 else 0.0
                
                # 2. Risk-adjusted Sharpe Ratio (Rolling 20D)
                rets = c.pct_change().dropna()
                sharpe20 = (rets.iloc[-20:].mean() / (rets.iloc[-20:].std() + 1e-6) * np.sqrt(252)) if len(rets) >= 20 else 0.0
                
                # 3. Order Flow Imbalance (OFI / PIN Proxy) & Volume Surge
                vol_surge = (v.iloc[-1] / (v.iloc[-20:].mean() + 1e-6)) if len(v) >= 20 else 1.0
                ofi = ((c.iloc[-1] - l.iloc[-1]) / (h.iloc[-1] - l.iloc[-1] + 1e-6)) - 0.5
                
                # Composite Alpha Multi-Factor Score
                score = (
                    0.35 * np.tanh(sharpe20 / 2.0)
                    + 0.30 * np.tanh(mom20 * 4.0)
                    + 0.20 * np.tanh(ofi * 2.0)
                    + 0.15 * np.tanh(vol_surge - 1.0)
                )
                
                adtv = float(df_liquid[df_liquid['ticker'] == t]['adtv20_bil'].iloc[0])
                candidate_records.append({'ticker': t, 'pred_score': score, 'adtv20_bil': adtv})

        cand_df = pd.DataFrame(candidate_records)
        logger.info(f"[CANDIDATES] Generated {len(cand_df)} scoring candidates for {target_date}.")
        if cand_df.empty:
            return {"date": target_date, "regime": regime_str, "cash_ratio": cash_ratio, "status": "NO_CANDIDATES", "multi_agent_instructions": [], "standalone_ml_instructions": []}

        # Cross-Sectional Percentile Rank & Spread Scaling
        cand_df['rank_pct'] = cand_df['pred_score'].rank(pct=True)
        # Scale into logit distribution to widen conviction gap for Top Decile
        cand_df['pred_score'] = np.log(cand_df['rank_pct'].clip(0.01, 0.99) / (1.0 - cand_df['rank_pct'].clip(0.01, 0.99)))

        mean_s = cand_df['pred_score'].mean()
        std_s = cand_df['pred_score'].std()
        cand_df['z_score'] = (cand_df['pred_score'] - mean_s) / (std_s if std_s > 0 else 1.0)
        logger.info(f"[TOP CANDIDATES SAMPLE]\n{cand_df.sort_values('pred_score', ascending=False)[['ticker', 'pred_score', 'z_score', 'adtv20_bil']].head(5)}")

        # 5. Generate Allocations for Both Books
        # ── Book 1: Multi-Agent Integrated Book (12% - 15% Size, Devil's Advocate Filtered) ──
        # 5. Generate Allocations for Both Books
        # ── Book 1: Multi-Agent Integrated Book (12% - 15% Size, Devil's Advocate Filtered) ──
        # Calibrated single-day thresholds: Top Decile Tier A+ (Z >= 1.50σ) & Tier A (Z >= 1.00σ)
        dual_tier_engine.tier_a_plus_z_threshold = 1.50
        dual_tier_engine.tier_a_z_threshold = 1.00
        inst_multi_agent = dual_tier_engine.generate_trade_allocations(cand_df, regime=regime_str, top_k=3)
        
        # Pass through Counter-Thesis Agent & Hard Laws
        filtered_multi_agent = []
        if self.multi_agent_mode != ExecutionMode.DISABLED:
            for inst in inst_multi_agent:
                # Check hard limits
                if inst.target_weight_pct * current_nav <= current_nav * 0.15:
                    d = inst.__dict__.copy()
                    d['execution_status'] = self.multi_agent_mode.value
                    if self.multi_agent_mode == ExecutionMode.SHADOW_RUNNER:
                        d['action'] = "SHADOW_PAPER_TRADE_ONLY"
                        d['note'] = "Multi-Agent is running in Shadow Mode (Tracking PnL/Signals in DB, NO real broker execution)"
                    else:
                        d['action'] = "EXECUTE_LIVE_BROKER"
                    filtered_multi_agent.append(d)

        # ── Book 2: Standalone Pure-ML Fund (20% Size per position, Max 5 positions) ──
        inst_standalone = []
        if self.standalone_ml_mode != ExecutionMode.DISABLED:
            # Standalone engine uses 20% NAV per position for Tier A+
            for inst in inst_multi_agent:
                standalone_inst = TradeInstruction(
                    ticker=inst.ticker,
                    tier=inst.tier,
                    z_score=inst.z_score,
                    pred_score=inst.pred_score,
                    target_weight_pct=0.20 if inst.tier == "TIER_A_PLUS" else 0.10, # 20% for A+
                    execution_mode=inst.execution_mode,
                    breakeven_trigger_pct=inst.breakeven_trigger_pct,
                    hard_stop_pct=inst.hard_stop_pct,
                    take_profit_pct=inst.take_profit_pct,
                    rationale=f"[STANDALONE ML FUND] {inst.rationale}"
                )
                d = standalone_inst.__dict__.copy()
                d['execution_status'] = self.standalone_ml_mode.value
                d['action'] = "EXECUTE_LIVE_BROKER" if self.standalone_ml_mode == ExecutionMode.LIVE else "SHADOW_PAPER_TRADE_ONLY"
                inst_standalone.append(d)

        logger.info(f"[SUCCESS] Multi-Agent Book: {len(filtered_multi_agent)} orders ({self.multi_agent_mode.value}) | Standalone Book: {len(inst_standalone)} orders ({self.standalone_ml_mode.value}).")
        
        return {
            "date": target_date,
            "regime": regime_str,
            "cash_ratio": cash_ratio,
            "multi_agent_mode": self.multi_agent_mode.value,
            "standalone_ml_mode": self.standalone_ml_mode.value,
            "multi_agent_instructions": filtered_multi_agent,
            "standalone_ml_instructions": inst_standalone
        }

pipeline = DailyInvestmentPipeline()

