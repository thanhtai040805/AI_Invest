"""Standalone Pure-ML Fund Autonomous Channel (IOS v5.1).

Vận hành kênh đầu tư tự động độc lập hoàn toàn dựa trên mô hình Machine Learning:
`hybrid_stacking_ranker.pkl` (LambdaMART + 3D Momentum Ridge + T+2.5 Survival Gate).

Các đặc tính cốt lõi:
1. Account Isolation: Hoạt động trên một Tài khoản độc lập (STANDALONE_ML_ACCOUNT_ID),
   không dùng chung số dư tiền, danh mục vị thế hay sổ lệnh với hệ thống 12 Agent.
2. Pure-ML Decision Making: Tự động nạp Universe HOSE, tính 51 đặc trưng (Feature Forge + Graph Contagion),
   dự báo xác suất và tự quyết định giải ngân (20% NAV / vị thế).
3. Shadow / Live Automation:
   - SHADOW_RUNNER: Chỉ ghi paper trade khi sổ lệnh thật còn mới và đủ độ sâu.
   - LIVE: Không khả dụng; chưa có cổng đặt lệnh môi giới.
4. Continuous Accuracy Tracking: Đo đạc và đối soát độ chính xác thực tế trên thị trường:
   - Realized Survival Rate vs Predicted Probability.
   - Directional Win Rate vs Predicted 3D Momentum.
   - Reference-price return after 3 observed sessions, separate from executed fund PnL.
"""

from __future__ import annotations

import logging
import math
import os
import uuid
from datetime import date, datetime, timedelta
from enum import Enum
from zoneinfo import ZoneInfo
from typing import Any, Dict, List, Optional, Union

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from app.domain.repositories.portfolio_repository import PortfolioRepository
from app.domain.services.ml.feature_forge import feature_forge
from app.domain.services.ml.graph_contagion_engine import graph_engine
from app.domain.services.ml.dual_tier_sniper_engine import dual_tier_engine
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
    REPLAY = "REPLAY"


