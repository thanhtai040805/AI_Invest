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

    def log_metric(
        self,
        metric_type: str,
        metric_date: date,
        ticker: Optional[str] = None,
        predicted_value: Optional[str] = None,
        realized_value: Optional[str] = None,
        numeric_value: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Ghi nhận một bản ghi đo lường sai lệch vào bảng mral_metrics."""
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            meta_json = json.dumps(metadata or {}, ensure_ascii=False, default=str)
            cur.execute("""
                INSERT INTO mral_metrics (metric_type, metric_date, ticker, predicted_value, realized_value, numeric_value, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """, (
                metric_type,
                metric_date,
                ticker,
                str(predicted_value) if predicted_value is not None else None,
                str(realized_value) if realized_value is not None else None,
                float(numeric_value) if numeric_value is not None else None,
                meta_json
            ))
            conn.commit()
            cur.close()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"Failed to log MRAL metric [{metric_type}] for ticker {ticker}: {e}")
            return False

    def log_metrics_batch(self, records: List[Dict[str, Any]]) -> int:
        """Ghi nhận nhiều bản ghi theo lô (Bulk Insert) tối ưu vào mral_metrics."""
        if not records:
            return 0
        try:
            conn = psycopg2.connect(DB_URL)
            cur = conn.cursor()
            values = [
                (
                    r.get("metric_type", "GENERAL"),
                    r.get("metric_date", date.today()),
                    r.get("ticker"),
                    str(r["predicted_value"]) if r.get("predicted_value") is not None else None,
                    str(r["realized_value"]) if r.get("realized_value") is not None else None,
                    float(r["numeric_value"]) if r.get("numeric_value") is not None else None,
                    json.dumps(r.get("metadata", {}), ensure_ascii=False, default=str),
                )
                for r in records
            ]
            from psycopg2.extras import execute_values
            execute_values(
                cur,
                """
                INSERT INTO mral_metrics (metric_type, metric_date, ticker, predicted_value, realized_value, numeric_value, metadata)
                VALUES %s
                """,
                values
            )
            conn.commit()
            cur.close()
            conn.close()
            return len(records)
        except Exception as e:
            logger.error(f"Failed to batch log {len(records)} MRAL metrics: {e}")
            return 0

    def check_and_trigger_retrain(self, factor_name: str) -> bool:
        """Kiểm tra rolling IC và phát sinh đề xuất Retrain (Proposal) cho Governance Agent thẩm định."""
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
                logger.warning(
                    f"MRAL Trigger: Rolling 20-day IC for {factor_name} is {rolling_ic:.4f} (< {THRESHOLD}). "
                    f"Submitting Retrain Proposal to Governance Agent instead of auto-overwriting production!"
                )
                # Thay vì tự đè vào production, ghi nhận Model Proposal có kiểm định vào mral_metrics
                self.log_metric(
                    metric_type="RETRAIN_PROPOSAL_GENERATED",
                    metric_date=date.today(),
                    ticker=factor_name,
                    predicted_value=f"ROLLING_IC:{rolling_ic:.4f}",
                    realized_value="THRESHOLD_BREACHED",
                    numeric_value=rolling_ic,
                    metadata={
                        "target_model": "panel_xgboost",
                        "status": "PENDING_GOVERNANCE_APPROVAL",
                        "threshold": THRESHOLD,
                        "action": "REQUIRE_OOS_WALK_FORWARD_BEFORE_DEPLOY"
                    }
                )
                return True
        except Exception as e:
            logger.error(f"Failed to check or execute proposal for retrain: {e}")
            
        return False

mral_engine = MRALEngine()
