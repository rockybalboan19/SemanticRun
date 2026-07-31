"""Ledger storage protocol - swappable backend for co-located sandboxes (CRAB R3)."""

from __future__ import annotations

from typing import Protocol

from semarun.models.state import SideEffectRecord


class LedgerStorage(Protocol):
    """Isolated ledger persistence; implement with SQLite, LMDB, etc."""

    def append_side_effect(self, record: SideEffectRecord) -> SideEffectRecord: ...

    def list_side_effects(
        self, run_id: str, step_id: str | None = None
    ) -> list[SideEffectRecord]: ...

    def get_last_outbound(
        self, run_id: str, target: str, kind: str | None = None
    ) -> SideEffectRecord | None: ...

    def put_blob(self, content_hash: str, data: bytes) -> str: ...

    def get_blob(self, content_hash: str) -> bytes | None: ...

    def put_snapshot_node(
        self,
        node_id: str,
        parent_id: str | None,
        content_hash: str,
        run_id: str,
    ) -> None: ...

    def get_snapshot_node(self, node_id: str) -> dict | None: ...

    def increment_snapshot_ref(self, node_id: str) -> None: ...

    def decrement_snapshot_ref(self, node_id: str) -> None: ...

    def list_snapshot_nodes(self, run_id: str) -> list[dict]: ...

    def delete_blob(self, content_hash: str) -> None: ...

    def delete_snapshot_node(self, node_id: str) -> None: ...
