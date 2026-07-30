"""SQLite storage backend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from semarun.models.state import Checkpoint, RunRecord, RunStatus, new_id


def _serialize(obj: object) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class SQLiteStorage:
    def __init__(self, db_path: str = "semarun.db") -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                id TEXT PRIMARY KEY,
                intent TEXT NOT NULL,
                status TEXT NOT NULL,
                model_context_json TEXT NOT NULL,
                continuation_policy_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                latest_checkpoint_id TEXT,
                step_count INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS checkpoints (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                snapshot_json TEXT NOT NULL,
                summary_text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS audit_events (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id ON checkpoints(run_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_run_id ON audit_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
            """
        )
        self._conn.commit()

    def create_run(self, run: RunRecord) -> RunRecord:
        self._conn.execute(
            """
            INSERT INTO runs (
                id, intent, status, model_context_json, continuation_policy_json,
                created_at, updated_at, latest_checkpoint_id, step_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run.id,
                run.intent,
                run.status.value,
                run.model_context.model_dump_json(),
                "{}",
                run.created_at.isoformat(),
                run.updated_at.isoformat(),
                run.latest_checkpoint_id,
                run.step_count,
            ),
        )
        self._conn.commit()
        return run

    def get_run(self, run_id: str) -> RunRecord | None:
        row = self._conn.execute(
            "SELECT * FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
        if row is None:
            return None
        from semarun.models.state import ModelContext

        return RunRecord(
            id=row["id"],
            intent=row["intent"],
            status=RunStatus(row["status"]),
            model_context=ModelContext.model_validate_json(row["model_context_json"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            latest_checkpoint_id=row["latest_checkpoint_id"],
            step_count=row["step_count"],
        )

    def update_run(self, run: RunRecord) -> RunRecord:
        run.updated_at = datetime.now(timezone.utc)
        self._conn.execute(
            """
            UPDATE runs SET
                intent = ?, status = ?, model_context_json = ?,
                updated_at = ?, latest_checkpoint_id = ?, step_count = ?
            WHERE id = ?
            """,
            (
                run.intent,
                run.status.value,
                run.model_context.model_dump_json(),
                run.updated_at.isoformat(),
                run.latest_checkpoint_id,
                run.step_count,
                run.id,
            ),
        )
        self._conn.commit()
        return run

    def save_checkpoint(self, checkpoint: Checkpoint) -> Checkpoint:
        snapshot = checkpoint.model_dump(mode="json")
        self._conn.execute(
            """
            INSERT INTO checkpoints (id, run_id, snapshot_json, summary_text, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                checkpoint.id,
                checkpoint.run_id,
                json.dumps(snapshot, default=_serialize),
                checkpoint.summary_text,
                checkpoint.created_at.isoformat(),
            ),
        )
        self._conn.commit()
        return checkpoint

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint | None:
        row = self._conn.execute(
            "SELECT snapshot_json FROM checkpoints WHERE id = ?", (checkpoint_id,)
        ).fetchone()
        if row is None:
            return None
        return Checkpoint.model_validate(json.loads(row["snapshot_json"]))

    def get_latest_checkpoint(self, run_id: str) -> Checkpoint | None:
        row = self._conn.execute(
            """
            SELECT snapshot_json FROM checkpoints
            WHERE run_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return Checkpoint.model_validate(json.loads(row["snapshot_json"]))

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]:
        rows = self._conn.execute(
            """
            SELECT snapshot_json FROM checkpoints
            WHERE run_id = ? ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        return [Checkpoint.model_validate(json.loads(r["snapshot_json"])) for r in rows]

    def append_audit_event(
        self, run_id: str, event_type: str, payload: dict
    ) -> str:
        event_id = new_id("evt")
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO audit_events (id, run_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (event_id, run_id, event_type, json.dumps(payload, default=_serialize), now),
        )
        self._conn.commit()
        return event_id

    def list_audit_events(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT id, run_id, event_type, payload_json, created_at
            FROM audit_events WHERE run_id = ? ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "run_id": r["run_id"],
                "event_type": r["event_type"],
                "payload": json.loads(r["payload_json"]),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def save_approval(
        self,
        run_id: str,
        action: str,
        status: str,
        payload: dict,
        approval_id: str | None = None,
    ) -> str:
        aid = approval_id or new_id("appr")
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO approvals (id, run_id, action, status, payload_json, created_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            """,
            (aid, run_id, action, status, json.dumps(payload), now),
        )
        self._conn.commit()
        return aid

    def get_latest_approval(self, run_id: str) -> dict | None:
        row = self._conn.execute(
            """
            SELECT * FROM approvals WHERE run_id = ?
            ORDER BY created_at DESC LIMIT 1
            """,
            (run_id,),
        ).fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "run_id": row["run_id"],
            "action": row["action"],
            "status": row["status"],
            "payload": json.loads(row["payload_json"]),
            "created_at": row["created_at"],
            "resolved_at": row["resolved_at"],
        }

    def update_approval(self, approval_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE approvals SET status = ?, resolved_at = ? WHERE id = ?",
            (status, now, approval_id),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @classmethod
    def from_path(cls, path: str | Path) -> SQLiteStorage:
        return cls(str(path))