class StandaloneMLChannel:
    """
    Kênh Tự Hành Độc Lập Quỹ Standalone Pure-ML (IOS v5.1).
    """

    def __init__(
        self,
        account_id: Optional[str] = None,
        initial_nav: float = 500_000_000.0,
        position_weight: float = 0.20,
        model=None,
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
        self.model = model or hybrid_stacking_ranker
        self._peak_prices: Dict[str, float] = {}

    def _ensure_storage_and_account(self) -> None:
        """Create the dedicated user only; table schema is managed by Prisma."""
        with get_conn() as conn:
            with conn.cursor() as cur:
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
                cur.execute(
                    "INSERT INTO portfolio_account (account_id, cash_balance, total_nav, peak_nav, drawdown_tier, updated_at) "
                    "SELECT id, cash_balance, cash_balance, cash_balance, 'GREEN', CURRENT_TIMESTAMP FROM users WHERE id = %s "
                    "ON CONFLICT (account_id) DO NOTHING",
                    (self.account_id,),
                )

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
        limit_universe: int = 100,
    ) -> pd.DataFrame:
        """
        Quét dữ liệu thực tế và chạy suy luận 3 nhánh qua hybrid_stacking_ranker.pkl.
        """
        run_date_str = (
            target_date.isoformat()
            if isinstance(target_date, date)
            else (str(target_date) if target_date else datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat())
        )

        tickers: List[str] = []
        if candidate_tickers and len(candidate_tickers) > 0:
            tickers = [str(t).upper().strip() for t in candidate_tickers]
        else:
            try:
                with get_conn() as conn:
                    q = """
                        SELECT ticker, SUM(close * volume_continuous) as total_val
                        FROM market_data_daily_calculation
                        WHERE date >= %s::date - INTERVAL '1 year' AND date < %s::date AND ticker != 'VNINDEX'
                        GROUP BY ticker
                        ORDER BY total_val DESC
                        LIMIT %s;
                    """
                    df_t = pd.read_sql(q, conn, params=(run_date_str, run_date_str, limit_universe))
                    tickers = df_t["ticker"].tolist()
            except Exception as e:
                logger.error(f"Lỗi nạp Universe từ DB: {e}")
                tickers = ["FPT", "HPG", "VNM", "SSI", "MWG", "VIC", "TCB", "MBB"]

        if not tickers:
            return pd.DataFrame()

        # Lớp 0 Forensic Gate: Loại bỏ các mã có dấu hiệu gian lận BCTC (Beneish M-Score > -1.78)
        try:
            df_beneish = beneish_engine.fetch_and_compute_scores(tickers)
            if not df_beneish.empty:
                df_beneish_filtered = df_beneish[df_beneish["effective_date"] <= pd.Timestamp(run_date_str)]
                if not df_beneish_filtered.empty:
                    latest_beneish = df_beneish_filtered.sort_values("effective_date").groupby("ticker").last()
                    manipulators = set(latest_beneish[latest_beneish["is_manipulator"] == 1].index.str.upper())
                    tickers = [t for t in tickers if t.upper() not in manipulators]
        except Exception as e_ben:
            logger.warning(f"Lỗi kiểm tra Beneish Lớp 0: {e_ben}")

        # Nạp dữ liệu OHLCV lịch sử cho các tickers
        try:
            with get_conn() as conn:
                q_data = f"""
                    SELECT ticker, date, open, high, low, close, volume_continuous as volume
                    FROM market_data_daily_calculation
                    WHERE ticker IN ({','.join([repr(t) for t in tickers])})
                    AND date >= %s::date - INTERVAL '3 years' AND date < %s::date
                    ORDER BY ticker, date ASC;
                """
                df_data = pd.read_sql(q_data, conn, params=(run_date_str, run_date_str))
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
        for col in self.model.feature_cols:
            if col not in eval_df.columns:
                eval_df[col] = 0.0

        # 3. Chạy dự báo qua mô hình Hybrid Stacking
        preds_df = self.model.predict_hybrid_scores(eval_df)
        preds_df["close"] = eval_df["close"].values
        preds_df["feature_date"] = eval_df.index.date
        return preds_df

    async def run_autonomous_cycle(
        self,
        target_date: Optional[Union[date, str]] = None,
        candidate_tickers: Optional[List[str]] = None,
        execution_mode: Optional[Union[StandaloneExecutionMode, str]] = None,
        max_candidates: int = 5,
        nav: Optional[float] = None,
        replay_prices: Optional[Dict[str, float]] = None,
        buy_block_reason: Optional[str] = None,
        min_z_threshold: Optional[float] = None,
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
            else (str(target_date) if target_date else datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date().isoformat())
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

        if exec_mode == StandaloneExecutionMode.LIVE:
            raise RuntimeError("LIVE execution is unavailable: no broker order gateway is implemented")

        if exec_mode == StandaloneExecutionMode.DISABLED:
            logger.info("[Standalone ML Fund] Chế độ DISABLED. Bỏ qua vận hành.")
            return {
                "status": "DISABLED",
                "account_id": self.account_id,
                "execution_mode": exec_mode.value,
                "orders": [],
            }

        today = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).date()
        is_replay = exec_mode == StandaloneExecutionMode.REPLAY
        if is_replay:
            trained_through = getattr(self.model, "trained_through", None)
            if target_date_obj >= today or replay_prices is None:
                raise ValueError("ML replay requires a historical session and historical prices")
            if not trained_through or date.fromisoformat(trained_through) >= target_date_obj:
                raise ValueError("Replay model must be trained strictly before the replay session")
        elif target_date_obj != today or replay_prices is not None:
            raise ValueError("Live ML queue is today-only; historical prices require REPLAY mode")
        if not math.isfinite(self.position_weight) or not 0 < self.position_weight <= 1:
            raise ValueError("ML position weight must be in (0, 1]")
        if not is_replay:
            self._ensure_storage_and_account()
        account_state = self.portfolio_repo.get_account_state(
            user_id=self.account_id, as_of=target_date_obj - timedelta(days=1) if is_replay else None
        )
        current_nav = float(nav if nav is not None else account_state["total_nav"])
        if not math.isfinite(current_nav) or current_nav < 0:
            raise ValueError("Invalid ML NAV")
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

        # Persist every signal and its decision together with the pending order.
        # Locking the user serializes daily decisions against fills and other cycles.
        qualified_orders: List[Dict[str, Any]] = []
        sorted_df = preds_df.sort_values("pred_score", ascending=False).copy()
        effective_z_threshold = (
            min_z_threshold
            if min_z_threshold is not None
            else (
                float(os.getenv("STANDALONE_MIN_Z_THRESHOLD"))
                if os.getenv("STANDALONE_MIN_Z_THRESHOLD")
                else None
            )
        )
        if "z_score" not in sorted_df.columns:
            mean_score = sorted_df["pred_score"].mean()
            std_score = sorted_df["pred_score"].std()
            if std_score == 0 or np.isnan(std_score):
                std_score = 1.0
            sorted_df["z_score"] = (sorted_df["pred_score"] - mean_score) / std_score

        prices = dict(replay_prices or {})
        if not is_replay:
            from app.domain.repositories.market_data_repository import MarketDataRepository
            market_repo = MarketDataRepository()
            for _, row in sorted_df.head(max_candidates).iterrows():
                ticker = str(row["ticker"]).upper().strip()
                quote = market_repo.get_realtime_or_latest_price(ticker, allow_eod_fallback=False, socket_only=True)
                if quote and math.isfinite(float(quote)) and quote > 0:
                    prices[ticker] = float(quote)
        pending_status = "PENDING_REPLAY" if is_replay else "PENDING_SHADOW"
        order_type = "REPLAY_ML_LIMIT" if is_replay else "SHADOW_ML_LIMIT"

        with get_conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT cash_balance FROM users WHERE id = %s FOR UPDATE", (self.account_id,))
            cash_row = cur.fetchone()
            if not cash_row or cash_row[0] is None:
                raise LookupError("ML account cash balance is unavailable")
            cur.execute("SELECT symbol FROM positions WHERE user_id = %s AND quantity > 0", (self.account_id,))
            held_tickers = {str(row[0]).upper() for row in cur.fetchall()}
            cur.execute("SELECT symbol, quantity, price FROM orders WHERE user_id = %s AND side = 'BUY' AND status IN ('PENDING_SHADOW', 'PENDING_REPLAY')", (self.account_id,))
            pending = cur.fetchall()
            pending_tickers = {str(row[0]).upper() for row in pending}
            reserved = sum(float(row[1]) * float(row[2]) + max(float(row[1]) * float(row[2]) * 0.001, 10000.0) for row in pending)
            remaining_cash = max(0.0, float(cash_row[0]) - reserved)
            allowed_tickers = {str(t).upper().strip() for t in candidate_tickers} if candidate_tickers else None

            for index, (_, row) in enumerate(sorted_df.iterrows()):
                ticker = str(row["ticker"]).upper().strip()
                raw_close = float(row.get("close", 0.0))
                reference_price = raw_close * 1000.0 if raw_close < 1000.0 else raw_close
                limit_price = prices.get(ticker, reference_price)
                scores = [float(row.get(key, float("nan"))) for key in ("rank_pred", "mom_pred", "surv_prob", "pred_score")]
                rank_pred, mom_pred, surv_prob, pred_score = scores
                z_score = float(row.get("z_score", pred_score))
                feature_date = row["feature_date"]
                if isinstance(feature_date, datetime):
                    feature_date = feature_date.date()
                elif isinstance(feature_date, str):
                    feature_date = date.fromisoformat(feature_date)
                if not isinstance(feature_date, date) or feature_date >= target_date_obj:
                    raise ValueError("ML features must precede the decision session")

                # Dynamic sizing chuẩn EXP-016: Tier A+ (12% NAV), Tier A (5% NAV)
                if z_score >= 3.80:
                    target_weight = 0.12
                    tier = "TIER_A_PLUS"
                    conviction = "A+"
                elif z_score >= 2.85:
                    target_weight = 0.05
                    tier = "TIER_A"
                    conviction = "A"
                else:
                    target_weight = self.position_weight
                    tier = "TIER_B"
                    conviction = "B"

                shares = 0
                reason = "SELECTED"
                if not all(math.isfinite(n) for n in [reference_price, limit_price, *scores]) or reference_price <= 0 or limit_price <= 0 or not 0 <= surv_prob <= 1:
                    reason = "INVALID_PREDICTION"
                elif is_replay and ticker not in prices:
                    reason = "NO_HISTORICAL_PRICE"
                elif buy_block_reason:
                    reason = buy_block_reason
                elif allowed_tickers is not None and ticker not in allowed_tickers:
                    reason = "OUTSIDE_SNIPER_GATE"
                elif effective_z_threshold is not None and z_score < effective_z_threshold:
                    reason = "NO_SNIPER_SETUP"
                elif index >= max_candidates:
                    reason = "OUTSIDE_TOP_K"
                elif ticker in held_tickers:
                    reason = "ALREADY_HELD"
                elif ticker in pending_tickers:
                    reason = "ORDER_ALREADY_PENDING"
                else:
                    capital = min(current_nav * target_weight, remaining_cash)
                    # Include the same brokerage fee used by PortfolioRepository.
                    budget = min(capital / 1.001, max(0.0, capital - 10000.0))
                    shares = int(budget / limit_price / 100) * 100
                    if shares <= 0:
                        reason = "INSUFFICIENT_BUDGET"
                decision = "BUY" if shares > 0 else "SKIP"
                order_id = str(uuid.uuid4()) if shares > 0 else None

                cur.execute("""
                    INSERT INTO standalone_ml_predictions (
                      account_id, predict_date, feature_date, ticker, rank_pred, mom_pred,
                      surv_prob, pred_score_z, shares, price, target_weight_pct, execution_mode,
                      decision, decision_reason, order_id, model_version
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (predict_date, ticker, account_id) DO NOTHING RETURNING id
                """, (self.account_id, target_date_obj, feature_date, ticker,
                      rank_pred if math.isfinite(rank_pred) else None,
                      mom_pred if math.isfinite(mom_pred) else None,
                      surv_prob if math.isfinite(surv_prob) else None,
                      z_score if math.isfinite(z_score) else (pred_score if math.isfinite(pred_score) else None),
                      shares,
                      reference_price if math.isfinite(reference_price) and reference_price > 0 else None,
                      target_weight, exec_mode.value, decision, reason, order_id, getattr(self.model, "model_version", None)))
                if not cur.fetchone():
                    continue  # Daily snapshot is immutable; never requeue or invalidate its accuracy.
                if not order_id:
                    continue
                created_at = datetime.combine(target_date_obj, datetime.min.time()).replace(hour=9, minute=45) if is_replay else datetime.now(ZoneInfo("Asia/Ho_Chi_Minh")).replace(tzinfo=None)
                cur.execute("""INSERT INTO orders (id, user_id, symbol, side, order_type, price, quantity, status, created_at)
                    VALUES (%s, %s, %s, 'BUY', %s, %s, %s, %s, %s)
                """, (order_id, self.account_id, ticker, order_type, limit_price, shares, pending_status, created_at))
                remaining_cash -= shares * limit_price + max(shares * limit_price * 0.001, 10000.0)
                pending_tickers.add(ticker)
                qualified_orders.append({
                    "ticker": ticker, "account_id": self.account_id, "order_id": order_id,
                    "shares": shares, "price": limit_price, "target_weight_pct": target_weight,
                    "rank_pred": rank_pred, "pred_score": pred_score, "z_score": z_score,
                    "tier": tier,
                    "conviction": conviction,
                    "surv_prob": surv_prob, "mom_pred": mom_pred, "execution_mode": exec_mode.value,
                    "execution_status": pending_status, "action": "SHADOW_PAPER_TRADE_ONLY",
                    "rationale": f"[STANDALONE PURE-ML] P(Surv)={surv_prob:.1%} | E[Mom3D]={mom_pred:+.2%} | Z={z_score:+.2f}sigma | {tier} ({target_weight:.0%})",
                })

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

    async def monitor_positions(self) -> Dict[str, Any]:
        """Protect only ML holdings; sell orders still wait for executable live depth."""
        if os.getenv("STANDALONE_ML_MODE", "SHADOW_RUNNER") != "SHADOW_RUNNER":
            return {"monitored": 0, "queued": 0}
        from app.domain.rules.stop_loss import StopLossEngine
        from app.domain.rules.execution.shadow_fill import shadow_fill
        from app.infrastructure.external_api.market_data_service import market_data_svc
        from app.domain.repositories.portfolio_repository import calculate_is_t25_locked

        account = self.get_account_state()
        positions = self.get_open_positions()
        now = datetime.now(ZoneInfo("Asia/Ho_Chi_Minh"))
        risk = StopLossEngine()
        queued = 0
        for position in positions:
            ticker = str(position["ticker"]).upper()
            quantity = int(position["shares"])
            if quantity < 100 or calculate_is_t25_locked(position.get("opened_at"), now):
                continue
            try:
                book = await market_data_svc.get_order_book(ticker)
                current_price = shadow_fill(book, "SELL", 100, 1.0, now=now)
            except ValueError:
                continue  # Stale/empty depth cannot produce an artificial protective fill.
            entry = float(position["average_price"])
            self._peak_prices[ticker] = max(self._peak_prices.get(ticker, entry), current_price)
            days_held = (now.date() - position["opened_at"].date()).days if position.get("opened_at") else 0
            gain_from_entry = (current_price - entry) / entry
            peak_gain = (self._peak_prices[ticker] - entry) / entry

            sell_quantity = 0
            # Rule 1: Breakeven Shield (+2.5% -> +0.2%)
            if peak_gain >= 0.025 and gain_from_entry <= 0.002:
                sell_quantity = quantity
            # Rule 2: Hard Stop (-3.5%)
            elif gain_from_entry <= -0.035:
                sell_quantity = quantity
            # Rule 3: Take Profit (+6.0%)
            elif gain_from_entry >= 0.060:
                sell_quantity = quantity
            # Rule 4: Time Stop (5 days)
            elif days_held >= 5:
                sell_quantity = quantity
            else:
                stop = risk.check_position(
                    ticker, quantity, entry, current_price,
                    float(account["total_nav"]), available_shares=quantity,
                    market_data={"peak_price": self._peak_prices[ticker], "days_held": days_held},
                )
                if stop and stop.quantity > 0:
                    sell_quantity = stop.quantity

            if sell_quantity <= 0:
                continue
            with get_conn() as conn, conn.cursor() as cur:
                cur.execute("SELECT id FROM users WHERE id = %s FOR UPDATE", (self.account_id,))
                if not cur.fetchone():
                    raise LookupError("ML account is unavailable")
                cur.execute("SELECT id FROM orders WHERE user_id = %s AND symbol = %s AND side = 'SELL' AND status = 'PENDING_SHADOW'", (self.account_id, ticker))
                if cur.fetchone():
                    continue
                cur.execute("INSERT INTO orders (id,user_id,symbol,side,order_type,price,quantity,status,created_at) VALUES (%s,%s,%s,'SELL','SHADOW_ML_LIMIT',%s,%s,'PENDING_SHADOW',%s)",
                    (str(uuid.uuid4()), self.account_id, ticker, current_price * 0.985, sell_quantity, now.replace(tzinfo=None)))
                queued += 1
        return {"monitored": len(positions), "queued": queued}

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
                        SELECT id, COALESCE(feature_date, predict_date), ticker, price, surv_prob, mom_pred
                        FROM standalone_ml_predictions
                        WHERE account_id = %s
                        AND accuracy_evaluated_at IS NULL
                        AND COALESCE(feature_date, predict_date) < (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
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
                        SELECT date, low, close
                        FROM market_data_daily_calculation
                        WHERE ticker = %s AND date > %s AND date <= (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date
                        ORDER BY date ASC
                        LIMIT 3;
                    """
                    df_post = pd.read_sql(q_post, conn, params=(ticker, p_date))

                if len(df_post) >= 3 and p_price > 0:
                    low_1 = float(df_post["low"].iloc[0])
                    low_2 = float(df_post["low"].iloc[1])
                    close_3 = float(df_post["close"].iloc[2])

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
                    WHERE account_id = %s AND accuracy_evaluated_at IS NOT NULL
                      AND predict_date BETWEEN (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date - (%s - 1) AND (now() AT TIME ZONE 'Asia/Ho_Chi_Minh')::date;
                    """,
                    conn,
                    params=(self.account_id, lookback_days),
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
