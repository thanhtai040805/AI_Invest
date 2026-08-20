"""HMM Regime Classifier — TASK-301

Phân loại trạng thái thị trường dựa trên Hidden Markov Model (HMM).
Hỗ trợ 4 states: Bull Trending, Bull Choppy, Bear Trending, Bear Bounce.
Cơ chế Hysteresis: Transition threshold 15%, consecutive 3 sessions.
"""

import logging
import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import List, Dict, Any, Optional, Tuple
from enum import Enum
import pickle
from pathlib import Path
import numpy as np
import pandas as pd

# Thư mục lưu model (ai-engine/data/models)
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
MODEL_DIR = BASE_DIR / "data" / "models"
MODEL_DIR.mkdir(parents=True, exist_ok=True)
HMM_MODEL_PATH = MODEL_DIR / "hmm_regime_model.pkl"

logger = logging.getLogger(__name__)

class MarketRegime(Enum):
    BULL_TRENDING = "Bull Trending"
    BULL_CHOPPY = "Bull Choppy"
    BEAR_TRENDING = "Bear Trending"
    BEAR_BOUNCE = "Bear Bounce"

class HMMRegimeClassifier:
    def __init__(self):
        # States mapping
        self.states = [
            MarketRegime.BULL_TRENDING,
            MarketRegime.BULL_CHOPPY,
            MarketRegime.BEAR_TRENDING,
            MarketRegime.BEAR_BOUNCE
        ]
        
        # Hysteresis settings (IOS v5.1 DEC-11)
        self.threshold = 0.15  # 15% diff
        self.consecutive_required = 3
        
        # State tracking for Hysteresis
        self.last_stable_state: Optional[MarketRegime] = None
        self.pending_state: Optional[MarketRegime] = None
        self.pending_count = 0

    def calculate_posterior(self, vni_vs_ma50: float, breadth_20d: float, vol_trend: float) -> Dict[MarketRegime, float]:
        """Tính xác suất hậu nghiệm (Posterior) cho mỗi trạng thái.
        
        Sử dụng mô hình GMM-HMM đã được huấn luyện, fallback về heuristic nếu không tìm thấy mô hình.
        Input:
            vni_vs_ma50: (VNI / MA50) - 1
            breadth_20d: % stocks > MA50 (0-100)
            vol_trend: (Volume / SMA20_Volume) - 1
        """
        # Nếu đã có model, sử dụng hmmlearn
        if HMM_MODEL_PATH.exists():
            try:
                with open(HMM_MODEL_PATH, "rb") as f:
                    model_data = pickle.load(f)
                
                model = model_data["model"]
                state_mapping = model_data["state_mapping"] # map từ hidden_state index sang MarketRegime
                scaler = model_data.get("scaler")
                
                # Predict proba
                obs = np.array([[vni_vs_ma50, breadth_20d, vol_trend]])
                if scaler is not None:
                    obs_scaled = scaler.transform(obs)
                else:
                    obs_scaled = obs
                posteriors = model.predict_proba(obs_scaled)[0]
                
                probs = {}
                for i, state_prob in enumerate(posteriors):
                    regime = state_mapping[i]
                    probs[regime] = probs.get(regime, 0.0) + state_prob
                    
                # Normalize just in case
                total = sum(probs.values())
                if total > 0:
                    return {k: v / total for k, v in probs.items()}
            except Exception as e:
                logger.warning(f"Failed to use hmmlearn model, falling back to heuristics: {e}")
                
        # -------------------------------------
        # FALLBACK: Heuristics
        # -------------------------------------
        probs = {}
        
        # Bull Trending: Price up, Breadth high, Volume confirmation
        s1 = 0.0
        if vni_vs_ma50 > 0.02: s1 += 0.4
        if breadth_20d > 60: s1 += 0.4
        if vol_trend > 0: s1 += 0.2
        probs[MarketRegime.BULL_TRENDING] = s1
        
        # Bull Choppy: Price up but weak, Breadth low, Volume weak
        s2 = 0.0
        if 0 <= vni_vs_ma50 <= 0.02: s2 += 0.4
        if breadth_20d < 50: s2 += 0.4
        if vol_trend <= 0: s2 += 0.2
        probs[MarketRegime.BULL_CHOPPY] = s2
        
        # Bear Trending: Price down, Breadth low, Volume high (capitulation)
        s3 = 0.0
        if vni_vs_ma50 < -0.02: s3 += 0.4
        if breadth_20d < 30: s3 += 0.4
        if vol_trend > 0: s3 += 0.2
        probs[MarketRegime.BEAR_TRENDING] = s3
        
        # Bear Bounce: Price down but recovering, Breadth improving, Volume weak
        s4 = 0.0
        if -0.02 <= vni_vs_ma50 < 0: s4 += 0.4
        if breadth_20d > 40: s4 += 0.4
        if vol_trend < 0: s4 += 0.2
        probs[MarketRegime.BEAR_BOUNCE] = s4
        
        # Normalize to sum to 1
        total = sum(probs.values())
        if total == 0:
            return {s: 0.25 for s in self.states}
        return {s: v / total for s, v in probs.items()}

    def train_hmm_model(self, days_history: int = 1500) -> bool:
        """Huấn luyện mô hình GaussianHMM từ lịch sử và ánh xạ states."""
        try:
            import hmmlearn.hmm as hmm
            import psycopg2
            from app.infrastructure.database.pg_pool import DB_URL
            
            # 1. Fetch data
            conn = psycopg2.connect(DB_URL)
            query = """
                WITH vni AS (
                    SELECT date, close_adj, volume_total,
                           AVG(close_adj) OVER(ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
                           AVG(volume_total) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as vol_ma20
                    FROM market_data_daily
                    WHERE ticker = 'VNINDEX'
                ),
                br AS (
                    SELECT date, breadth_ma50 FROM market_regime
                )
                SELECT vni.date, vni.close_adj, vni.ma50, vni.volume_total, vni.vol_ma20,
                       COALESCE(br.breadth_ma50, 50.0) as breadth
                FROM vni
                LEFT JOIN br ON vni.date = br.date
                ORDER BY vni.date DESC LIMIT %s
            """
            df = pd.read_sql(query, conn, params=(days_history,))
            conn.close()
            
            if len(df) < 200:
                logger.error(f"Not enough data to train HMM: {len(df)} rows")
                return False
                
            df = df.sort_values('date').dropna()
            
            # 2. Prepare observations
            vni_vs_ma50 = (df['close_adj'] / df['ma50'] - 1).values
            breadth = df['breadth'].values
            vol_trend = (df['volume_total'] / df['vol_ma20'] - 1).values
            
            X = np.column_stack([vni_vs_ma50, breadth, vol_trend])
            
            # Scale features to prevent higher variance features (like breadth) from dominating HMM
            from sklearn.preprocessing import StandardScaler
            scaler = StandardScaler()
            X_scaled = scaler.fit_transform(X)
            
            # 3. Fit Model
            model = hmm.GaussianHMM(n_components=4, covariance_type="diag", n_iter=1000, random_state=42)
            model.fit(X_scaled)
            
            # 4. Map hidden states to MarketRegime
            # Tính mean của vni_vs_ma50 cho mỗi state
            state_means = model.means_[:, 0] # cột 0 là vni_vs_ma50
            breadth_means = model.means_[:, 1] # cột 1 là breadth
            
            # Sắp xếp states theo vni_vs_ma50 giảm dần
            sorted_idx = np.argsort(state_means)[::-1]
            
            state_mapping = {}
            # State mạnh nhất -> Bull Trending
            state_mapping[sorted_idx[0]] = MarketRegime.BULL_TRENDING
            # State yếu nhất -> Bear Trending
            state_mapping[sorted_idx[3]] = MarketRegime.BEAR_TRENDING
            
            # 2 states giữa: state nào breadth cao hơn -> Bull Choppy, thấp hơn -> Bear Bounce
            mid1, mid2 = sorted_idx[1], sorted_idx[2]
            if breadth_means[mid1] > breadth_means[mid2]:
                state_mapping[mid1] = MarketRegime.BULL_CHOPPY
                state_mapping[mid2] = MarketRegime.BEAR_BOUNCE
            else:
                state_mapping[mid2] = MarketRegime.BULL_CHOPPY
                state_mapping[mid1] = MarketRegime.BEAR_BOUNCE
                
            # 5. Save model
            with open(HMM_MODEL_PATH, "wb") as f:
                pickle.dump({
                    "model": model,
                    "scaler": scaler,
                    "state_mapping": state_mapping,
                    "trained_at": date.today().isoformat(),
                    "samples": len(X)
                }, f)
                
            logger.info(f"HMM trained successfully on {len(X)} samples. Model saved to {HMM_MODEL_PATH}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to train HMM: {e}")
            return False

    def classify(self, posterior: Dict[MarketRegime, float]) -> MarketRegime:
        """Phân loại Regime áp dụng Hysteresis logic."""
        # Tìm state có xác suất cao nhất hiện tại
        current_best = max(posterior, key=posterior.get)
        current_prob = posterior[current_best]
        
        if self.last_stable_state is None:
            self.last_stable_state = current_best
            return current_best
            
        last_prob = posterior.get(self.last_stable_state, 0.0)
        
        # IOS DEC-11: Transition occurs iff P(RB|X) - P(RA|X) > 15% for 3 consecutive days
        if current_best != self.last_stable_state and (current_prob - last_prob) > self.threshold:
            if current_best == self.pending_state:
                self.pending_count += 1
            else:
                self.pending_state = current_best
                self.pending_count = 1
                
            if self.pending_count >= self.consecutive_required:
                logger.info(f"HMM Transition: {self.last_stable_state} -> {current_best}")
                self.last_stable_state = current_best
                self.pending_state = None
                self.pending_count = 0
        else:
            # Reset pending if condition not met
            self.pending_state = None
            self.pending_count = 0
            
        return self.last_stable_state

    def _backfill_vnindex_if_needed(self, cur, conn, target_date: date):
        """Check if VNINDEX has enough data in DB. If not, fetch from VietFin and upsert."""
        try:
            # Check how many rows we have in the last 75 calendar days up to target_date
            start_date = target_date - timedelta(days=75)
            cur.execute("""
                SELECT COUNT(1) FROM market_data_daily
                WHERE ticker = 'VNINDEX' AND date >= %s AND date <= %s
            """, (start_date, target_date))
            count = cur.fetchone()[0]
            
            if count < 40:  # Normally should have ~50 trading days in 75 calendar days
                logger.info(f"VNINDEX data in DB is sparse (only {count} rows in 75 days up to {target_date}). Backfilling from VietFin...")
                fetch_start = target_date - timedelta(days=730)
                from vietfin import vf
                import pandas as pd
                from psycopg2.extras import execute_values
                
                r = vf.index.price.historical(
                    symbol="vnindex",
                    start_date=fetch_start.strftime("%Y-%m-%d"),
                    end_date=target_date.strftime("%Y-%m-%d"),
                    interval="1d",
                    provider="dnse",
                )
                df = r.to_df()
                if df is not None and not df.empty:
                    rows = []
                    for idx, row in df.iterrows():
                        dt = pd.to_datetime(idx).date()
                        rows.append((
                            'VNINDEX',
                            dt,
                            float(row['open']),
                            float(row['high']),
                            float(row['low']),
                            float(row['close']),
                            float(row['close']),
                            int(row['volume']),
                            'vietfin'
                        ))
                    
                    execute_values(
                        cur,
                        """
                        INSERT INTO market_data_daily (
                            ticker, date, open_adj, high_adj, low_adj, close_adj, close_unadj, volume_total, data_source
                        ) VALUES %s
                        ON CONFLICT (ticker, date) DO UPDATE SET
                            open_adj = EXCLUDED.open_adj,
                            high_adj = EXCLUDED.high_adj,
                            low_adj = EXCLUDED.low_adj,
                            close_adj = EXCLUDED.close_adj,
                            close_unadj = EXCLUDED.close_unadj,
                            volume_total = EXCLUDED.volume_total,
                            data_source = EXCLUDED.data_source
                        """,
                        rows
                    )
                    conn.commit()
                    logger.info(f"Successfully backfilled {len(rows)} VNINDEX records up to {target_date}")
        except Exception as e:
            logger.warning(f"Failed to auto-backfill VNINDEX: {e}")

    def get_market_metrics(self, target_date: date) -> Tuple[float, float, float]:
        """Lấy các biến quan sát từ DB."""
        import psycopg2
        from app.infrastructure.database.pg_pool import DB_URL
        
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        
        # Auto-backfill VNINDEX if needed
        self._backfill_vnindex_if_needed(cur, conn, target_date)
        
        # 1. VNI vs MA50
        cur.execute("""
            SELECT close_adj, 
                   AVG(close_adj) OVER(ORDER BY date ROWS BETWEEN 49 PRECEDING AND CURRENT ROW) as ma50,
                   volume_total,
                   AVG(volume_total) OVER(ORDER BY date ROWS BETWEEN 19 PRECEDING AND CURRENT ROW) as vol_ma20
            FROM market_data_daily
            WHERE ticker = 'VNINDEX' AND date <= %s
            ORDER BY date DESC LIMIT 1
        """, (target_date,))
        row = cur.fetchone()
        
        if not row:
            conn.close()
            return 0.0, 50.0, 0.0
            
        close, ma50, vol, vol_ma20 = row
        vni_vs_ma50 = (close / ma50 - 1) if ma50 else 0
        vol_trend = (vol / vol_ma20 - 1) if vol_ma20 else 0
        
        # 2. Breadth (stocks > MA50)
        cur.execute("""
            SELECT breadth_ma50 FROM market_regime WHERE date <= %s ORDER BY date DESC LIMIT 1
        """, (target_date,))
        b_row = cur.fetchone()
        breadth = b_row[0] if b_row else 50.0
        
        conn.close()
        return vni_vs_ma50, breadth, vol_trend

hmm_classifier = HMMRegimeClassifier()

