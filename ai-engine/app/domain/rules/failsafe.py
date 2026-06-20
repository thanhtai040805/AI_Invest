"""Failsafe & Heartbeat System — TASK-112

Giám sát kết nối với Broker và kích hoạt trạng thái dừng an toàn khi có sự cố.
Hỗ trợ:
1. Heartbeat monitoring (30s interval).
2. Latency monitoring (> 1500ms detection).
3. Failsafe activation (Auto-cancel, Block new orders).
"""

import logging
import time
import threading
from enum import Enum
from typing import Optional, List, Callable

logger = logging.getLogger(__name__)

class FailsafeStatus(Enum):
    INACTIVE = "Hệ thống bình thường"
    ACTIVE = "FAILSAFE ACTIVE: Dừng giao dịch"

class FailsafeEngine:
    def __init__(
        self, 
        heartbeat_interval: float = 30.0,
        latency_threshold_ms: float = 1500.0,
        missed_heartbeats_limit: int = 3
    ):
        self.status = FailsafeStatus.INACTIVE
        self.heartbeat_interval = heartbeat_interval
        self.latency_threshold_ms = latency_threshold_ms
        self.missed_heartbeats_limit = missed_heartbeats_limit
        
        self.last_heartbeat_at: float = time.time()
        self.missed_heartbeats: int = 0
        self.last_latency_ms: float = 0.0
        self.latency_violation_start: Optional[float] = None
        
        self._lock = threading.Lock()
        self._on_activate_callbacks: List[Callable] = []

    def register_activation_callback(self, callback: Callable):
        """Đăng ký hàm xử lý khi Failsafe được kích hoạt (ví dụ: hủy lệnh)."""
        self._on_activate_callbacks.append(callback)

    def record_heartbeat(self, latency_ms: float = 0.0):
        """Ghi nhận một heartbeat thành công từ Broker."""
        with self._lock:
            self.last_heartbeat_at = time.time()
            self.missed_heartbeats = 0
            self.last_latency_ms = latency_ms
            
            # Kiểm tra latency
            if latency_ms > self.latency_threshold_ms:
                if self.latency_violation_start is None:
                    self.latency_violation_start = time.time()
                
                # Nếu latency cao kéo dài > 5s
                if time.time() - self.latency_violation_start > 5.0:
                    self._activate(f"Latency cao ({latency_ms}ms) kéo dài > 5s")
            else:
                self.latency_violation_start = None

    def check_health(self):
        """Kiểm tra sức khỏe hệ thống dựa trên thời gian trôi qua."""
        with self._lock:
            if self.status == FailsafeStatus.ACTIVE:
                return

            elapsed = time.time() - self.last_heartbeat_at
            
            # Nếu quá thời gian heartbeat
            if elapsed > self.heartbeat_interval:
                self.missed_heartbeats = int(elapsed // self.heartbeat_interval)
                
                if self.missed_heartbeats >= self.missed_heartbeats_limit:
                    self._activate(f"Mất kết nối: {self.missed_heartbeats} heartbeats nhỡ")

    def _activate(self, reason: str):
        """Kích hoạt trạng thái Failsafe."""
        if self.status == FailsafeStatus.ACTIVE:
            return
            
        self.status = FailsafeStatus.ACTIVE
        logger.critical(f"!!! FAILSAFE ACTIVATED: {reason} !!!")
        
        # Thực thi các hành động khẩn cấp (hủy lệnh, thông báo)
        for cb in self._on_activate_callbacks:
            try:
                cb(reason)
            except Exception as e:
                logger.error(f"Error in failsafe callback: {e}")

    def reset(self):
        """Reset trạng thái về bình thường (cần can thiệp thủ công hoặc kết nối ổn định lại)."""
        with self._lock:
            self.status = FailsafeStatus.INACTIVE
            self.last_heartbeat_at = time.time()
            self.missed_heartbeats = 0
            self.latency_violation_start = None
            logger.info("Failsafe status reset to INACTIVE")

failsafe_engine = FailsafeEngine()
