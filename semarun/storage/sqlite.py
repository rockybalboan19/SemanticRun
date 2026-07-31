"""SQLite storage backend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from semarun.models.checkpoint import Checkpoint
from semarun.models.state import RunRecord, RunStatus, SideEffectRecord, new_id


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
                policy_mapping_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                latest_checkpoint_id TEXT,
                last_green_checkpoint_id TEXT,
                step_count INTEGER NOT NULL DEFAULT 0,
                current_step_id TEXT
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

            CREATE TABLE IF NOT EXISTS side_effects (
                id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                target TEXT NOT NULL,
                payload_hash TEXT NOT NULL,
                schema_hash TEXT NOT NULL,
                request_payload_hash TEXT NOT NULL DEFAULT '',
                recovery_relevant INTEGER NOT NULL DEFAULT 0,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE TABLE IF NOT EXISTS ledger_blobs (
                content_hash TEXT PRIMARY KEY,
                data BLOB NOT NULL,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshot_nodes (
                node_id TEXT PRIMARY KEY,
                parent_id TEXT,
                content_hash TEXT NOT NULL,
                run_id TEXT NOT NULL,
                ref_count INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                FOREIGN KEY (content_hash) REFERENCES ledger_blobs(content_hash)
            );

            CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id ON checkpoints(run_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_run_id ON audit_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
            CREATE INDEX IF NOT EXISTS idx_side_effects_run_id ON side_effects(run_id);
            CREATE INDEX IF NOT EXISTS idx_snapshot_nodes_run_id ON snapshot_nodes(run_id);
            """
        )
        self._migrate_side_effects_columns()
        self._conn.commit()

    def _migrate_side_effects_columns(self) -> None:
        cols = {
            row[1]
            for row in self._conn.execute("PRAGMA table_info(side_effects)").fetchall()
        }
        if "request_payload_hash" not in cols:
            self._conn.execute(
                "ALTER TABLE side_effects ADD COLUMN request_payload_hash TEXT NOT NULL DEFAULT ''"
            )
        if "recovery_relevant" not in cols:
            self._conn.execute(
                "ALTER TABLE side_effects ADD COLUMN recovery_relevant INTEGER NOT NULL DEFAULT 0"
            )

    def create_run(self, run: RunRecord) -> RunRecord:
        self._conn.execute(
            """
            INSERT INTO runs (
                id, intent, status, model_context_json, policy_mapping_json,
                created_at, updated_at, latest_checkpoint_id, last_green_checkpoint_id,
                step_count, current_step_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                run.last_green_checkpoint_id,
                run.step_count,
                run.current_step_id,
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
            last_green_checkpoint_id=row["last_green_checkpoint_id"],
            step_count=row["step_count"],
            current_step_id=row["current_step_id"],
        )

    def update_run(self, run: RunRecord) -> RunRecord:
        run.updated_at = datetime.now(timezone.utc)
        self._conn.execute(
            """
            UPDATE runs SET
                intent = ?, status = ?, model_context_json = ?,
                updated_at = ?, latest_checkpoint_id = ?, last_green_checkpoint_id = ?,
                step_count = ?, current_step_id = ?
            WHERE id = ?
            """,
            (
                run.intent,
                run.status.value,
                run.model_context.model_dump_json(),
                run.updated_at.isoformat(),
                run.latest_checkpoint_id,
                run.last_green_checkpoint_id,
                run.step_count,
                run.current_step_id,
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

    def append_side_effect(self, record: SideEffectRecord) -> SideEffectRecord:
        self._conn.execute(
            """
            INSERT INTO side_effects (
                id, run_id, step_id, kind, target, payload_hash, schema_hash,
                request_payload_hash, recovery_relevant, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.run_id,
                record.step_id,
                record.kind,
                record.target,
                record.payload_hash,
                record.schema_hash,
                record.request_payload_hash,
                1 if record.recovery_relevant else 0,
                record.recorded_at.isoformat(),
            ),
        )
        self._conn.commit()
        return record

    def get_last_outbound(
        self, run_id: str, target: str, kind: str | None = None
    ) -> SideEffectRecord | None:
        if kind:
            row = self._conn.execute(
                """
                SELECT * FROM side_effects
                WHERE run_id = ? AND target = ? AND kind = ?
                  AND request_payload_hash != ''
                ORDER BY recorded_at DESC LIMIT 1
                """,
                (run_id, target, kind),
            ).fetchone()
        else:
            row = self._conn.execute(
                """
                SELECT * FROM side_effects
                WHERE run_id = ? AND target = ?
                  AND request_payload_hash != ''
                ORDER BY recorded_at DESC LIMIT 1
                """,
                (run_id, target),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_side_effect(row)

    def put_blob(self, content_hash: str, data: bytes) -> str:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT OR IGNORE INTO ledger_blobs (content_hash, data, created_at)
            VALUES (?, ?, ?)
            """,
            (content_hash, data, now),
        )
        self._conn.commit()
        return content_hash

    def get_blob(self, content_hash: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT data FROM ledger_blobs WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return None if row is None else bytes(row["data"])

    def put_snapshot_node(
        self,
        node_id: str,
        parent_id: str | None,
        content_hash: str,
        run_id: str,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            """
            INSERT INTO snapshot_nodes
                (node_id, parent_id, content_hash, run_id, ref_count, created_at)
            VALUES (?, ?, ?, ?, 1, ?)
            """,
            (node_id, parent_id, content_hash, run_id, now),
        )
        self._conn.commit()

    def get_snapshot_node(self, node_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM snapshot_nodes WHERE node_id = ?", (node_id,)
        ).fetchone()
        if row is None:
            return None
        return {
            "node_id": row["node_id"],
            "parent_id": row["parent_id"],
            "content_hash": row["content_hash"],
            "run_id": row["run_id"],
            "ref_count": row["ref_count"],
            "created_at": row["created_at"],
        }

    def increment_snapshot_ref(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE snapshot_nodes SET ref_count = ref_count + 1 WHERE node_id = ?",
            (node_id,),
        )
        self._conn.commit()

    def decrement_snapshot_ref(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE snapshot_nodes SET ref_count = MAX(ref_count - 1, 0) WHERE node_id = ?",
            (node_id,),
        )
        self._conn.commit()

    def list_snapshot_nodes(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM snapshot_nodes WHERE run_id = ?
            ORDER BY created_at ASC
            """,
            (run_id,),
        ).fetchall()
        return [
            {
                "node_id": r["node_id"],
                "parent_id": r["parent_id"],
                "content_hash": r["content_hash"],
                "run_id": r["run_id"],
                "ref_count": r["ref_count"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def delete_blob(self, content_hash: str) -> None:
        self._conn.execute(
            "DELETE FROM ledger_blobs WHERE content_hash = ?", (content_hash,)
        )
        self._conn.commit()

    def delete_snapshot_node(self, node_id: str) -> None:
        self._conn.execute(
            "DELETE FROM snapshot_nodes WHERE node_id = ?", (node_id,)
        )
        self._conn.commit()

    def _row_to_side_effect(self, r: sqlite3.Row) -> SideEffectRecord:
        keys = set(r.keys())
        return SideEffectRecord(
            id=r["id"],
            run_id=r["run_id"],
            step_id=r["step_id"],
            kind=r["kind"],
            target=r["target"],
            payload_hash=r["payload_hash"],
            schema_hash=r["schema_hash"],
            request_payload_hash=r["request_payload_hash"]
            if "request_payload_hash" in keys
            else "",
            recovery_relevant=bool(r["recovery_relevant"])
            if "recovery_relevant" in keys
            else False,
            recorded_at=datetime.fromisoformat(r["recorded_at"]),
        )

    def list_side_effects(
        self, run_id: str, step_id: str | None = None
    ) -> list[SideEffectRecord]:
        if step_id is None:
            rows = self._conn.execute(
                """
                SELECT * FROM side_effects WHERE run_id = ? ORDER BY recorded_at ASC
                """,
                (run_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                """
                SELECT * FROM side_effects
                WHERE run_id = ? AND step_id = ? ORDER BY recorded_at ASC
                """,
                (run_id, step_id),
            ).fetchall()
        return [
            self._row_to_side_effect(r)
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
