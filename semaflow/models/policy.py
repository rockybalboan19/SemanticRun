"""Continuation policy and divergence models."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DivergenceKind(str, Enum):
    BENIGN_CHANGE = "benign_change"
    STALE_EVIDENCE = "stale_evidence"
    TOOL_DRIFT = "tool_drift"
    MODEL_CHANGE = "model_change"
    USER_INSTRUCTION_CHANGE = "user_instruction_change"
    APPROVAL_INVALIDATED = "approval_invalidated"
    SEMANTIC_CONTRADICTION = "semantic_contradiction"
    UNSAFE_BRANCH = "unsafe_branch"


class DivergenceAction(str, Enum):
    RESUME_SILENTLY = "resume_silently"
    REVALIDATE = "revalidate"
    RESUME_WITH_WARNING = "resume_with_warning"
    REPLAN = "replan"
    CONFIRM_WITH_HUMAN = "confirm_with_human"
    BRANCH = "branch"
    ABORT = "abort"


class ResumeMode(str, Enum):
    TRANSPARENT = "transparent"
    REVALIDATED = "revalidated"
    SEMANTIC_REPLAN = "semantic_replan"
    ABORT = "abort"


class ContinuationPolicy(BaseModel):
    on_benign_change: DivergenceAction = DivergenceAction.RESUME_SILENTLY
    on_stale_evidence: DivergenceAction = DivergenceAction.REVALIDATE
    on_tool_drift: DivergenceAction = DivergenceAction.REVALIDATE
    on_model_change: DivergenceAction = DivergenceAction.RESUME_WITH_WARNING
    on_user_change: DivergenceAction = DivergenceAction.REPLAN
    on_approval_invalidated: DivergenceAction = DivergenceAction.REPLAN
    on_semantic_contradiction: DivergenceAction = DivergenceAction.CONFIRM_WITH_HUMAN
    on_unsafe_branch: DivergenceAction = DivergenceAction.ABORT

    def action_for(self, kind: DivergenceKind) -> DivergenceAction:
        mapping = {
            DivergenceKind.BENIGN_CHANGE: self.on_benign_change,
            DivergenceKind.STALE_EVIDENCE: self.on_stale_evidence,
            DivergenceKind.TOOL_DRIFT: self.on_tool_drift,
            DivergenceKind.MODEL_CHANGE: self.on_model_change,
            DivergenceKind.USER_INSTRUCTION_CHANGE: self.on_user_change,
            DivergenceKind.APPROVAL_INVALIDATED: self.on_approval_invalidated,
            DivergenceKind.SEMANTIC_CONTRADICTION: self.on_semantic_contradiction,
            DivergenceKind.UNSAFE_BRANCH: self.on_unsafe_branch,
        }
        return mapping[kind]


class DivergenceEvent(BaseModel):
    kind: DivergenceKind
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class DivergenceReport(BaseModel):
    events: list[DivergenceEvent] = Field(default_factory=list)

    @property
    def has_divergence(self) -> bool:
        return len(self.events) > 0


class ContinuationResult(BaseModel):
    mode: ResumeMode
    action: DivergenceAction
    revalidation_checklist: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    message: str = ""
