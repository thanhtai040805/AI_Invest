"""Job state tracking — PostgreSQL-backed, survives restarts."""

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.infrastructure.database.pg_pool import get_cursor


def get_job(job_name: str) -> Optional[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM job_states WHERE job_name = %s", (job_name,))
        row = cur.fetchone()
        if not row:
            return None
        return {
            "job_name": row[0],
            "status": row[1],
            "started_at": row[2].isoformat() if row[2] else None,
            "completed_at": row[3].isoformat() if row[3] else None,
            "metadata": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
            "error": row[5],
        }


def set_running(job_name: str, metadata: Optional[dict] = None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """INSERT INTO job_states (job_name, status, started_at, metadata)
               VALUES (%s, 'running', %s, %s)
               ON CONFLICT (job_name) DO UPDATE SET
                   status = 'running',
                   started_at = %s,
                   completed_at = NULL,
                   metadata = COALESCE(%s, job_states.metadata),
                   error = NULL""",
            (
                job_name,
                datetime.now(timezone.utc),
                json.dumps(metadata or {}),
                datetime.now(timezone.utc),
                json.dumps(metadata or {}),
            ),
        )


def set_completed(job_name: str, metadata: Optional[dict] = None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """UPDATE job_states SET
                   status = 'completed',
                   completed_at = %s,
                   metadata = COALESCE(%s::jsonb, metadata)
               WHERE job_name = %s""",
            (datetime.now(timezone.utc), json.dumps(metadata or {}) if metadata else None, job_name),
        )


def set_failed(job_name: str, error: str, metadata: Optional[dict] = None) -> None:
    with get_cursor() as cur:
        cur.execute(
            """UPDATE job_states SET
                   status = 'failed',
                   completed_at = %s,
                   error = %s,
                   metadata = COALESCE(%s::jsonb, metadata)
               WHERE job_name = %s""",
            (datetime.now(timezone.utc), error, json.dumps(metadata or {}) if metadata else None, job_name),
        )


def list_jobs() -> list[dict]:
    with get_cursor() as cur:
        cur.execute("SELECT * FROM job_states ORDER BY job_name")
        rows = cur.fetchall()
        return [
            {
                "job_name": r[0],
                "status": r[1],
                "started_at": r[2].isoformat() if r[2] else None,
                "completed_at": r[3].isoformat() if r[3] else None,
                "metadata": r[4] if isinstance(r[4], dict) else json.loads(r[4] or "{}"),
                "error": r[5],
            }
            for r in rows
        ]


get_all_jobs = list_jobs


def is_job_completed_today(job_name: str) -> bool:
    """Check if a job completed today (VN timezone)."""
    from datetime import timedelta
    vn_tz = timezone(timedelta(hours=7))
    today_start = datetime.now(vn_tz).replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_utc = today_start.astimezone(timezone.utc)

    with get_cursor() as cur:
        cur.execute(
            """SELECT status, completed_at FROM job_states
               WHERE job_name = %s AND status = 'completed'
                 AND completed_at >= %s""",
            (job_name, today_start_utc),
        )
        return cur.fetchone() is not None
