"""Isolated ledger blob/snapshot storage (R3: per-sandbox SQLite)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Protocol


class LedgerStorage(Protocol):
    def put_blob(self, content_hash: str, data: bytes) -> None: ...

    def get_blob(self, content_hash: str) -> bytes | None: ...

    def delete_blob(self, content_hash: str) -> None: ...

    def put_snapshot_node(
        self,
        node_id: str,
        parent_id: str | None,
        content_hash: str,
        run_id: str,
    ) -> None: ...

    def get_snapshot_node(self, node_id: str) -> dict | None: ...

    def list_snapshot_nodes(self, run_id: str) -> list[dict]: ...

    def increment_snapshot_ref(self, node_id: str) -> None: ...

    def decrement_snapshot_ref(self, node_id: str) -> None: ...

    def delete_snapshot_node(self, node_id: str) -> None: ...

    def close(self) -> None: ...


class SQLiteLedgerStore:
    """Separate SQLite file for COW snapshot blobs and nodes."""

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS blobs (
                content_hash TEXT PRIMARY KEY,
                data BLOB NOT NULL
            );

            CREATE TABLE IF NOT EXISTS snapshot_nodes (
                node_id TEXT PRIMARY KEY,
                parent_id TEXT,
                content_hash TEXT NOT NULL,
                run_id TEXT NOT NULL,
                ref_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_snapshot_nodes_run_id
                ON snapshot_nodes(run_id);
            """
        )
        self._conn.commit()

    def put_blob(self, content_hash: str, data: bytes) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO blobs (content_hash, data) VALUES (?, ?)",
            (content_hash, data),
        )
        self._conn.commit()

    def get_blob(self, content_hash: str) -> bytes | None:
        row = self._conn.execute(
            "SELECT data FROM blobs WHERE content_hash = ?", (content_hash,)
        ).fetchone()
        return None if row is None else row["data"]

    def delete_blob(self, content_hash: str) -> None:
        self._conn.execute("DELETE FROM blobs WHERE content_hash = ?", (content_hash,))
        self._conn.commit()

    def put_snapshot_node(
        self,
        node_id: str,
        parent_id: str | None,
        content_hash: str,
        run_id: str,
    ) -> None:
        self._conn.execute(
            """
            INSERT INTO snapshot_nodes (node_id, parent_id, content_hash, run_id, ref_count)
            VALUES (?, ?, ?, ?, 0)
            """,
            (node_id, parent_id, content_hash, run_id),
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
        }

    def list_snapshot_nodes(self, run_id: str) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT * FROM snapshot_nodes WHERE run_id = ?
            ORDER BY created_at ASC, rowid ASC
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
            }
            for r in rows
        ]

    def increment_snapshot_ref(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE snapshot_nodes SET ref_count = ref_count + 1 WHERE node_id = ?",
            (node_id,),
        )
        self._conn.commit()

    def decrement_snapshot_ref(self, node_id: str) -> None:
        self._conn.execute(
            "UPDATE snapshot_nodes SET ref_count = MAX(0, ref_count - 1) WHERE node_id = ?",
            (node_id,),
        )
        self._conn.commit()

    def delete_snapshot_node(self, node_id: str) -> None:
        self._conn.execute("DELETE FROM snapshot_nodes WHERE node_id = ?", (node_id,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    @classmethod
    def for_run(cls, base_dir: str | Path, run_id: str) -> SQLiteLedgerStore:
        path = Path(base_dir)
        path.mkdir(parents=True, exist_ok=True)
        return cls(str(path / f"ledger_{run_id}.db"))
