"""Thin run lifecycle handle - delegates diff and policy routing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from semarun.checkpoint.engine import CheckpointEngine
from semarun.checkpoint.triggers import CheckpointTrigger, should_checkpoint
from semarun.kernel.divergence_matrix import build_divergence_matrix
from semarun.kernel.ledger import SideEffectLedger
from semarun.kernel.step_context import StepContext
from semarun.models.artifacts import ResumeArtifacts
from semarun.models.checkpoint import Checkpoint
from semarun.models.divergence import DivergenceMatrix
from semarun.models.state import (
    AgentState,
    ApprovalState,
    ApprovalStatus,
    GreenCheckpointRef,
    ModelContext,
    PendingAction,
    RunRecord,
    RunStatus,
    StepType,
    new_id,
)
from semarun.policies.contract import PolicyOutcome, PolicyRegistry
from semarun.policies.mapping import PolicyMapping
from semarun.resume.router import PolicyRouter


class StateMutationError(RuntimeError):
    pass


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
        snapshot_index: Any = None,
        daemon_proxy: Any = None,
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
        self._snapshot_index = snapshot_index
        self._daemon_proxy = daemon_proxy
        self._active_step: StepContext | None = None
        self._tool_schemas: dict = {}
        self._file_tree = None
        self._resume_artifacts: ResumeArtifacts | None = None

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
            self._run.model_context.model_version = str(metadata["model"])
        return StepContext(self, st, name=name, metadata=metadata)

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

    def _end_step(self, ctx: StepContext, failed: bool) -> None:
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
        elif should_checkpoint(
            CheckpointTrigger.PERIODIC,
            step_count=self._run.step_count,
            periodic_interval=self._periodic_interval,
        ):
            self._enqueue_checkpoint(CheckpointTrigger.PERIODIC)

    def _enqueue_checkpoint(self, trigger: CheckpointTrigger) -> None:
        if self._checkpoint_worker is not None:
            self._checkpoint_worker.enqueue(
                self._run.id,
                lambda t=trigger: self.checkpoint(trigger=t),
            )
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
