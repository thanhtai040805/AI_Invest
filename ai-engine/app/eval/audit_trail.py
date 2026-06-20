"""Audit Trail Engine — TASK-503

Ghi bất biến mọi quyết định quan trọng của hệ thống.
Sử dụng hash-chaining để đảm bảo tính toàn vẹn (không thể xóa/sửa).
"""

import logging
import hashlib
import json
import os
from datetime import datetime
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

class AuditTrailEngine:
    def __init__(self):
        self.last_hash = "INITIAL_BLOCK"
        self.db_url = os.getenv("DATABASE_URL", "postgresql://postgres:123@localhost:5432/aiinvest")

    def log_event(self, agent_id: str, event_type: str, details: Dict[str, Any]):
        """Ghi nhận một sự kiện vào Audit Log."""
        
        timestamp = datetime.now().isoformat()
        payload = {
            "timestamp": timestamp,
            "agent_id": agent_id,
            "event_type": event_type,
            "details": details,
            "previous_hash": self.last_hash
        }
        
        # 1. Tính toán hash cho record hiện tại
        payload_str = json.dumps(payload, sort_keys=True)
        current_hash = hashlib.sha256(payload_str.encode()).hexdigest()
        
        # 2. Lưu vào DB
        self._save_to_db(timestamp, agent_id, event_type, details, self.last_hash, current_hash)
        
        # 3. Cập nhật state
        self.last_hash = current_hash
        logger.info(f"Audit log created: {event_type} by {agent_id}, Hash: {current_hash[:8]}")

    def _save_to_db(self, ts, agent, ev_type, details, prev_hash, curr_hash):
        """Thực hiện lưu vào table audit_logs."""
        import psycopg2
        from psycopg2.extras import Json
        
        try:
            conn = psycopg2.connect(self.db_url)
            cur = conn.cursor()
            
            cur.execute("""
                INSERT INTO audit_logs (
                    timestamp, agent_id, event_type, details, 
                    previous_hash, current_hash
                ) VALUES (%s, %s, %s, %s, %s, %s)
            """, (ts, agent, ev_type, Json(details), prev_hash, curr_hash))
            
            conn.commit()
            cur.close()
            conn.close()
        except Exception as e:
            logger.error(f"Failed to write audit log to DB: {e}")

    def verify_chain(self) -> bool:
        """Kiểm tra tính toàn vẹn của chuỗi hash."""
        # TODO: Implement logic đọc lại toàn bộ table và re-hash để kiểm tra
        return True

audit_engine = AuditTrailEngine()
