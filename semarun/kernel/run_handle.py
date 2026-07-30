"""Step context manager and run handle."""

from __future__ import annotations

from collections.abc import Callable, Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semarun.checkpoint.engine import CheckpointEngine
from semarun.checkpoint.hashing import hash_tool_result
from semarun.checkpoint.triggers import CheckpointTrigger, should_checkpoint
from semarun.models.policy import (
    ContinuationPolicy,
    ContinuationResult,
    DivergenceReport,
    ResumeMode,
)
from semarun.models.state import (
    AgentState,
    ApprovalState,
    ApprovalStatus,
    ModelContext,
    PendingAction,
    RunRecord,
    RunStatus,
    StepType,
    ToolResultRef,
)
from semarun.resume.engine import ResumeEngine


class StateMutationError(RuntimeError):
    pass


class StepContext:
    def __init__(
        self,
        handle: RunHandle,
        step_type: StepType,
        name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._handle = handle
        self.step_type = step_type
        self.name = name
        self.metadata = metadata or {}
        self._tool_results: dict[str, tuple[Any, ToolResultRef]] = {}

    def set_tool_result(
        self,
        tool_name: str,
        result: Any,
        hash_exclude: list[str] | None = None,
        canonicalizer: Callable[[Any], Any] | None = None,
    ) -> None:
        ref = ToolResultRef(
            status="success",
            result_hash=hash_tool_result(result, hash_exclude, canonicalizer),
            hash_exclude=list(hash_exclude or []),
            raw_result=result,
        )
        self._tool_results[tool_name] = (result, ref)
        self._handle.state.tool_commitments[tool_name] = ref

    def __enter__(self) -> StepContext:
        self._handle._begin_step(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._handle._end_step(self, exc_type is not None)


class RunHandle:
    def __init__(
        self,
        run: RunRecord,
        state: AgentState,
        policy: ContinuationPolicy,
        checkpoint_engine: CheckpointEngine,
        resume_engine: ResumeEngine,
        storage: Any,
        audit: Any,
        periodic_interval: int = 0,
    ) -> None:
        self._run = run
        self._state = state
        self._policy = policy
        self._checkpoint_engine = checkpoint_engine
        self._resume_engine = resume_engine
        self._storage = storage
        self._audit = audit
        self._periodic_interval = periodic_interval
        self._active_step: StepContext | None = None
        self._last_checkpoint = None
        self._resume_context: dict[str, Any] = {}

    @property
    def id(self) -> str:
        return self._run.id

    @property
    def state(self) -> AgentState:
        return self._state

    @property
    def status(self) -> RunStatus:
        return self._run.status

    @property
    def policy(self) -> ContinuationPolicy:
        return self._policy

    @property
    def model_context(self) -> ModelContext:
        return self._run.model_context

    def _ensure_mutable(self) -> None:
        if self._active_step is None:
            raise StateMutationError("State mutations must occur inside an active step")

    def _sync_run(self) -> None:
        self._storage.update_run(self._run)

    def step(
        self,
        step_type: str | StepType,
        name: str = "",
        **metadata: Any,
    ) -> StepContext:
        st = StepType(step_type) if isinstance(step_type, str) else step_type
        if "model" in metadata:
            parts = str(metadata["model"]).split("-", 1)
            self._run.model_context.model_family = parts[0]
            self._run.model_context.model_version = metadata["model"]
        return StepContext(self, st, name=name, metadata=metadata)

    def _begin_step(self, ctx: StepContext) -> None:
        self._active_step = ctx
        self._audit.emit(
            self._run.id,
            "step_started",
            {"step_type": ctx.step_type.value, "name": ctx.name, "metadata": ctx.metadata},
        )

    def _end_step(self, ctx: StepContext, failed: bool) -> None:
        self._run.step_count += 1
        event = "step_failed" if failed else "step_completed"
        self._audit.emit(
            self._run.id,
            "step_completed" if not failed else "step_failed",
            {"step_type": ctx.step_type.value, "name": ctx.name},
        )
        self._active_step = None
        self._sync_run()
        if not failed and should_checkpoint(
            CheckpointTrigger.TOOL_BOUNDARY,
            step_type=ctx.step_type.value,
            step_count=self._run.step_count,
            periodic_interval=self._periodic_interval,
        ):
            self.checkpoint(trigger=CheckpointTrigger.TOOL_BOUNDARY)
        elif should_checkpoint(
            CheckpointTrigger.PERIODIC,
            step_count=self._run.step_count,
            periodic_interval=self._periodic_interval,
        ):
            self.checkpoint(trigger=CheckpointTrigger.PERIODIC)

    def checkpoint(self, trigger: str | CheckpointTrigger = CheckpointTrigger.MANUAL) -> None:
        ckpt = self._checkpoint_engine.create_checkpoint(
            run_id=self._run.id,
            status=self._run.status,
            state=self._state,
            model_context=self._run.model_context,
            policy=self._policy,
        )
        self._last_checkpoint = ckpt
        self._run.latest_checkpoint_id = ckpt.id
        self._sync_run()

    def pause(self) -> None:
        self._run.status = RunStatus.PAUSED
        self.checkpoint(trigger=CheckpointTrigger.MANUAL)
        self._audit.emit(self._run.id, "run_paused", {})
        self._sync_run()

    def request_approval(self, action: str, payload: dict[str, Any] | None = None) -> None:
        approval = ApprovalState(action=action, payload=payload or {})
        self._state.approval_state = approval
        self._state.pending_actions.append(
            PendingAction(type="human_approval", action=action, payload=payload or {})
        )
        self._run.status = RunStatus.WAITING_APPROVAL
        self._storage.save_approval(
            self._run.id, action, ApprovalStatus.PENDING.value, payload or {}
        )
        self.checkpoint(trigger=CheckpointTrigger.APPROVAL_GATE)
        self._audit.emit(self._run.id, "approval_requested", {"action": action})
        self._sync_run()

    def approve(self) -> None:
        if self._state.approval_state is None:
            raise RuntimeError("No approval pending")
        self._state.approval_state.status = ApprovalStatus.APPROVED
        self._state.approval_state.resolved_at = datetime.now(timezone.utc)
        self._run.status = RunStatus.RUNNING
        approval = self._storage.get_latest_approval(self._run.id)
        if approval:
            self._storage.update_approval(approval["id"], ApprovalStatus.APPROVED.value)
        self._audit.emit(self._run.id, "approval_granted", {"action": self._state.approval_state.action})
        self._sync_run()

    def reject(self) -> None:
        if self._state.approval_state is None:
            raise RuntimeError("No approval pending")
        self._state.approval_state.status = ApprovalStatus.REJECTED
        self._state.approval_state.resolved_at = datetime.now(timezone.utc)
        self._run.status = RunStatus.PAUSED
        approval = self._storage.get_latest_approval(self._run.id)
        if approval:
            self._storage.update_approval(approval["id"], ApprovalStatus.REJECTED.value)
        self._audit.emit(self._run.id, "approval_rejected", {"action": self._state.approval_state.action})
        self.checkpoint(trigger=CheckpointTrigger.APPROVAL_GATE)
        self._sync_run()

    def set_resume_context(self, **kwargs: Any) -> None:
        self._resume_context.update(kwargs)

    def detect_divergence(self, **kwargs: Any) -> DivergenceReport:
        checkpoint = self._storage.get_latest_checkpoint(self._run.id)
        if checkpoint is None:
            return DivergenceReport()
        ctx = {**self._resume_context, **kwargs}
        return self._resume_engine.detector.detect(
            checkpoint,
            current_model=ctx.get("current_model", self._run.model_context),
            current_intent=ctx.get("current_intent"),
            current_plan=ctx.get("current_plan"),
            fresh_tool_results=ctx.get("fresh_tool_results"),
            fresh_facts=ctx.get("fresh_facts"),
        )

    def apply_continuation(self, report: DivergenceReport | None = None) -> ContinuationResult:
        if report is None:
            report = self.detect_divergence()
        return self._resume_engine.apply_continuation(self._run.id, report, self._policy)

    def replan(self, preserve_intent: bool = True) -> AgentState:
        self._state = self._resume_engine.replan(self._state, preserve_intent=preserve_intent)
        return self._state

    def revalidate_stale(self, tools: list[str]) -> list[str]:
        cleared: list[str] = []
        for tool in tools:
            if tool.startswith("fact:"):
                continue
            if tool in self._state.tool_commitments:
                del self._state.tool_commitments[tool]
                cleared.append(tool)
        return cleared

    def complete_step(self, action_name: str) -> None:
        self._state.pending_actions = [
            a for a in self._state.pending_actions if a.action != action_name
        ]

    def export_checkpoint_json(self, path: str | None = None) -> str:
        checkpoint = self._storage.get_latest_checkpoint(self._run.id)
        if checkpoint is None:
            raise RuntimeError("No checkpoint available for export")
        content = checkpoint.model_dump_json(indent=2)
        if path is not None:
            Path(path).write_text(content, encoding="utf-8")
        return content

    def mark_resumed(self) -> None:
        if self._run.status in (RunStatus.PAUSED, RunStatus.WAITING_APPROVAL):
            self._run.status = RunStatus.RUNNING
            self._audit.emit(self._run.id, "run_resumed", {})
            self._sync_run()
