"""Checkpoint snapshot engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semaflow.models.policy import ContinuationPolicy
from semaflow.models.state import AgentState, Checkpoint, ModelContext, RunStatus

if TYPE_CHECKING:
    from semaflow.audit.log import AuditLog
    from semaflow.storage.base import StorageBackend


def build_summary(state: AgentState, status: RunStatus) -> str:
    pending = ", ".join(a.action for a in state.pending_actions) or "none"
    questions = "; ".join(state.open_questions) or "none"
    return (
        f"Intent: {state.intent}\n"
        f"Status: {status.value}\n"
        f"Pending actions: {pending}\n"
        f"Open questions: {questions}"
    )


class CheckpointEngine:
    def __init__(
        self,
        storage: StorageBackend,
        audit: AuditLog,
        periodic_interval: int = 0,
    ) -> None:
        self._storage = storage
        self._audit = audit
        self._periodic_interval = periodic_interval

    @property
    def periodic_interval(self) -> int:
        return self._periodic_interval

    def create_checkpoint(
        self,
        run_id: str,
        status: RunStatus,
        state: AgentState,
        model_context: ModelContext,
        policy: ContinuationPolicy,
    ) -> Checkpoint:
        summary = build_summary(state, status)
        checkpoint = Checkpoint.from_agent_state(
            run_id=run_id,
            status=status,
            state=state,
            model_context=model_context,
            continuation_policy_json=policy.model_dump(mode="json"),
            summary_text=summary,
        )
        saved = self._storage.save_checkpoint(checkpoint)
        self._audit.emit(
            run_id,
            "checkpoint_created",
            {"checkpoint_id": saved.id, "status": status.value},
        )
        return saved
