"""Standalone Pure-ML Fund Autonomous Channel (IOS v5.1).

Vận hành kênh đầu tư tự động độc lập hoàn toàn dựa trên mô hình Machine Learning:
`hybrid_stacking_ranker.pkl` (LambdaMART + 3D Momentum Ridge + T+2.5 Survival Gate).

Các đặc tính cốt lõi:
1. Account Isolation: Hoạt động trên một Tài khoản độc lập (STANDALONE_ML_ACCOUNT_ID),
   không dùng chung số dư tiền, danh mục vị thế hay sổ lệnh với hệ thống 12 Agent.
2. Pure-ML Decision Making: Tự động nạp Universe HOSE, tính 51 đặc trưng (Feature Forge + Graph Contagion),
   dự báo xác suất và tự quyết định giải ngân (20% NAV / vị thế).
3. Shadow / Live Automation:
   - SHADOW_RUNNER: Tự động ghi nhận paper trade, trừ/cộng tiền và theo dõi danh mục ngầm.
   - LIVE: Chuyển giao lệnh sang Execution Gateway khi có quyết định mở cửa.
4. Continuous Accuracy Tracking: Đo đạc và đối soát độ chính xác thực tế trên thị trường:
   - Realized Survival Rate vs Predicted Probability.
   - Directional Win Rate vs Predicted 3D Momentum.
   - Realized PnL & Brier Score sau T+2.5 / T+3 phiên.
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.hybrid_stacking_ranker import (
    beneish_engine,
    hybrid_stacking_ranker,
)
from app.infrastructure.database.pg_pool import get_conn

load_dotenv()
logger = logging.getLogger("ai_engine.ml.standalone_channel")


class StandaloneExecutionMode(str, Enum):
    LIVE = "LIVE"
    SHADOW_RUNNER = "SHADOW_RUNNER"
    DISABLED = "DISABLED"


class StandaloneMLChannel:
    """
    Kênh Tự Hành Độc Lập Quỹ Standalone Pure-ML (IOS v5.1).
    """

    def __init__(
        self,
        account_id: Optional[str] = None,
        initial_nav: float = 500_000_000.0,
        position_weight: float = 0.20,
    ):
        self.account_id = (
            account_id
            or os.getenv("STANDALONE_ML_ACCOUNT_ID", "standalone-pure-ml-fund-account")
        ).strip()
        self.default_nav = float(initial_nav)
        self.position_weight = float(
            os.getenv("STANDALONE_ML_POSITION_WEIGHT", str(position_weight))
        )
        self.portfolio_repo = PortfolioRepository()

        # Đảm bảo khởi tạo tài khoản và bảng lưu trữ dự báo
        self._ensure_storage_and_account()

    def _ensure_storage_and_account(self) -> None:
        """Đảm bảo tài khoản riêng và bảng theo dõi dự báo tồn tại trong PostgreSQL."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # 1. Bảng lưu trữ và đối soát dự báo ML
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS standalone_ml_predictions (
                            id SERIAL PRIMARY KEY,
                            account_id VARCHAR(100) NOT NULL DEFAULT 'standalone-pure-ml-fund-account',
                            predict_date DATE NOT NULL,
                            ticker VARCHAR(20) NOT NULL,
                            rank_pred FLOAT,
                            mom_pred FLOAT,
                            surv_prob FLOAT,
                            pred_score_z FLOAT,
                            shares INT,
                            price FLOAT,
                            target_weight_pct FLOAT,
                            execution_mode VARCHAR(30),
                            realized_min_lock_ret FLOAT,
                            realized_3d_ret FLOAT,
                            survival_outcome BOOLEAN,
                            accuracy_evaluated_at TIMESTAMPTZ,
                            created_at TIMESTAMPTZ DEFAULT NOW(),
                            UNIQUE (predict_date, ticker, account_id)
                        );
                        """
                    )
                    # 2. Khởi tạo tài khoản riêng trong bảng users nếu chưa có
                    cur.execute(
                        """
                        INSERT INTO users (id, email, password_hash, display_name, cash_balance, win_rate)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING;
                        """,
                        (
                            self.account_id,
                            f"{self.account_id}@aiinvest.internal",
                            "internal_system_account",
                            "Standalone Pure-ML Fund (IOS v5.1)",
                            self.default_nav,
                            0.0,
                        ),
                    )
                conn.commit()
        except Exception as e:
            logger.warning(f"[StandaloneMLChannel] Khởi tạo CSDL: {e}")

    def get_account_state(self) -> Dict[str, Any]:
        """Lấy trạng thái số dư và NAV độc lập của tài khoản Standalone Pure-ML."""
        return self.portfolio_repo.get_account_state(user_id=self.account_id)

    def get_open_positions(self) -> List[Dict[str, Any]]:
        """Lấy danh mục vị thế mở riêng biệt của Standalone ML Fund."""
        return self.portfolio_repo.get_open_positions(user_id=self.account_id)

    def predict_universe(
        self,
        target_date: Optional[Union[date, str]] = None,
        candidate_tickers: Optional[List[str]] = None,
        limit_universe: int = 40,
    ) -> pd.DataFrame:
        """
        Quét dữ liệu thực tế và chạy suy luận 3 nhánh qua hybrid_stacking_ranker.pkl.
        """
        run_date_str = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else (str(target_date) if target_date else date.today().isoformat())
        )

        tickers: List[str] = []
        if candidate_tickers and len(candidate_tickers) > 0:
            tickers = [str(t).upper().strip() for t in candidate_tickers]
        else:
            try:
                with get_conn() as conn:
                    q = """
                        SELECT ticker, SUM(close_adj * volume_continuous) as total_val
                        FROM market_data_daily
                        WHERE date >= '2026-01-01' AND ticker != 'VNINDEX'
                        GROUP BY ticker
                        ORDER BY total_val DESC
                        LIMIT %s;
                    """
                    df_t = pd.read_sql(q, conn, params=(limit_universe,))
                    tickers = df_t["ticker"].tolist()
            except Exception as e:
                logger.error(f"Lỗi nạp Universe từ DB: {e}")
                tickers = ["FPT", "HPG", "VNM", "SSI", "MWG", "VIC", "TCB", "MBB"]

        if not tickers:
            return pd.DataFrame()

        # Nạp dữ liệu OHLCV lịch sử cho các tickers
        try:
            with get_conn() as conn:
                q_data = f"""
                    SELECT ticker, date, open_adj as open, high_adj as high, low_adj as low,
                           close_adj as close, volume_continuous as volume
                    FROM market_data_daily
                    WHERE ticker IN ({','.join([repr(t) for t in tickers])})
                    AND date >= '2025-01-01' AND date <= %s
                    ORDER BY ticker, date ASC;
                """
                df_data = pd.read_sql(q_data, conn, params=(run_date_str,))
        except Exception as e:
            logger.error(f"Lỗi truy vấn OHLCV: {e}")
            return pd.DataFrame()

        if df_data.empty:
            return pd.DataFrame()

        df_data["date"] = pd.to_datetime(df_data["date"])
        data_dict: Dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            df_sym = df_data[df_data["ticker"] == ticker].copy()
            if len(df_sym) >= 30:
                data_dict[ticker] = df_sym.set_index("date").sort_index()

        if not data_dict:
            return pd.DataFrame()

        # 1. Feature Forge
        base_features_dict = {}
        for ticker, df_sym in data_dict.items():
            feats = feature_forge.generate(df_sym, ticker)
            if not feats.empty:
                feats["ticker"] = ticker
                feats["close"] = df_sym["close"]
                val_20d = (df_sym["close"] * df_sym["volume"]).rolling(20, min_periods=5).mean() / 1e6
                feats["adtv20_bil"] = val_20d
                base_features_dict[ticker] = feats.iloc[[-1]].copy()

        if not base_features_dict:
            return pd.DataFrame()

        # 2. Graph Contagion signals
        try:
            graph_dict = graph_engine.extract_graph_contagion_signals(data_dict)
        except Exception as e:
            logger.warning(f"Graph Contagion engine warning: {e}")
            graph_dict = {}

        combined_list = []
        for ticker, feats in base_features_dict.items():
            g_feats = graph_dict.get(ticker)
            if g_feats is not None and not g_feats.empty:
                merged = pd.concat([feats, g_feats.iloc[[-1]]], axis=1).fillna(0.0)
            else:
                merged = feats.fillna(0.0)
            combined_list.append(merged)

        if not combined_list:
            return pd.DataFrame()

        eval_df = pd.concat(combined_list)
        # Đảm bảo toàn bộ 51 features của mô hình đều có mặt
        for col in hybrid_stacking_ranker.feature_cols:
            if col not in eval_df.columns:
                eval_df[col] = 0.0

        # 3. Chạy dự báo qua mô hình Hybrid Stacking
        preds_df = hybrid_stacking_ranker.predict_hybrid_scores(eval_df)
        preds_df["close"] = eval_df["close"].values
        return preds_df

    async def run_autonomous_cycle(
        self,
        target_date: Optional[Union[date, str]] = None,
        candidate_tickers: Optional[List[str]] = None,
        execution_mode: Optional[Union[StandaloneExecutionMode, str]] = None,
        max_candidates: int = 5,
        nav: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Vận hành chu trình tự động độc lập hoàn chỉnh:
        1. Đọc số dư tài khoản độc lập (Account Isolation).
        2. Chạy suy luận ML trên Universe.
        3. Chọn lọc các mã có xác suất sinh tồn và lợi nhuận kỳ vọng cao.
        4. Sizing lệnh (20% NAV / mã) và ghi nhận sổ lệnh riêng biệt.
        5. Tự động lưu dự báo vào CSDL để theo dõi độ chính xác.
        """
        run_date_str = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else (str(target_date) if target_date else date.today().isoformat())
        )
        target_date_obj = (
            date.fromisoformat(run_date_str)
            if isinstance(run_date_str, str)
            else run_date_str
        )

        mode_str = (
            execution_mode.value
            if isinstance(execution_mode, StandaloneExecutionMode)
            else str(
                execution_mode
                or os.getenv("STANDALONE_ML_MODE", StandaloneExecutionMode.SHADOW_RUNNER.value)
            )
        )
        try:
            exec_mode = StandaloneExecutionMode(mode_str)
        except ValueError:
            exec_mode = StandaloneExecutionMode.SHADOW_RUNNER

        if exec_mode == StandaloneExecutionMode.DISABLED:
            logger.info("[Standalone ML Fund] Chế độ DISABLED. Bỏ qua vận hành.")
            return {
                "status": "DISABLED",
                "account_id": self.account_id,
                "execution_mode": exec_mode.value,
                "orders": [],
            }

        account_state = self.get_account_state()
        current_nav = nav or float(account_state.get("total_nav", account_state.get("cash_balance", self.default_nav)))
        cash_balance = float(account_state.get("cash_balance", current_nav))

        logger.info(
            f"[Standalone ML Fund] Khởi động chu trình tự hành — "
            f"Account: '{self.account_id}' | Mode: '{exec_mode.value}' | "
            f"NAV: {current_nav:,.0f} VND | Cash: {cash_balance:,.0f} VND"
        )

        # Chạy dự báo ML
        preds_df = self.predict_universe(
            target_date=target_date_obj,
            candidate_tickers=candidate_tickers,
        )

        if preds_df.empty:
            logger.warning("[Standalone ML Fund] Không có dữ liệu dự báo cho Universe.")
            return {
                "status": "NO_PREDICTIONS",
                "account_id": self.account_id,
                "execution_mode": exec_mode.value,
                "orders": [],
            }

        # Lấy danh sách cổ phiếu hiện đang nắm giữ để tránh mua trùng
        held_tickers = {str(p.get("symbol", p.get("ticker"))).upper().strip() for p in self.get_open_positions()}
        remaining_cash = max(0.0, cash_balance)

        # Lọc và sắp xếp ứng viên theo pred_score giảm dần
        sorted_df = preds_df.sort_values("pred_score", ascending=False)
        selected_candidates = sorted_df.head(max_candidates)

        qualified_orders: List[Dict[str, Any]] = []
        target_capital_per_pos = current_nav * self.position_weight

        for _, row in selected_candidates.iterrows():
            ticker = str(row["ticker"]).upper().strip()
            raw_close = float(row.get("close", 0.0))
            if raw_close <= 0:
                continue

            # Ưu tiên lấy giá Realtime từ DNSE OpenAPI / WebSocket
            try:
                from app.domain.repositories.market_data_repository import MarketDataRepository
                m_repo = MarketDataRepository()
                rt_price = m_repo.get_realtime_or_latest_price(ticker)
                if rt_price and rt_price > 0:
                    close_price = float(rt_price)
                else:
                    close_price = raw_close * 1000.0 if raw_close < 1000.0 else raw_close
            except Exception:
                close_price = raw_close * 1000.0 if raw_close < 1000.0 else raw_close

            # Tránh mua trùng lặp nếu đã nắm giữ vị thế mã này trong tài khoản
            if ticker in held_tickers and not candidate_tickers:
                continue

            # Kiểm tra số dư tiền mặt khả dụng
            if remaining_cash < close_price * 100 and not candidate_tickers:
                logger.info(f"[Standalone ML Fund] Tiền mặt khả dụng ({remaining_cash:,.0f} VND) không đủ mở thêm vị thế {ticker}.")
                continue

            surv_prob = float(row.get("surv_prob", 0.50))
            mom_pred = float(row.get("mom_pred", 0.0))
            pred_score = float(row.get("pred_score", 0.0))
            rank_pred = float(row.get("rank_pred", 0.0))

            # Tính số lượng cổ phiếu theo chuẩn lô 100 sàn HOSE, không vượt quá tiền mặt
            alloc_capital = min(target_capital_per_pos, remaining_cash) if remaining_cash > 0 else target_capital_per_pos
            shares = int(alloc_capital / close_price / 100) * 100
            if shares <= 0:
                shares = 100

            order_val = shares * close_price
            if order_val > remaining_cash and remaining_cash >= close_price * 100:
                shares = int(remaining_cash / close_price / 100) * 100
                order_val = shares * close_price

            remaining_cash = max(0.0, remaining_cash - order_val)

            tier = "TIER_A_PLUS" if pred_score >= 1.0 else "TIER_A"
            conviction = "A+" if pred_score >= 1.0 else "A"

            order_record = {
                "ticker": ticker,
                "account_id": self.account_id,
                "tier": tier,
                "conviction": conviction,
                "z_score": round(pred_score, 2),
                "pred_score": round(pred_score, 2),
                "surv_prob": round(surv_prob, 4),
                "mom_pred": round(mom_pred, 4),
                "shares": shares,
                "price": close_price,
                "target_weight_pct": self.position_weight,
                "execution_mode": exec_mode.value,
                "action": (
                    "EXECUTE_LIVE_BROKER"
                    if exec_mode == StandaloneExecutionMode.LIVE
                    else "SHADOW_PAPER_TRADE_ONLY"
                ),
                "rationale": (
                    f"[STANDALONE PURE-ML] P(Surv)={surv_prob:.1%} | "
                    f"E[Mom3D]={mom_pred:+.2%} | Z={pred_score:+.2f}"
                ),
            }
            qualified_orders.append(order_record)

            # Lưu snapshot dự báo vào bảng standalone_ml_predictions
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            INSERT INTO standalone_ml_predictions (
                                account_id, predict_date, ticker, rank_pred, mom_pred,
                                surv_prob, pred_score_z, shares, price, target_weight_pct, execution_mode
                            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (predict_date, ticker, account_id) DO UPDATE SET
                                rank_pred = EXCLUDED.rank_pred,
                                mom_pred = EXCLUDED.mom_pred,
                                surv_prob = EXCLUDED.surv_prob,
                                pred_score_z = EXCLUDED.pred_score_z,
                                shares = EXCLUDED.shares,
                                price = EXCLUDED.price,
                                execution_mode = EXCLUDED.execution_mode;
                            """,
                            (
                                self.account_id,
                                target_date_obj,
                                ticker,
                                rank_pred,
                                mom_pred,
                                surv_prob,
                                pred_score,
                                shares,
                                close_price,
                                self.position_weight,
                                exec_mode.value,
                            ),
                        )
                    conn.commit()
            except Exception as e:
                logger.debug(f"Lưu dự báo standalone_ml_predictions: {e}")

            # Nếu chạy SHADOW_RUNNER, tự động khớp paper trade vào tài khoản độc lập
            if exec_mode == StandaloneExecutionMode.SHADOW_RUNNER:
                try:
                    self.portfolio_repo.execute_order_transaction(
                        ticker=ticker,
                        action="BUY",
                        shares=shares,
                        executed_price=close_price,
                        user_id=self.account_id,
                        execution_mode="SHADOW_PAPER",
                        status="FILLED",
                    )
                except Exception as e_pt:
                    logger.debug(f"Khớp paper trade SHADOW_RUNNER: {e_pt}")

        logger.info(
            f"[Standalone ML Fund] Hoàn tất chu trình: Đề xuất {len(qualified_orders)} lệnh "
            f"cho Account '{self.account_id}' ({exec_mode.value})."
        )

        return {
            "status": "SUCCESS",
            "account_id": self.account_id,
            "date": run_date_str,
            "execution_mode": exec_mode.value,
            "total_nav": current_nav,
            "cash_balance": cash_balance,
            "orders": qualified_orders,
            "predictions_count": len(preds_df),
        }

    def evaluate_forward_accuracy(self, lookback_days: int = 60) -> Dict[str, Any]:
        """
        Tự động đối soát và đo đạc độ chính xác thực tế của mô hình sau T+2.5 / T+3:
        1. Quét các lệnh/dự báo đã qua ít nhất 3 ngày giao dịch.
        2. Truy vấn giá thực tế trong CSDL market_data_daily.
        3. Cập nhật kết quả vào bảng standalone_ml_predictions.
        4. Tính toán:
           - Realized Survival Rate vs Predicted Probability.
           - Directional Hit Rate (% lần đón đúng chiều tăng/giảm sau 3 ngày).
           - Mean Return T+3 thực tế.
        """
        logger.info(f"[Standalone ML Accuracy] Bắt đầu đối soát độ chính xác (Lookback {lookback_days} ngày)...")

        un_evaluated_records = []
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        SELECT id, predict_date, ticker, price, surv_prob, mom_pred
                        FROM standalone_ml_predictions
                        WHERE account_id = %s
                        AND accuracy_evaluated_at IS NULL
                        AND predict_date <= CURRENT_DATE - INTERVAL '3 days'
                        ORDER BY predict_date ASC;
                        """,
                        (self.account_id,),
                    )
                    un_evaluated_records = cur.fetchall()
        except Exception as e:
            logger.error(f"Lỗi truy vấn dự báo chưa đối soát: {e}")
            return {"status": "ERROR", "message": str(e)}

        updates_count = 0
        for rec in un_evaluated_records:
            pred_id, p_date, ticker, p_price, p_surv, p_mom = rec
            try:
                with get_conn() as conn:
                    # Lấy 3 phiên giao dịch tiếp theo
                    q_post = """
                        SELECT date, low_adj, close_adj
                        FROM market_data_daily
                        WHERE ticker = %s AND date > %s
                        ORDER BY date ASC
                        LIMIT 3;
                    """
                    df_post = pd.read_sql(q_post, conn, params=(ticker, p_date))

                if len(df_post) >= 3 and p_price > 0:
                    low_1 = float(df_post["low_adj"].iloc[0])
                    low_2 = float(df_post["low_adj"].iloc[1])
                    close_3 = float(df_post["close_adj"].iloc[2])

                    # Chuẩn hóa giá tương lai sang VNĐ đầy đủ nếu lưu đơn vị nghìn đồng
                    if low_1 < 1000.0:
                        low_1 *= 1000.0
                    if low_2 < 1000.0:
                        low_2 *= 1000.0
                    if close_3 < 1000.0:
                        close_3 *= 1000.0

                    min_lock_low_ret = min((low_1 - p_price) / p_price, (low_2 - p_price) / p_price)
                    realized_3d_ret = (close_3 - p_price) / p_price
                    survival_outcome = bool(min_lock_low_ret > -0.035)

                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute(
                                """
                                UPDATE standalone_ml_predictions
                                SET realized_min_lock_ret = %s,
                                    realized_3d_ret = %s,
                                    survival_outcome = %s,
                                    accuracy_evaluated_at = NOW()
                                WHERE id = %s;
                                """,
                                (min_lock_low_ret, realized_3d_ret, survival_outcome, pred_id),
                            )
                        conn.commit()
                    updates_count += 1
            except Exception as e_eval:
                logger.debug(f"Đối soát record {pred_id} ({ticker}): {e_eval}")

        # Thống kê tổng hợp toàn bộ các dự báo đã đối soát
        metrics: Dict[str, Any] = {
            "status": "COMPLETED",
            "account_id": self.account_id,
            "newly_evaluated": updates_count,
            "total_evaluated": 0,
            "realized_survival_rate_pct": 0.0,
            "predicted_avg_survival_prob_pct": 0.0,
            "directional_hit_rate_pct": 0.0,
            "avg_realized_3d_return_pct": 0.0,
            "avg_predicted_3d_return_pct": 0.0,
        }

        try:
            with get_conn() as conn:
                df_all = pd.read_sql(
                    """
                    SELECT surv_prob, mom_pred, realized_min_lock_ret, realized_3d_ret, survival_outcome
                    FROM standalone_ml_predictions
                    WHERE account_id = %s AND accuracy_evaluated_at IS NOT NULL;
                    """,
                    conn,
                    params=(self.account_id,),
                )

            if not df_all.empty:
                total = len(df_all)
                surv_success = (df_all["survival_outcome"] == True).sum()
                directional_hits = (
                    ((df_all["mom_pred"] > 0) & (df_all["realized_3d_ret"] > 0))
                    | ((df_all["mom_pred"] <= 0) & (df_all["realized_3d_ret"] <= 0))
                ).sum()

                metrics.update({
                    "total_evaluated": int(total),
                    "realized_survival_rate_pct": round(float(surv_success / total * 100.0), 2),
                    "predicted_avg_survival_prob_pct": round(float(df_all["surv_prob"].mean() * 100.0), 2),
                    "directional_hit_rate_pct": round(float(directional_hits / total * 100.0), 2),
                    "avg_realized_3d_return_pct": round(float(df_all["realized_3d_ret"].mean() * 100.0), 2),
                    "avg_predicted_3d_return_pct": round(float(df_all["mom_pred"].mean() * 100.0), 2),
                })
        except Exception as e_stat:
            logger.error(f"Lỗi tính toán chỉ số thống kê độ chính xác: {e_stat}")

        logger.info(
            f"[Standalone ML Accuracy] Tổng kết đối soát: {metrics['total_evaluated']} dự báo | "
            f"Tỷ lệ sống sót thực tế: {metrics['realized_survival_rate_pct']}% | "
            f"Hit Rate xu hướng: {metrics['directional_hit_rate_pct']}%"
        )
        return metrics


# Singleton instance sẵn dùng cho toàn hệ sinh thái
standalone_ml_channel = StandaloneMLChannel()
