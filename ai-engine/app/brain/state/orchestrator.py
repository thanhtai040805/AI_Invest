"""Swarm Orchestrator Service — TASK-2

Manages the daily 12-agent swarm pipeline execution.
Bridges the Swarm Agent framework (LLM-based) and the Quantitative Core (Math/SQL-based).
"""

import time
import logging
from datetime import date, datetime
from typing import Dict, Any, Optional

from app.brain.state.runtime import SwarmRuntime
from app.brain.state.store import SwarmStore, swarm_runs_root
from app.brain.state.models import RunStatus

logger = logging.getLogger(__name__)

class SwarmOrchestrator:
    def __init__(self, store: Optional[SwarmStore] = None, runtime: Optional[SwarmRuntime] = None):
        self._store = store or SwarmStore(swarm_runs_root())
        self._runtime = runtime or SwarmRuntime(self._store)

    def run_daily_pipeline(self, market: str = "HOSE") -> str:
        """Starts the 12-agent swarm run asynchronously in the background.

        Returns:
            The run_id of the started swarm.
        """
        logger.info(f"Starting daily 12-agent swarm pipeline for market {market}")
        user_vars = {"market": market, "goal": "Daily structural factor rotation and stock selection"}
        
        run = self._runtime.start_run(
            preset_name="vn_market_swarm",
            user_vars=user_vars,
            include_shell_tools=False
        )
        logger.info(f"Swarm run {run.id} started with status {run.status.value}")
        return run.id

    def wait_for_run(self, run_id: str, timeout_seconds: int = 300) -> Optional[Dict[str, Any]]:
        """Synchronously waits for a swarm run to complete (primarily for testing/CLI)."""
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            loaded = self._store.load_run(run_id)
            if not loaded:
                return None
            
            run = self._store.reconcile_run(loaded, write=False)
            if run.status in (RunStatus.completed, RunStatus.failed, RunStatus.cancelled):
                return {
                    "id": run.id,
                    "status": run.status.value,
                    "final_report": run.final_report,
                    "completed_at": run.completed_at
                }
            time.sleep(1)
            
        logger.warning(f"Timeout waiting for swarm run {run_id} to finish")
        return None

    def get_latest_swarm_recommendation(self, target_date: date) -> Optional[Dict[str, Any]]:
        """Retrieves the latest completed swarm run's final report/results for the target date."""
        runs = self._store.list_runs()
        # Find the latest completed run for this day
        completed_runs = [
            r for r in runs 
            if r.preset_name == "vn_market_swarm" 
            and r.status == RunStatus.completed
            and r.completed_at
            and r.completed_at.startswith(target_date.isoformat())
        ]
        
        if not completed_runs:
            return None
            
        # Sort by completion date descending
        completed_runs.sort(key=lambda x: x.completed_at, reverse=True)
        latest_run = completed_runs[0]
        
        # Hydrate the full run to load final report
        full_run = self._store.load_run(latest_run.id)
        if not full_run or not full_run.final_report:
            return None
            
        return {
            "run_id": full_run.id,
            "completed_at": full_run.completed_at,
            "final_report": full_run.final_report,
            "tasks_summary": {t.id: t.summary for t in full_run.tasks if t.summary}
        }

swarm_orchestrator = SwarmOrchestrator()
