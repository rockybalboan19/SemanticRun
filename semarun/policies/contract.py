"""Policy hook contract and registry."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field

from semarun.models.artifacts import ResumeArtifacts
from semarun.models.checkpoint import Checkpoint
from semarun.models.divergence import DivergenceMatrix


class PolicyOutcome(BaseModel):
    action: str
    hook_name: str
    flag: str
    payload: dict[str, Any] = Field(default_factory=dict)
    message: str = ""


class PolicyContext(BaseModel):
    run_id: str
    flag: str
    matrix: DivergenceMatrix
    checkpoint: Checkpoint
    current: ResumeArtifacts = Field(default_factory=ResumeArtifacts)
    last_green_checkpoint_id: str | None = None
    revalidation_template: str = ""
    assertions: list[str] = Field(default_factory=list)


class PolicyHook(Protocol):
    name: str

    def execute(self, ctx: PolicyContext) -> PolicyOutcome: ...


class PolicyRegistry:
    def __init__(self) -> None:
        self._hooks: dict[str, PolicyHook] = {}

    def register(self, hook: PolicyHook) -> None:
        self._hooks[hook.name] = hook

    def get(self, name: str) -> PolicyHook | None:
        return self._hooks.get(name)

    def names(self) -> list[str]:
        return list(self._hooks.keys())
