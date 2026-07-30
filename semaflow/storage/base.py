"""Storage backend protocol."""

from __future__ import annotations

from typing import Protocol

from semaflow.models.state import Checkpoint, RunRecord


class StorageBackend(Protocol):
    def create_run(self, run: RunRecord) -> RunRecord: ...

    def get_run(self, run_id: str) -> RunRecord | None: ...

    def update_run(self, run: RunRecord) -> RunRecord: ...

    def save_checkpoint(self, checkpoint: Checkpoint) -> Checkpoint: ...

    def get_checkpoint(self, checkpoint_id: str) -> Checkpoint | None: ...

    def get_latest_checkpoint(self, run_id: str) -> Checkpoint | None: ...

    def list_checkpoints(self, run_id: str) -> list[Checkpoint]: ...

    def append_audit_event(
        self, run_id: str, event_type: str, payload: dict
    ) -> str: ...

    def list_audit_events(self, run_id: str) -> list[dict]: ...

    def save_approval(
        self,
        run_id: str,
        action: str,
        status: str,
        payload: dict,
        approval_id: str | None = None,
    ) -> str: ...

    def get_latest_approval(self, run_id: str) -> dict | None: ...

    def update_approval(self, approval_id: str, status: str) -> None: ...

    def close(self) -> None: ...
