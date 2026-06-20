"""Model Reality Alignment Layer (MRAL) — TASK-502

Theo dõi sự sai lệch giữa dự báo và thực tế.
Lưu log phục vụ việc Retrain HMM và GARCH.
"""

import logging
from datetime import date
from typing import Dict, Any, List, Optional
import json
import psycopg2

from app.infrastructure.database.pg_pool import DB_URL

logger = logging.getLogger(__name__)

class MRALEngine:
    def __init__(self):
        try:
            self.init_db()
        except Exception as e:
            logger.error(f"MRAL database initialization failed: {e}")

    def init_db(self):
        """Tạo bảng mral_metrics nếu chưa có."""
        conn = psycopg2.connect(DB_URL)
        cur = conn.cursor()
        try:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS mral_metrics (
                    id SERIAL PRIMARY KEY,
                    metric_type VARCHAR(50) NOT NULL,
                    metric_date DATE NOT NULL,
                    ticker VARCHAR(20),
                    predicted_value VARCHAR(100),
                    realized_value VARCHAR(100),
                    numeric_value FLOAT,
                    metadata JSONB DEFAULT '{}',
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            """)
            # Create indexes for queries
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mral_metric_type ON mral_metrics(metric_type)")
            cur.execute("CREATE INDEX IF NOT EXISTS idx_mral_metric_date ON mral_metrics(metric_date)")
            conn.commit()
            logger.info("MRAL persistent database storage initialized successfully.")
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cur.close()
            conn.close()

    def log_hmm_accuracy(self, metric_date: date, predicted_regime: str, realized_regime: str):
        """Log accuracy của HMM."""
        is_correct = predicted_regime == realized_regime
        logger.info(f"MRAL HMM Check [{metric_date}]: Predicted={predicted_regime}, Realized={realized_regime}, Correct={is_correct}")
        
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO mral_metrics (metric_type, metric_date, predicted_value, realized_value, numeric_value)
                VALUES (%s, %s, %s, %s, %s)
            """, ('hmm_accuracy', metric_date, str(predicted_regime), str(realized_regime), 1.0 if is_correct else 0.0))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to persist HMM accuracy log: {e}")
        
    def log_execution_slippage(self, ticker: str, target_price: float, filled_price: float, metric_date: Optional[date] = None):
        """Log slippage từ thực thi lệnh."""
        slippage_pct = (filled_price / target_price - 1) if target_price > 0 else 0
        logger.info(f"MRAL Slippage Check [{ticker}]: Target={target_price}, Filled={filled_price}, Slippage={slippage_pct:.4%}")
        
        m_date = metric_date or date.today()
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            meta_json = json.dumps({"target_price": target_price, "filled_price": filled_price})
            cur.execute("""
                INSERT INTO mral_metrics (metric_type, metric_date, ticker, numeric_value, metadata)
                VALUES (%s, %s, %s, %s, %s)
            """, ('slippage', m_date, ticker, float(slippage_pct), meta_json))
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to persist execution slippage log: {e}")

    def log_ic_decay(self, factor_name: str, realized_ic: float, metric_date: Optional[date] = None):
        """Log Information Coefficient (IC) thực tế của các Factors."""
        if realized_ic < 0.05:
            logger.warning(f"MRAL IC Warning [{factor_name}]: Realized IC={realized_ic:.4f} is too low.")
        else:
            logger.info(f"MRAL IC Log [{factor_name}]: Realized IC={realized_ic:.4f}")
            
        m_date = metric_date or date.today()
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO mral_metrics (metric_type, metric_date, ticker, numeric_value)
                VALUES (%s, %s, %s, %s)
            """, ('ic_decay', m_date, factor_name, float(realized_ic)))
            conn.commit()
            cur.close()
            conn.close()
            
            # Check and trigger auto-retrain on IC decay
            self.check_and_trigger_retrain(factor_name)
        except Exception as e:
            logger.error(f"Failed to persist IC decay log: {e}")

    def check_and_trigger_retrain(self, factor_name: str) -> bool:
        """Kiểm tra xem rolling IC có bị giảm mạnh (decay) không và kích hoạt retrain nếu cần."""
        if factor_name != "panel_xgboost":
            return False
            
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            # 1. Fetch recent IC logs
            cur.execute("""
                SELECT numeric_value FROM mral_metrics
                WHERE metric_type = 'ic_decay' AND ticker = %s
                ORDER BY metric_date DESC LIMIT 20
            """, (factor_name,))
            rows = cur.fetchall()
            cur.close()
            conn.close()
            
            if len(rows) < 10:
                # Không đủ dữ liệu để tính rolling mean
                return False
                
            recent_ics = [r[0] for r in rows]
            rolling_ic = sum(recent_ics) / len(recent_ics)
            
            THRESHOLD = 0.02
            if rolling_ic < THRESHOLD:
                logger.warning(f"MRAL Trigger: Rolling 20-day IC for {factor_name} is {rolling_ic:.4f} (under threshold {THRESHOLD}). Triggering auto-retrain!")
                
                # Gọi hàm retrain từ ml_alpha_predictor
                from app.domain.services.ml.ml_alpha_predictor import train_panel_model
                from app.infrastructure.database.pg_pool import DB_URL as PG_DB_URL
                
                # Use stable symbols with long history to avoid embargo split failures
                symbols = [
                    "VCB", "HPG", "VNM", "VIC", "MSN", "BID", "CTG", "FPT",
                    "MBB", "TCB", "ACB", "VIB", "VPB", "HDB", "STB", "SSI",
                    "VHC", "PNJ", "MWG", "GAS", "PLX", "POW", "SAB", "BVH"
                ]
                
                if symbols:
                    res = train_panel_model(symbols, force_retrain=True)
                    logger.info(f"MRAL Auto-retrain completed: {res}")
                    return True
        except Exception as e:
            logger.error(f"Failed to check or execute auto-retrain: {e}")
            
        return False

mral_engine = MRALEngine()
