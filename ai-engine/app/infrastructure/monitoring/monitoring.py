"""System monitoring and alerting service.

Tracks:
  - DNSE WebSocket health (connection status, staleness)
  - Data backfill job status
  - Risk flag alerts (push notifications for HIGH severity)
  - ML model freshness
  - System resource usage
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
    """Central monitoring service with alert history."""

    def __init__(self, max_alerts: int = 1000):
        self.alerts: List[Alert] = []
        self.max_alerts = max_alerts
        self.alert_handlers: List[Callable[[Alert], None]] = []
        self._start_time = time.time()

    def add_alert_handler(self, handler: Callable[[Alert], None]) -> None:
        """Register a handler for new alerts (e.g., send to Slack, email)."""
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
            severity="INFO", source=source, message=message,
            data=data or {},
        )
        self._record(a)
        logger.info("[%s] %s", source, message)

    def warning(self, source: str, message: str, data: Optional[Dict] = None) -> None:
        a = Alert(
            timestamp=datetime.now(TZ_VN).isoformat(),
            severity="WARNING", source=source, message=message,
            data=data or {},
        )
        self._record(a)
        logger.warning("[%s] %s", source, message)

    def critical(self, source: str, message: str, data: Optional[Dict] = None) -> None:
        a = Alert(
            timestamp=datetime.now(TZ_VN).isoformat(),
            severity="CRITICAL", source=source, message=message,
            data=data or {},
        )
        self._record(a)
        logger.critical("[%s] %s", source, message)

    def get_recent(self, limit: int = 20, severity: Optional[str] = None) -> List[Dict]:
        """Get recent alerts, optionally filtered by severity."""
        filtered = [a for a in self.alerts if severity is None or a.severity == severity]
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

    def get_status_summary(self) -> Dict[str, Any]:
        """Get overall system health summary."""
        critical = sum(1 for a in self.alerts if a.severity == "CRITICAL" and 
                       datetime.fromisoformat(a.timestamp) > datetime.now(TZ_VN) - timedelta(hours=24))
        warnings = sum(1 for a in self.alerts if a.severity == "WARNING" and
                       datetime.fromisoformat(a.timestamp) > datetime.now(TZ_VN) - timedelta(hours=24))

        return {
            "uptime_seconds": int(time.time() - self._start_time),
            "total_alerts": len(self.alerts),
            "critical_24h": critical,
            "warnings_24h": warnings,
            "health": "CRITICAL" if critical > 0 else ("WARNING" if warnings > 5 else "GOOD"),
        }


monitoring_svc = MonitoringService()


# ---------------------------------------------------------------------------
# Health check helpers
# ---------------------------------------------------------------------------

def check_dnse_hub_health() -> Dict[str, Any]:
    """Check DNSE WebSocket hub health."""
    try:
        from app.infrastructure.external_api.dnse.stream_hub import get_stream_hub
        hub = get_stream_hub()
        status = hub.status() if hasattr(hub, "status") else {}
        is_connected = status.get("connected", False)

        if not is_connected:
            monitoring_svc.warning(
                "dnse_hub", "DNSE WebSocket disconnected",
                data={"status": status},
            )
        return {
            "source": "dnse_hub",
            "connected": is_connected,
            "status": status,
            "healthy": is_connected,
        }
    except Exception as e:
        monitoring_svc.critical("dnse_hub", f"Health check failed: {e}")
        return {"source": "dnse_hub", "connected": False, "healthy": False, "error": str(e)}


def check_job_states() -> Dict[str, Any]:
    """Check backfill job states."""
    try:
        from app.infrastructure.monitoring.job_state_service import get_all_jobs
        jobs = get_all_jobs()
        failed = [j for j in jobs if j.get("status") == "failed"]
        if failed:
            monitoring_svc.warning(
                "job_states", f"{len(failed)} failed job(s)",
                data={"failed_jobs": failed[:5]},
            )
        return {
            "source": "job_states",
            "total_jobs": len(jobs),
            "failed": len(failed),
            "healthy": len(failed) == 0,
        }
    except Exception as e:
        return {"source": "job_states", "healthy": False, "error": str(e)}


def run_all_health_checks() -> Dict[str, Any]:
    """Run all health checks and return aggregated status."""
    checks = {
        "dnse_hub": check_dnse_hub_health(),
        "job_states": check_job_states(),
    }

    all_healthy = all(c.get("healthy", False) for c in checks.values())
    summary = monitoring_svc.get_status_summary()

    return {
        "timestamp": datetime.now(TZ_VN).isoformat(),
        "overall_healthy": all_healthy,
        "summary": summary,
        "checks": checks,
    }


# Risk alerts handled by CRS 7-layer risk_assessments (see daily_etl)
