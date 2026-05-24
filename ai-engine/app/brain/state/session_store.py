"""File-based SessionStore for chat sessions, messages, and attempts."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, TypeVar

from app.brain.state.models import Attempt, Message, Session

T = TypeVar("T")

RUNS_DIR = Path(__file__).resolve().parents[2] / "runs"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SessionStore:
    """Persists Session / Message / Attempt data on the filesystem under ``runs/.sessions/``.

    Layout::

        runs/.sessions/
        ├── {session_id}/
        │   ├── session.json
        │   ├── messages.jsonl
        │   └── attempts/
        │       ├── {attempt_id}.json
        │       └── ...
        └── ...
    """

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        self.base_dir = (base_dir or RUNS_DIR) / ".sessions"
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._write_lock = threading.Lock()

    # ── Session CRUD ──

    def create_session(self, session: Session) -> Session:
        sd = self._session_dir(session.session_id)
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "attempts").mkdir(exist_ok=True)
        self._write_json(sd / "session.json", session.model_dump())
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        p = self._session_dir(session_id) / "session.json"
        if not p.exists():
            return None
        return Session(**self._read_json(p))

    def update_session(self, session: Session) -> None:
        sd = self._session_dir(session.session_id)
        if not sd.exists():
            raise FileNotFoundError(f"Session {session.session_id} not found")
        session.updated_at = _now_iso()
        self._write_json(sd / "session.json", session.model_dump())

    def list_sessions(self, limit: int = 50) -> List[Session]:
        if not self.base_dir.exists():
            return []
        results: List[Session] = []
        for d in sorted(self.base_dir.iterdir(), key=lambda p: p.name, reverse=True):
            if not d.is_dir():
                continue
            try:
                s = self.get_session(d.name)
                if s:
                    results.append(s)
                    if len(results) >= limit:
                        break
            except Exception:
                continue
        return results

    def delete_session(self, session_id: str) -> bool:
        sd = self._session_dir(session_id)
        if not sd.exists():
            return False
        import shutil
        shutil.rmtree(sd)
        return True

    # ── Messages ──

    def append_message(self, message: Message) -> Message:
        sd = self._session_dir(message.session_id)
        sd.mkdir(parents=True, exist_ok=True)
        with self._write_lock:
            with (sd / "messages.jsonl").open("a", encoding="utf-8") as f:
                f.write(message.model_dump_json() + "\n")
        return message

    def get_messages(self, session_id: str, limit: int = 100) -> List[Message]:
        p = self._session_dir(session_id) / "messages.jsonl"
        if not p.exists():
            return []
        msgs: List[Message] = []
        with p.open("r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped:
                    msgs.append(Message(**json.loads(stripped)))
        return msgs[-limit:]

    # ── Attempts ──

    def create_attempt(self, attempt: Attempt) -> Attempt:
        ad = self._attempts_dir(attempt.session_id)
        ad.mkdir(parents=True, exist_ok=True)
        self._write_json(ad / f"{attempt.attempt_id}.json", attempt.model_dump())
        return attempt

    def update_attempt(self, attempt: Attempt) -> None:
        ad = self._attempts_dir(attempt.session_id)
        self._write_json(ad / f"{attempt.attempt_id}.json", attempt.model_dump())

    def get_attempt(self, session_id: str, attempt_id: str) -> Optional[Attempt]:
        p = self._attempts_dir(session_id) / f"{attempt_id}.json"
        if not p.exists():
            return None
        return Attempt(**self._read_json(p))

    # ── Helpers ──

    def _session_dir(self, session_id: str) -> Path:
        return self.base_dir / session_id

    def _attempts_dir(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "attempts"

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        tmp = path.with_suffix(".tmp")
        with self._write_lock:
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(path)

    @staticmethod
    def _read_json(path: Path) -> Dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))
