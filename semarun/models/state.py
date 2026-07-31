"""Core state taxonomy - first-class versionable objects."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field

from semarun.checkpoint.hashing import hash_tool_result


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


class PendingAction(BaseModel):
    type: str
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)


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


class ActiveIntent(BaseModel):
    text: str
    plan: list[str] = Field(default_factory=list)
    version: int = 1
    schema_version: str = "1"
    declared_at: datetime = Field(default_factory=_utcnow)

    def with_text(self, text: str, plan: list[str] | None = None) -> ActiveIntent:
        return ActiveIntent(
            text=text,
            plan=list(plan if plan is not None else self.plan),
            version=self.version + 1,
            schema_version=self.schema_version,
            declared_at=_utcnow(),
        )


class MemorySlot(BaseModel):
    schema_ref: str = ""
    content_hash: str = ""
    value: Any = None
    verified_at: datetime = Field(default_factory=_utcnow)
    source_step_id: str = ""


class VerifiedWorkingMemory(BaseModel):
    slots: dict[str, MemorySlot] = Field(default_factory=dict)

    def set_slot(
        self,
        key: str,
        value: Any,
        *,
        step_id: str = "",
        schema_ref: str = "",
    ) -> None:
        self.slots[key] = MemorySlot(
            schema_ref=schema_ref,
            content_hash=hash_tool_result(value),
            value=value,
            verified_at=_utcnow(),
            source_step_id=step_id,
        )

    def get(self, key: str, default: Any = None) -> Any:
        slot = self.slots.get(key)
        return default if slot is None else slot.value

    def __getitem__(self, key: str) -> Any:
        return self.slots[key].value

    def __setitem__(self, key: str, value: Any) -> None:
        self.set_slot(key, value)


class ToolResultCommitment(BaseModel):
    tool_name: str
    schema_hash: str = ""
    result_hash: str = ""
    hash_exclude: list[str] = Field(default_factory=list)
    status: str = "success"
    committed_at: datetime = Field(default_factory=_utcnow)
    step_id: str = ""
    raw_result: Any | None = None


class VerifiedClaim(BaseModel):
    claim: str
    source: str
    content_hash: str
    verified_at: datetime = Field(default_factory=_utcnow)


class GreenCheckpointRef(BaseModel):
    checkpoint_id: str
    marked_at: datetime = Field(default_factory=_utcnow)


class AgentState(BaseModel):
    active_intent: ActiveIntent
    working_memory: VerifiedWorkingMemory = Field(default_factory=VerifiedWorkingMemory)
    tool_commitments: dict[str, ToolResultCommitment] = Field(default_factory=dict)
    verified_claims: list[VerifiedClaim] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    pending_actions: list[PendingAction] = Field(default_factory=list)
    approval_state: ApprovalState | None = None
    failure_history: list[FailureRecord] = Field(default_factory=list)
    green_checkpoint: GreenCheckpointRef | None = None

    @property
    def intent(self) -> str:
        return self.active_intent.text

    @property
    def plan(self) -> list[str]:
        return self.active_intent.plan

    @classmethod
    def create(cls, intent: str, plan: list[str] | None = None) -> AgentState:
        return cls(active_intent=ActiveIntent(text=intent, plan=list(plan or [])))


class RunRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("run"))
    intent: str
    status: RunStatus = RunStatus.RUNNING
    model_context: ModelContext = Field(default_factory=ModelContext)
    policy_mapping_name: str = "default"
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    latest_checkpoint_id: str | None = None
    last_green_checkpoint_id: str | None = None
    step_count: int = 0
    current_step_id: str | None = None


class StepRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("step"))
    run_id: str
    step_type: StepType
    name: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    started_at: datetime = Field(default_factory=_utcnow)
    completed_at: datetime | None = None


class SideEffectRecord(BaseModel):
    id: str = Field(default_factory=lambda: new_id("fx"))
    run_id: str
    step_id: str
    kind: str
    target: str
    payload_hash: str = ""
    schema_hash: str = ""
    request_payload_hash: str = ""
    recovery_relevant: bool = False
    recorded_at: datetime = Field(default_factory=_utcnow)
