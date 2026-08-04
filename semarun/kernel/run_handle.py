"""Thin run lifecycle handle - delegates diff and policy routing."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semarun.checkpoint.engine import CheckpointEngine
from semarun.checkpoint.triggers import CheckpointTrigger, should_checkpoint
from semarun.kernel.divergence_matrix import build_divergence_matrix
from semarun.kernel.ledger import SideEffectLedger
from semarun.kernel.step_context import PlanStepHandle, StepContext
from semarun.models.artifacts import ResumeArtifacts, ToolSchemaRef
from semarun.models.checkpoint import Checkpoint
from semarun.models.divergence import DivergenceMatrix
from semarun.models.state import (
    AgentState,
    ApprovalState,
    ApprovalStatus,
    FailureRecord,
    GreenCheckpointRef,
    ModelContext,
    PendingAction,
    RunRecord,
    RunStatus,
    StepType,
    new_id,
)
from semarun.policies.contract import PolicyOutcome, PolicyRegistry
from semarun.policies.errors import PolicyAbort
from semarun.policies.mapping import PolicyMapping
from semarun.resume.router import PolicyRouter


class RunHandle:
    def __init__(
        self,
        run: RunRecord,
        state: AgentState,
        policy_mapping: PolicyMapping,
        checkpoint_engine: CheckpointEngine,
        policy_router: PolicyRouter,
        ledger: SideEffectLedger,
        storage: Any,
        audit: Any,
        registry: PolicyRegistry,
        periodic_interval: int = 0,
        revalidation_template: str = "",
        assertions: list[str] | None = None,
        checkpoint_worker: Any = None,
        tool_schemas: dict[str, ToolSchemaRef] | None = None,
        file_tree: Any = None,
        pending_revalidations: list[dict[str, Any]] | None = None,
    ) -> None:
        self._run = run
        self._state = state
        self._policy_mapping = policy_mapping
        self._checkpoint_engine = checkpoint_engine
        self._policy_router = policy_router
        self._ledger = ledger
        self._storage = storage
        self._audit = audit
        self._registry = registry
        self._periodic_interval = periodic_interval
        self._revalidation_template = revalidation_template
        self._assertions = list(assertions or [])
        self._checkpoint_worker = checkpoint_worker
        self._active_step: StepContext | None = None
        self._tool_schemas: dict[str, ToolSchemaRef] = dict(tool_schemas or {})
        self._file_tree = file_tree
        self._resume_artifacts: ResumeArtifacts | None = None
        self._pending_revalidations: list[dict[str, Any]] = list(
            pending_revalidations or []
        )
        self._last_policy_outcomes: list[PolicyOutcome] = []

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
    def policy_mapping(self) -> PolicyMapping:
        return self._policy_mapping

    @property
    def model_context(self) -> ModelContext:
        return self._run.model_context

    @property
    def plan_index(self) -> int:
        return self._state.plan_index

    @property
    def completed_steps(self) -> list[str]:
        return list(self._state.completed_steps)

    @property
    def pending_revalidations(self) -> list[dict[str, Any]]:
        return list(self._pending_revalidations)

    @property
    def last_policy_outcomes(self) -> list[PolicyOutcome]:
        return list(self._last_policy_outcomes)

    @property
    def tool_schemas(self) -> dict[str, ToolSchemaRef]:
        return dict(self._tool_schemas)

    def _sync_run(self) -> None:
        self._storage.update_run(self._run)

    def hydrate_from_checkpoint(self, checkpoint: Checkpoint) -> None:
        """Restore handle-local artifacts that live outside AgentState."""
        self._tool_schemas = dict(checkpoint.tool_schemas)
        self._file_tree = checkpoint.file_tree
        self._run.model_context = checkpoint.model_context.model_copy(deep=True)
        if checkpoint.state.green_checkpoint:
            self._run.last_green_checkpoint_id = (
                checkpoint.state.green_checkpoint.checkpoint_id
            )

    def step(
        self,
        step_type: str | StepType,
        name: str = "",
        **metadata: Any,
    ) -> StepContext:
        st = StepType(step_type) if isinstance(step_type, str) else step_type
        if "model" in metadata and metadata["model"]:
            parts = str(metadata["model"]).split("-", 1)
            self._run.model_context.model_family = parts[0]
            self._run.model_context.model_version = str(metadata["model"])
        return StepContext(self, st, name=name, metadata=metadata)

    def steps(self) -> Iterator[PlanStepHandle]:
        """Yield incomplete plan steps; completed cursor entries are skipped."""
        plan = list(self._state.plan)
        while self._state.plan_index < len(plan):
            if self._run.status in (RunStatus.ABORTED, RunStatus.COMPLETED):
                return
            if self._run.status == RunStatus.WAITING_APPROVAL:
                return
            idx = self._state.plan_index
            name = plan[idx]
            if name in self._state.completed_steps:
                self._state.plan_index = idx + 1
                continue
            handle = PlanStepHandle(self, name, idx)
            yield handle
            if self._run.status in (
                RunStatus.ABORTED,
                RunStatus.WAITING_APPROVAL,
                RunStatus.PAUSED,
            ):
                return
            if handle._failed:
                return
            if not handle._completed:
                handle.complete()

    def advance_cursor(self, step_name: str, index: int) -> None:
        if step_name not in self._state.completed_steps:
            self._state.completed_steps.append(step_name)
        self._state.plan_index = max(self._state.plan_index, index + 1)
        self.checkpoint(trigger=CheckpointTrigger.MANUAL)
        self._audit.emit(
            self._run.id,
            "cursor_advanced",
            {"step": step_name, "plan_index": self._state.plan_index},
        )

    def record_step_failure(self, step_name: str, error: str) -> None:
        self._state.failure_history.append(
            FailureRecord(step_name=step_name, error=error)
        )
        self._sync_run()

    def clear_revalidation(self, tool_name: str) -> None:
        self._pending_revalidations = [
            item
            for item in self._pending_revalidations
            if tool_name not in item.get("tools_to_revalidate", [])
        ]

    def _begin_step(self, ctx: StepContext) -> str:
        self._active_step = ctx
        step_id = new_id("step")
        self._run.current_step_id = step_id
        self._audit.emit(
            self._run.id,
            "step_started",
            {"step_id": step_id, "step_type": ctx.step_type.value, "name": ctx.name},
        )
        return step_id

    def _end_step(
        self,
        ctx: StepContext,
        failed: bool,
        *,
        memory_mutated: bool = False,
    ) -> None:
        self._run.step_count += 1
        self._audit.emit(
            self._run.id,
            "step_failed" if failed else "step_completed",
            {"step_type": ctx.step_type.value, "name": ctx.name},
        )
        self._active_step = None
        self._run.current_step_id = None
        self._sync_run()
        if failed:
            return
        tool_name = ctx.name or ctx.metadata.get("tool", "")
        if ctx.step_type == StepType.TOOL_CALL and should_checkpoint(
            CheckpointTrigger.SIDE_EFFECT_BOUNDARY,
            step_type=ctx.step_type.value,
            step_count=self._run.step_count,
            periodic_interval=self._periodic_interval,
            tool_name=tool_name,
            tool_args=ctx.tool_args,
            explicit_side_effect=ctx.explicit_side_effect,
            recovery_relevant=ctx.recovery_relevant,
        ):
            self._enqueue_checkpoint(CheckpointTrigger.SIDE_EFFECT_BOUNDARY)
        elif ctx.step_type == StepType.LLM_CALL or memory_mutated:
            self._enqueue_checkpoint(CheckpointTrigger.MANUAL)
        elif should_checkpoint(
            CheckpointTrigger.PERIODIC,
            step_count=self._run.step_count,
            periodic_interval=self._periodic_interval,
        ):
            self._enqueue_checkpoint(CheckpointTrigger.PERIODIC)

    def _enqueue_checkpoint(self, trigger: CheckpointTrigger) -> None:
        if self._checkpoint_worker is not None:
            # Async path: enqueue then wait for durability before returning.
            self._checkpoint_worker.enqueue(
                self._run.id,
                lambda t=trigger: self.checkpoint(trigger=t),
            )
            self._checkpoint_worker.drain()
        else:
            self.checkpoint(trigger=trigger)

    def set_file_tree(self, snapshot: Any) -> None:
        self._file_tree = snapshot

    def set_resume_artifacts(self, artifacts: ResumeArtifacts) -> None:
        self._resume_artifacts = artifacts

    def mark_green_checkpoint(self, checkpoint_id: str | None = None) -> None:
        ckpt_id = checkpoint_id or self._run.latest_checkpoint_id
        if ckpt_id is None:
            raise RuntimeError("No checkpoint to mark green")
        self._state.green_checkpoint = GreenCheckpointRef(checkpoint_id=ckpt_id)
        self._run.last_green_checkpoint_id = ckpt_id
        self._sync_run()

    def checkpoint(self, trigger: str | CheckpointTrigger = CheckpointTrigger.MANUAL) -> Checkpoint:
        from semarun.kernel.artifact_diff import model_context_to_ref

        model_id = model_context_to_ref(
            self._run.model_context.model_family,
            self._run.model_context.model_version,
        )
        ckpt = self._checkpoint_engine.create_checkpoint(
            run_id=self._run.id,
            status=self._run.status,
            state=self._state,
            model_context=self._run.model_context,
            model_id=model_id,
            tool_schemas=self._tool_schemas,
            file_tree=self._file_tree,
            policy_mapping=self._policy_mapping,
        )
        self._run.latest_checkpoint_id = ckpt.id
        self._sync_run()
        return ckpt

    def pause(self) -> None:
        self._run.status = RunStatus.PAUSED
        self.checkpoint(trigger=CheckpointTrigger.MANUAL)
        self._audit.emit(self._run.id, "run_paused", {})
        self._sync_run()

    def request_approval(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self._state.approval_state = ApprovalState(action=action, payload=payload or {})
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
        self._audit.emit(
            self._run.id,
            "approval_granted",
            {"action": self._state.approval_state.action},
        )
        self.checkpoint(trigger=CheckpointTrigger.APPROVAL_GATE)
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
        self._audit.emit(
            self._run.id,
            "approval_rejected",
            {"action": self._state.approval_state.action},
        )
        self.checkpoint(trigger=CheckpointTrigger.APPROVAL_GATE)
        self._sync_run()

    def compute_divergence_matrix(
        self,
        current: ResumeArtifacts | None = None,
    ) -> DivergenceMatrix:
        checkpoint = self._storage.get_latest_checkpoint(self._run.id)
        if checkpoint is None:
            return DivergenceMatrix()
        artifacts = current or self._resume_artifacts or ResumeArtifacts()
        matrix = build_divergence_matrix(checkpoint, artifacts)
        if artifacts.outbound_payloads:
            matrix.outbound_payload_divergence = self._ledger.check_outbound_divergence(
                self._run.id, artifacts.outbound_payloads
            )
            if matrix.outbound_payload_divergence:
                matrix.deltas["outbound_payloads"] = matrix.outbound_payload_divergence
        return matrix

    def route_policies(
        self,
        matrix: DivergenceMatrix | None = None,
        current: ResumeArtifacts | None = None,
    ) -> list[PolicyOutcome]:
        checkpoint = self._storage.get_latest_checkpoint(self._run.id)
        if checkpoint is None:
            return [
                PolicyOutcome(
                    action="continue",
                    hook_name="none",
                    flag="none",
                    message="No checkpoint",
                )
            ]
        if matrix is None:
            matrix = self.compute_divergence_matrix(current)
        return self._policy_router.route(
            run_id=self._run.id,
            matrix=matrix,
            checkpoint=checkpoint,
            mapping=self._policy_mapping,
            current=current or self._resume_artifacts,
            last_green_checkpoint_id=self._run.last_green_checkpoint_id,
            revalidation_template=self._revalidation_template,
            assertions=self._assertions,
        )

    def apply_policies(
        self,
        matrix: DivergenceMatrix | None = None,
        current: ResumeArtifacts | None = None,
    ) -> list[PolicyOutcome]:
        """Route policies and enforce abort / strict reset / revalidate in-environment."""
        outcomes = self.route_policies(matrix=matrix, current=current)
        self._last_policy_outcomes = outcomes
        for outcome in outcomes:
            if outcome.action == "abort":
                self._run.status = RunStatus.ABORTED
                self.checkpoint()
                self._audit.emit(
                    self._run.id,
                    "policy_enforced_abort",
                    outcome.model_dump(mode="json"),
                )
                self._sync_run()
                raise PolicyAbort(outcome)
            if outcome.action == "load_checkpoint":
                ckpt_id = outcome.payload.get("checkpoint_id")
                if ckpt_id:
                    self._load_green_checkpoint(str(ckpt_id))
            if outcome.action == "run_assertions":
                self._pending_revalidations.append(dict(outcome.payload))
            if outcome.action == "halt_for_human":
                self._run.status = RunStatus.PAUSED
                self.checkpoint()
                self._sync_run()
        return outcomes

    def _load_green_checkpoint(self, checkpoint_id: str) -> None:
        ckpt = self._storage.get_checkpoint(checkpoint_id)
        if ckpt is None:
            raise RuntimeError(f"Green checkpoint not found: {checkpoint_id}")
        self._state = ckpt.state.model_copy(deep=True)
        self.hydrate_from_checkpoint(ckpt)
        self._run.latest_checkpoint_id = ckpt.id
        self._run.status = RunStatus.RUNNING
        self._audit.emit(
            self._run.id,
            "strict_reset_applied",
            {"checkpoint_id": checkpoint_id},
        )
        self._sync_run()

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
        if self._run.status == RunStatus.PAUSED:
            self._run.status = RunStatus.RUNNING
        self._audit.emit(self._run.id, "run_resumed", {})
        self._sync_run()
