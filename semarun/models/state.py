"""Core state and checkpoint models."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class RunStatus(str, Enum):
    RUNNING = "running"
    PAUSED = "paused"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    ABORTED = "aborted"


class StepType(str, Enum):
    LLM_CALL = "llm_call"
    TOOL_CALL = "tool_call"
    HUMAN_APPROVAL = "human_approval"
    RETRIEVAL = "retrieval"
    VALIDATION = "validation"
    REASONING = "reasoning"


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class Fact(BaseModel):
    fact: str
    source: str
    confidence: float = 1.0


class PendingAction(BaseModel):
    type: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ToolResultRef(BaseModel):
    status: str = "success"
    result_hash: str = ""
    hash_exclude: list[str] = Field(default_factory=list)
    raw_result: Any | None = None


class ApprovalState(BaseModel):
    action: str
    status: ApprovalStatus = ApprovalStatus.PENDING
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_at: datetime = Field(default_factory=_utcnow)
    resolved_at: datetime | None = None


class ModelContext(BaseModel):
    model_family: str = ""
    model_version: str = ""


class FailureRecord(BaseModel):
    step_name: str
    error: str
    occurred_at: datetime = Field(default_factory=_utcnow)


class AgentState(BaseModel):
    intent: str
    plan: list[str] = Field(default_factory=list)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    established_facts: list[Fact] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    tool_commitments: dict[str, ToolResultRef] = Field(default_factory=dict)
    approval_state: ApprovalState | None = None
    failure_history: list[FailureRecord] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)


class RunRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    intent: str
    status: RunStatus = RunStatus.RUNNING
    model_context: ModelContext = Field(default_factory=ModelContext)
    continuation_policy_name: str = "default"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    latest_checkpoint_id: str | None = None
    step_count: int = 0


class StepRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    run_id: str
    step_type: StepType
    name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None


class Checkpoint(BaseModel):
    id: str = Field(default_factory=lambda: new_id("ckpt"))
    run_id: str
    intent: str
    status: RunStatus
    model_context: ModelContext = Field(default_factory=ModelContext)
    plan: list[str] = Field(default_factory=list)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    established_facts: list[Fact] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    tool_state: dict[str, ToolResultRef] = Field(default_factory=dict)
    approval_state: ApprovalState | None = None
    failure_history: list[FailureRecord] = Field(default_factory=list)
    risk_flags: list[str] = Field(default_factory=list)
    continuation_policy_json: dict[str, Any] = Field(default_factory=dict)
    summary_text: str = ""
    created_at: datetime = Field(default_factory=_utcnow)

    @classmethod
    def from_agent_state(
        cls,
        run_id: str,
        status: RunStatus,
        state: AgentState,
        model_context: ModelContext,
        continuation_policy_json: dict[str, Any],
        summary_text: str = "",
    ) -> Checkpoint:
        return cls(
            run_id=run_id,
            intent=state.intent,
            status=status,
            model_context=model_context,
            plan=list(state.plan),
            working_memory=dict(state.working_memory),
            established_facts=list(state.established_facts),
            open_questions=list(state.open_questions),
            pending_actions=list(state.pending_actions),
            tool_state=dict(state.tool_commitments),
            approval_state=state.approval_state,
            failure_history=list(state.failure_history),
            risk_flags=list(state.risk_flags),
            continuation_policy_json=continuation_policy_json,
            summary_text=summary_text,
        )

    def to_agent_state(self) -> AgentState:
        return AgentState(
            intent=self.intent,
            plan=list(self.plan),
            working_memory=dict(self.working_memory),
            established_facts=list(self.established_facts),
            open_questions=list(self.open_questions),
            pending_actions=list(self.pending_actions),
            tool_commitments=dict(self.tool_state),
            approval_state=self.approval_state,
            failure_history=list(self.failure_history),
            risk_flags=list(self.risk_flags),
        )
