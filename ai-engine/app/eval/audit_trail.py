"""Audit Trail Engine — TASK-503 (IOS v5.1 Institutional Upgrade)

Ghi bất biến mọi quyết định quan trọng của hệ thống đầu tư tự trị.
Sử dụng SHA-256 Hash Chaining liên tục (Persistent) kết nối trực tiếp PostgreSQL.
Tích hợp bộ kiểm toán mật mã (Full Chain Verifier) phát hiện mọi hành vi sửa đổi / giả mạo dữ liệu.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class AuditTrailEngine:
    """
    Trụ Cột 2 (AUDIT): Sổ cái Mật mã Bất biến (Immutable Cryptographic Ledger).
    """

    GENESIS_BLOCK = "GENESIS_BLOCK_HOSE_IOS_V5"

    def __init__(self, db_url: Optional[str] = None):
        self.db_url = db_url or os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")
        self.last_hash = self._get_latest_hash_from_db()
        logger.info(f"[AuditTrailEngine] Khởi tạo sổ cái thành công. Head Hash: {self.last_hash[:12]}...")

    def _get_latest_hash_from_db(self) -> str:
        """
        Khôi phục mắt xích băm cuối cùng từ CSDL PostgreSQL.
        Đảm bảo tính liên tục của chuỗi băm (Hash Chain Continuity) sau mỗi lần restart hệ thống.
        """
        try:
            from app.infrastructure.database.pg_pool import get_conn
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS audit_logs (
                            id SERIAL PRIMARY KEY,
                            timestamp TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
                            agent_id VARCHAR(64) NOT NULL,
                            event_type VARCHAR(64) NOT NULL,
                            details JSONB,
                            previous_hash VARCHAR(64),
                            current_hash VARCHAR(64)
                        );
                        SELECT current_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
                    """)
                    row = cur.fetchone()
                    if row and row[0]:
                        return str(row[0])
        except Exception as e:
            logger.warning(f"[AuditTrailEngine] Không thể khôi phục head hash từ CSDL: {e}. Sử dụng Genesis Block.")

        return self.GENESIS_BLOCK

    def log_event(self, agent_id: str, event_type: str, details: Dict[str, Any]) -> str:
        """
        Ghi nhận một sự kiện vào Sổ cái Bất biến SHA-256 Hash Chaining.
        Khóa bảng EXCLUSIVE trong giao dịch để đảm bảo tính tuần tự tuyệt đối (Strict Serializability),
        tránh đứt gãy chuỗi băm khi có nhiều agent / luồng ghi nhận đồng thời.
        """
        timestamp = datetime.now().isoformat()
        clean_details = details if isinstance(details, dict) else {"payload": str(details)}

        from psycopg2.extras import Json
        from app.infrastructure.database.pg_pool import get_conn

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    # Khóa bảng để đảm bảo không bị xung đột / phân nhánh chuỗi băm (Forking)
                    cur.execute("""
                        CREATE TABLE IF NOT EXISTS audit_logs (
                            id SERIAL PRIMARY KEY,
                            timestamp VARCHAR(64) NOT NULL,
                            agent_id VARCHAR(64) NOT NULL,
                            event_type VARCHAR(64) NOT NULL,
                            details JSONB,
                            previous_hash VARCHAR(64),
                            current_hash VARCHAR(64)
                        );
                        LOCK TABLE audit_logs IN EXCLUSIVE MODE;
                        SELECT current_hash FROM audit_logs ORDER BY id DESC LIMIT 1;
                    """)
                    row = cur.fetchone()
                    prev_hash = str(row[0]) if (row and row[0]) else self.GENESIS_BLOCK

                    payload = {
                        "timestamp": timestamp,
                        "agent_id": str(agent_id),
                        "event_type": str(event_type),
                        "details": clean_details,
                        "previous_hash": prev_hash,
                    }
                    payload_str = json.dumps(payload, sort_keys=True, default=str)
                    current_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()

                    cur.execute("""
                        INSERT INTO audit_logs (
                            timestamp, agent_id, event_type, details, 
                            previous_hash, current_hash
                        ) VALUES (%s, %s, %s, %s, %s, %s);
                    """, (timestamp, str(agent_id), str(event_type), Json(clean_details), prev_hash, current_hash))

                    self.last_hash = current_hash
                    logger.debug(f"[AuditTrailEngine] Ghi log thành công: {event_type} by {agent_id}, Hash: {current_hash[:8]}")
                    return current_hash
        except Exception as e:
            logger.warning(f"[AuditTrailEngine] Ghi log CSDL gặp lỗi ({e}), chuyển sang fallback bộ nhớ.")
            # Fallback in-memory
            payload = {
                "timestamp": timestamp,
                "agent_id": str(agent_id),
                "event_type": str(event_type),
                "details": clean_details,
                "previous_hash": self.last_hash,
            }
            payload_str = json.dumps(payload, sort_keys=True, default=str)
            current_hash = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
            self.last_hash = current_hash
            return current_hash


    def verify_full_chain(self) -> Tuple[bool, int, Optional[str]]:
        """
        Kiểm toán toàn vẹn toàn bộ chuỗi băm (Full Cryptographic Chain Audit):
        - Kiểm tra tính liên tục của previous_hash -> current_hash.
        - Tái tính toán băm SHA-256 từ nội dung bản ghi để phát hiện dữ liệu bị sửa đổi.
        - Trả về: (is_valid, records_checked, error_message).
        """
        from app.infrastructure.database.pg_pool import get_conn

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        SELECT id, timestamp, agent_id, event_type, details, previous_hash, current_hash
                        FROM audit_logs
                        ORDER BY id ASC;
                    """)
                    records = cur.fetchall()
        except Exception as e:
            return False, 0, f"Lỗi truy vấn CSDL: {e}"

        if not records:
            return True, 0, None

        expected_prev = records[0][5]  # previous_hash của bản ghi đầu tiên

        for r in records:
            rec_id, ts, agent, ev_type, details, prev_h, curr_h = r

            # Kiểm tra mắt xích liên tục
            if prev_h != expected_prev:
                err = f"ĐỨT GÃY CHUỖI BĂM tại ID={rec_id}: previous_hash ({prev_h}) != expected ({expected_prev})."
                logger.critical(f"[AuditTrailEngine] {err}")
                return False, rec_id, err

            # Tái tính toán mã băm SHA-256 của khối
            ts_str = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            clean_details = details if isinstance(details, dict) else (json.loads(details) if isinstance(details, str) else {"payload": str(details)})

            recomputed_payload = {
                "timestamp": ts_str,
                "agent_id": str(agent),
                "event_type": str(ev_type),
                "details": clean_details,
                "previous_hash": prev_h,
            }
            recomputed_hash = hashlib.sha256(
                json.dumps(recomputed_payload, sort_keys=True, default=str).encode("utf-8")
            ).hexdigest()

            if recomputed_hash != curr_h:
                err = f"DỮ LIỆU BỊ GIẢ MẠO tại ID={rec_id}: Recomputed Hash ({recomputed_hash[:8]}) != Recorded Hash ({curr_h[:8]})."
                logger.critical(f"[AuditTrailEngine] {err}")
                return False, rec_id, err

            expected_prev = curr_h

        logger.info(f"[AuditTrailEngine] Toàn vẹn sổ cái được xác thực 100% ({len(records)} bản ghi).")
        return True, len(records), None

    def verify_chain(self) -> bool:
        """Phương thức tương thích ngược trả về boolean."""
        is_valid, _, _ = self.verify_full_chain()
        return is_valid


audit_engine = AuditTrailEngine()
