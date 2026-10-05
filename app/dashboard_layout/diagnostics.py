"""Bounded geometry-only run snapshots; diagnostic failures never stop a solve."""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from collections import Counter
from contextlib import closing, contextmanager
from contextvars import ContextVar
from pathlib import Path

logger = logging.getLogger(__name__)
_trace: ContextVar[Counter | None] = ContextVar("layout_trace", default=None)
RETENTION_SECONDS = 7 * 86400
MAX_RUNS = 1000
MAX_TOTAL_BYTES = 64 * 1024 * 1024
MAX_RECORD_BYTES = 2 * 1024 * 1024


def count(name: str, amount: int = 1) -> None:
    """Count bounded, code-defined decisions without retaining search branches."""
    current = _trace.get()
    if current is not None:
        current[name] += amount


@contextmanager
def capture():
    """Isolate counters between concurrent solves, including worker processes."""
    counters = Counter()
    token = _trace.set(counters)
    try:
        yield counters
    finally:
        _trace.reset(token)


class LayoutDiagnostics:
    """Small SQLite journal under the existing persistent application data volume."""

    def __init__(self, root: Path):
        self.path = root / "layout-diagnostics" / "runs.sqlite3"

    @contextmanager
    def _db(self):
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with closing(sqlite3.connect(self.path, timeout=0.2)) as db:
            self.path.chmod(0o600)
            db.execute("PRAGMA journal_mode=DELETE")
            db.execute("""CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, call TEXT NOT NULL,
                created REAL NOT NULL, payload TEXT NOT NULL)""")
            with db:
                db.execute("DELETE FROM runs WHERE created < ?", (time.time() - RETENTION_SECONDS,))
                yield db

    def save(self, record: dict) -> bool:
        """Persist an atomic snapshot, evicting oldest runs within bounded retention."""
        try:
            payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
            if len(payload) > MAX_RECORD_BYTES:
                raise ValueError("diagnostic size limit")
            with self._db() as db:
                db.execute("INSERT OR REPLACE INTO runs VALUES (?, ?, ?, ?, ?)", (
                    record["layoutRunId"], record["sessionId"], record["toolCallId"],
                    record["createdAt"], payload))
                rows = db.execute("SELECT id, length(payload) FROM runs ORDER BY created DESC, id").fetchall()
                size = 0
                expired = []
                for index, (run_id, length) in enumerate(rows):
                    size += length
                    if index >= MAX_RUNS or size > MAX_TOTAL_BYTES:
                        expired.append((run_id,))
                db.executemany("DELETE FROM runs WHERE id = ?", expired)
            return True
        except (OSError, sqlite3.Error, ValueError, TypeError):
            # Do not log the exception message: it may contain paths or payloads.
            logger.warning("layout_diagnostics_write_failed")
            return False

    def get(self, run_id: str, session_id: str | None = None) -> dict | None:
        """Read one unexpired run; callers enforce owner or inspector authorization."""
        try:
            with self._db() as db:
                row = db.execute("SELECT session, payload FROM runs WHERE id = ?", (run_id,)).fetchone()
                if row and (session_id is None or row[0] == session_id):
                    return json.loads(row[1])
        except (OSError, sqlite3.Error, ValueError):
            logger.warning("layout_diagnostics_read_failed")
        return None

    def outcome(self, run_id: str, session_id: str, outcome: dict) -> bool:
        """Attach only validated client evidence, without replacing server decisions."""
        try:
            with self._db() as db:
                row = db.execute("SELECT payload FROM runs WHERE id = ? AND session = ? AND call = ?",
                    (run_id, session_id, outcome["toolCallId"])).fetchone()
                if row is None:
                    return False
                record = json.loads(row[0])
                record["frontendOutcome"] = outcome
                record["frontendReportedAt"] = time.time()
                payload = json.dumps(record, separators=(",", ":"), ensure_ascii=True)
                if len(payload) > MAX_RECORD_BYTES:
                    return False
                db.execute("UPDATE runs SET payload = ? WHERE id = ?", (payload, run_id))
            return True
        except (OSError, sqlite3.Error, ValueError):
            logger.warning("layout_diagnostics_outcome_failed")
            return False

    def list_runs(self, session_id: str, call_id: str | None, limit: int) -> list[dict]:
        """Find recent IDs in a known session without returning replay payloads."""
        try:
            with self._db() as db:
                rows = db.execute("""SELECT payload FROM runs WHERE session = ?
                    AND (? IS NULL OR call = ?) ORDER BY created DESC LIMIT ?""",
                    (session_id, call_id, call_id, min(50, max(1, limit)))).fetchall()
            keys = ("layoutRunId", "sessionId", "toolCallId", "createdAt", "state", "solverRevision")
            return [{key: record[key] for key in keys if key in record}
                    for row in rows for record in [json.loads(row[0])]]
        except (OSError, sqlite3.Error, ValueError):
            logger.warning("layout_diagnostics_list_failed")
            return []


def store_for(services) -> LayoutDiagnostics | None:
    """Use the configured data directory; never silently write into a fallback cwd."""
    root = getattr(getattr(services, "settings", None), "app_data_dir", None)
    return LayoutDiagnostics(Path(root)) if root is not None else None
