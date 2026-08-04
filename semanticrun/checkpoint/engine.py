"""Checkpoint snapshot engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semanticrun.models.artifacts import FileTreeSnapshot, ModelIdRef, ToolSchemaRef
from semanticrun.models.checkpoint import Checkpoint
from semanticrun.models.state import AgentState, ModelContext, RunStatus
from semanticrun.policies.mapping import PolicyMapping

if TYPE_CHECKING:
    from semanticrun.audit.log import AuditLog
    from semanticrun.storage.base import StorageBackend


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
        model_id: ModelIdRef,
        tool_schemas: dict[str, ToolSchemaRef],
        file_tree: FileTreeSnapshot | None,
        policy_mapping: PolicyMapping,
    ) -> Checkpoint:
        summary = build_summary(state, status)
        checkpoint = Checkpoint.from_run_state(
            run_id=run_id,
            status=status,
            state=state,
            model_context=model_context,
            model_id=model_id,
            tool_schemas=tool_schemas,
            file_tree=file_tree,
            policy_mapping_json=policy_mapping.as_dict(),
            summary_text=summary,
        )
        saved = self._storage.save_checkpoint(checkpoint)
        self._audit.emit(
            run_id,
            "checkpoint_created",
            {
                "checkpoint_id": saved.id,
                "status": status.value,
            },
        )
        return saved
