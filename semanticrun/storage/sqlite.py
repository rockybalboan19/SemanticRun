"""SQLite storage backend."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from semanticrun.models.checkpoint import Checkpoint
from semanticrun.models.state import RunRecord, RunStatus, SideEffectRecord, new_id


def _serialize(obj: object) -> str:
    if isinstance(obj, datetime):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


class SQLiteStorage:
    def __init__(self, db_path: str = "semanticrun.db") -> None:
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
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (run_id) REFERENCES runs(id)
            );

            CREATE INDEX IF NOT EXISTS idx_checkpoints_run_id ON checkpoints(run_id);
            CREATE INDEX IF NOT EXISTS idx_audit_events_run_id ON audit_events(run_id);
            CREATE INDEX IF NOT EXISTS idx_approvals_run_id ON approvals(run_id);
            CREATE INDEX IF NOT EXISTS idx_side_effects_run_id ON side_effects(run_id);
            """
        )
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self) -> None:
        cols = {r[1] for r in self._conn.execute("PRAGMA table_info(side_effects)").fetchall()}
        if "outbound_request_hash" not in cols:
            self._conn.execute(
                "ALTER TABLE side_effects ADD COLUMN outbound_request_hash TEXT NOT NULL DEFAULT ''"
            )
        if "side_effect_class" not in cols:
            self._conn.execute(
                "ALTER TABLE side_effects ADD COLUMN side_effect_class TEXT NOT NULL DEFAULT 'read_only'"
            )
        if "replay_permitted" not in cols:
            self._conn.execute(
                "ALTER TABLE side_effects ADD COLUMN replay_permitted INTEGER NOT NULL DEFAULT 1"
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
        from semanticrun.models.state import ModelContext

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
                outbound_request_hash, side_effect_class, replay_permitted, recorded_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record.id,
                record.run_id,
                record.step_id,
                record.kind,
                record.target,
                record.payload_hash,
                record.schema_hash,
                record.outbound_request_hash,
                record.side_effect_class,
                1 if record.replay_permitted else 0,
                record.recorded_at.isoformat(),
            ),
        )
        self._conn.commit()
        return record

    def update_side_effect(self, record: SideEffectRecord) -> SideEffectRecord:
        self._conn.execute(
            """
            UPDATE side_effects SET replay_permitted = ?
            WHERE id = ?
            """,
            (1 if record.replay_permitted else 0, record.id),
        )
        self._conn.commit()
        return record

    def get_latest_side_effect_for_target(
        self, run_id: str, target: str
    ) -> SideEffectRecord | None:
        row = self._conn.execute(
            """
            SELECT * FROM side_effects
            WHERE run_id = ? AND target = ?
            ORDER BY recorded_at DESC, rowid DESC LIMIT 1
            """,
            (run_id, target),
        ).fetchone()
        if row is None:
            return None
        return self._row_to_side_effect(row)

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
            outbound_request_hash=r["outbound_request_hash"] if "outbound_request_hash" in keys else "",
            side_effect_class=r["side_effect_class"] if "side_effect_class" in keys else "read_only",
            replay_permitted=bool(r["replay_permitted"]) if "replay_permitted" in keys else True,
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
