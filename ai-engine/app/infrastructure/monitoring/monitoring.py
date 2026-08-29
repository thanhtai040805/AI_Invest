"""System monitoring and alerting service.

Tracks:
  - PostgreSQL database connection health & latency
  - DNSE WebSocket stream health & session state
  - Data backfill and ETL job status
  - System resource usage (CPU, RAM, Disk via psutil)
  - System alerts (INFO, WARNING, CRITICAL) with notification dispatch
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

TZ_VN = timezone(timedelta(hours=7))


@dataclass
class Alert:
    timestamp: str
    severity: str  # INFO, WARNING, CRITICAL
    source: str
    message: str
    data: Dict[str, Any] = field(default_factory=dict)


class MonitoringService:
    """Central monitoring service with alert history and notification handlers."""

    def __init__(self, max_alerts: int = 1000):
        self.alerts: List[Alert] = []
        self.max_alerts = max_alerts
        self.alert_handlers: List[Callable[[Alert], None]] = []
        self._start_time = time.time()

    def add_alert_handler(self, handler: Callable[[Alert], None]) -> None:
        """Register a handler for new alerts (e.g., Telegram, Slack, Webhook)."""
        self.alert_handlers.append(handler)

    def _record(self, alert: Alert) -> None:
        self.alerts.append(alert)
        if len(self.alerts) > self.max_alerts:
            self.alerts.pop(0)
        for handler in self.alert_handlers:
            try:
                handler(alert)
            except Exception as e:
                logger.error("Alert handler failed: %s", e)

    def info(self, source: str, message: str, data: Optional[Dict] = None) -> None:
        a = Alert(
            timestamp=datetime.now(TZ_VN).isoformat(),
            severity="INFO",
            source=source,
            message=message,
            data=data or {},
        )
        self._record(a)
        logger.info("[%s] %s", source, message)

    def warning(self, source: str, message: str, data: Optional[Dict] = None) -> None:
        a = Alert(
            timestamp=datetime.now(TZ_VN).isoformat(),
            severity="WARNING",
            source=source,
            message=message,
            data=data or {},
        )
        self._record(a)
        logger.warning("[%s] %s", source, message)

    def critical(self, source: str, message: str, data: Optional[Dict] = None) -> None:
        a = Alert(
            timestamp=datetime.now(TZ_VN).isoformat(),
            severity="CRITICAL",
            source=source,
            message=message,
            data=data or {},
        )
        self._record(a)
        logger.critical("[%s] %s", source, message)

    def get_recent(self, limit: int = 20, severity: Optional[str] = None, source: Optional[str] = None) -> List[Dict]:
        """Get recent alerts, optionally filtered by severity or source."""
        filtered = [
            a for a in self.alerts
            if (severity is None or a.severity == severity.upper())
            and (source is None or a.source.lower() == source.lower())
        ]
        return [
            {
                "timestamp": a.timestamp,
                "severity": a.severity,
                "source": a.source,
                "message": a.message,
                "data": a.data,
            }
            for a in filtered[-limit:]
        ]

    def clear_alerts(self) -> None:
        """Clear recorded alerts."""
        self.alerts.clear()

    def get_status_summary(self) -> Dict[str, Any]:
        """Get overall system health summary."""
        now_vn = datetime.now(TZ_VN)
        critical_24h = sum(
            1 for a in self.alerts
            if a.severity == "CRITICAL"
            and datetime.fromisoformat(a.timestamp) > (now_vn - timedelta(hours=24))
        )
        warnings_24h = sum(
            1 for a in self.alerts
            if a.severity == "WARNING"
            and datetime.fromisoformat(a.timestamp) > (now_vn - timedelta(hours=24))
        )

        uptime_secs = int(time.time() - self._start_time)
        if critical_24h > 0:
            health = "CRITICAL"
        elif warnings_24h > 5:
            health = "WARNING"
        else:
            health = "GOOD"

        return {
            "uptime_seconds": uptime_secs,
            "uptime_human": str(timedelta(seconds=uptime_secs)),
            "total_alerts": len(self.alerts),
            "critical_24h": critical_24h,
            "warnings_24h": warnings_24h,
            "health": health,
        }


monitoring_svc = MonitoringService()


# ---------------------------------------------------------------------------
# Individual Health Check Functions
# ---------------------------------------------------------------------------

def check_database_health() -> Dict[str, Any]:
    """Check PostgreSQL database connection and query latency."""
    t0 = time.perf_counter()
    try:
        from app.infrastructure.database.pg_pool import get_cursor
        with get_cursor() as cur:
            cur.execute("SELECT 1;")
            row = cur.fetchone()
            latency_ms = round((time.perf_counter() - t0) * 1000, 2)
            healthy = row is not None and row[0] == 1
            return {
                "source": "database",
                "healthy": healthy,
                "latency_ms": latency_ms,
                "engine": "PostgreSQL",
            }
    except Exception as e:
        latency_ms = round((time.perf_counter() - t0) * 1000, 2)
        monitoring_svc.critical("database", f"Database health check failed: {e}")
        return {
            "source": "database",
            "healthy": False,
            "latency_ms": latency_ms,
            "error": str(e),
        }


def check_dnse_hub_health() -> Dict[str, Any]:
    """Check DNSE WebSocket stream hub status and connection."""
    try:
        from app.infrastructure.external_api.dnse.stream_hub import get_stream_hub
        hub = get_stream_hub()
        status = hub.status() if hasattr(hub, "status") else {}
        is_connected = status.get("connected", False)

        if not is_connected:
            monitoring_svc.warning(
                "dnse_hub", "DNSE WebSocket stream disconnected",
                data={"status": status},
            )
        return {
            "source": "dnse_hub",
            "connected": is_connected,
            "status": status,
            "healthy": is_connected,
        }
    except Exception as e:
        monitoring_svc.critical("dnse_hub", f"DNSE health check failed: {e}")
        return {"source": "dnse_hub", "connected": False, "healthy": False, "error": str(e)}


def check_job_states() -> Dict[str, Any]:
    """Check background ETL & data pipeline job states."""
    try:
        from app.infrastructure.monitoring.job_state_service import list_jobs
        jobs = list_jobs()
        failed = [j for j in jobs if j.get("status") == "failed"]
        running = [j for j in jobs if j.get("status") == "running"]
        completed = [j for j in jobs if j.get("status") == "completed"]

        if failed:
            monitoring_svc.warning(
                "job_states", f"{len(failed)} background job(s) in failed state",
                data={"failed_jobs": [j.get("job_name") for j in failed]},
            )
        return {
            "source": "job_states",
            "total_jobs": len(jobs),
            "completed": len(completed),
            "running": len(running),
            "failed": len(failed),
            "failed_details": failed[:5],
            "healthy": len(failed) == 0,
        }
    except Exception as e:
        monitoring_svc.critical("job_states", f"Job state check failed: {e}")
        return {"source": "job_states", "healthy": False, "error": str(e)}


def check_system_resources() -> Dict[str, Any]:
    """Check CPU, memory, and disk utilization."""
    try:
        import psutil
        cpu_pct = psutil.cpu_percent(interval=None)
        mem = psutil.virtual_memory()
        disk = psutil.disk_usage(os.path.abspath(os.sep))

        healthy = cpu_pct < 90 and mem.percent < 90
        if not healthy:
            monitoring_svc.warning(
                "system_resources",
                f"High resource utilization: CPU={cpu_pct}%, RAM={mem.percent}%",
                data={"cpu": cpu_pct, "memory_percent": mem.percent},
            )

        return {
            "source": "system_resources",
            "healthy": healthy,
            "cpu_percent": cpu_pct,
            "memory": {
                "total_mb": round(mem.total / (1024 * 1024), 1),
                "available_mb": round(mem.available / (1024 * 1024), 1),
                "used_percent": mem.percent,
            },
            "disk": {
                "total_gb": round(disk.total / (1024 * 1024 * 1024), 1),
                "free_gb": round(disk.free / (1024 * 1024 * 1024), 1),
                "used_percent": disk.percent,
            },
        }
    except Exception as e:
        return {"source": "system_resources", "healthy": True, "note": f"psutil not accessible: {e}"}


def run_all_health_checks() -> Dict[str, Any]:
    """Run all subsystem health checks and return aggregated status."""
    checks = {
        "database": check_database_health(),
        "dnse_hub": check_dnse_hub_health(),
        "job_states": check_job_states(),
        "system_resources": check_system_resources(),
    }

    all_healthy = all(c.get("healthy", False) for c in checks.values())
    summary = monitoring_svc.get_status_summary()

    return {
        "timestamp": datetime.now(TZ_VN).isoformat(),
        "overall_healthy": all_healthy,
        "health_status": "HEALTHY" if all_healthy else ("CRITICAL" if summary["critical_24h"] > 0 else "DEGRADED"),
        "summary": summary,
        "checks": checks,
    }
