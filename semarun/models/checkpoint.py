"""Checkpoint snapshot model."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from semarun.models.artifacts import FileTreeSnapshot, ModelIdRef, ToolSchemaRef
from semarun.models.state import (
    AgentState,
    ApprovalState,
    FailureRecord,
    GreenCheckpointRef,
    ModelContext,
    PendingAction,
    RunStatus,
    ToolResultCommitment,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_checkpoint_id() -> str:
    return f"ckpt_{uuid4().hex[:12]}"


class Checkpoint(BaseModel):
    id: str = Field(default_factory=new_checkpoint_id)
    run_id: str
    status: RunStatus
    state: AgentState
    model_context: ModelContext = Field(default_factory=ModelContext)
    model_id: ModelIdRef = Field(default_factory=ModelIdRef)
    tool_schemas: dict[str, ToolSchemaRef] = Field(default_factory=dict)
    file_tree: FileTreeSnapshot | None = None
    policy_mapping_json: dict[str, str] = Field(default_factory=dict)
    summary_text: str = ""
    snapshot_node_id: str | None = None
    created_at: datetime = Field(default_factory=_utcnow)

    @classmethod
    def from_run_state(
        cls,
        run_id: str,
        status: RunStatus,
        state: AgentState,
        model_context: ModelContext,
        model_id: ModelIdRef,
        tool_schemas: dict[str, ToolSchemaRef],
        file_tree: FileTreeSnapshot | None,
        policy_mapping_json: dict[str, str],
        summary_text: str = "",
    ) -> Checkpoint:
        return cls(
            run_id=run_id,
            status=status,
            state=state.model_copy(deep=True),
            model_context=model_context,
            model_id=model_id,
            tool_schemas=dict(tool_schemas),
            file_tree=file_tree,
            policy_mapping_json=dict(policy_mapping_json),
            summary_text=summary_text,
        )

    @property
    def intent(self) -> str:
        return self.state.active_intent.text

    @property
    def plan(self) -> list[str]:
        return list(self.state.active_intent.plan)

    @property
    def tool_state(self) -> dict[str, ToolResultCommitment]:
        return self.state.tool_commitments

    @property
    def working_memory(self) -> dict[str, Any]:
        return {k: slot.value for k, slot in self.state.working_memory.slots.items()}

    @property
    def approval_state(self) -> ApprovalState | None:
        return self.state.approval_state

    @property
    def pending_actions(self) -> list[PendingAction]:
        return self.state.pending_actions

    @property
    def open_questions(self) -> list[str]:
        return self.state.open_questions

    @property
    def failure_history(self) -> list[FailureRecord]:
        return self.state.failure_history

    @property
    def green_checkpoint(self) -> GreenCheckpointRef | None:
        return self.state.green_checkpoint
