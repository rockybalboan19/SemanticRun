"""Append-only audit event log."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from semanticrun.storage.base import StorageBackend


DIVERGENCE_EVENTS = frozenset(
    {"divergence_detected", "policy_applied"}
)


class AuditLog:
    def __init__(self, storage: StorageBackend) -> None:
        self._storage = storage

    def emit(self, run_id: str, event_type: str, payload: dict | None = None) -> str:
        return self._storage.append_audit_event(
            run_id, event_type, payload or {}
        )

    def get_run_history(self, run_id: str) -> list[dict]:
        return self._storage.list_audit_events(run_id)

    def get_checkpoint_history(self, run_id: str) -> list[dict]:
        return [
            e
            for e in self._storage.list_audit_events(run_id)
            if e["event_type"] == "checkpoint_created"
        ]

    def get_divergence_events(self, run_id: str) -> list[dict]:
        return [
            e
            for e in self._storage.list_audit_events(run_id)
            if e["event_type"] in DIVERGENCE_EVENTS
        ]
