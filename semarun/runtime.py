"""SemanticRun environment facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from semarun.audit.log import AuditLog
from semarun.checkpoint.engine import CheckpointEngine
from semarun.kernel.checkpoint_worker import CheckpointWorker
from semarun.kernel.ledger import SideEffectLedger
from semarun.kernel.run_handle import RunHandle
from semarun.models.artifacts import ResumeArtifacts
from semarun.models.state import AgentState, ModelContext, RunRecord, RunStatus
from semarun.policies.behavioral import BehavioralDriftPolicy
from semarun.policies.builtin import FailFast, RevalidateWithPrompt, StrictReset
from semarun.policies.contract import PolicyHook, PolicyRegistry
from semarun.policies.mapping import PolicyMapping
from semarun.resume.router import PolicyRouter
from semarun.storage.sqlite import SQLiteStorage


def _default_registry() -> PolicyRegistry:
    registry = PolicyRegistry()
    for hook in (
        FailFast(),
        RevalidateWithPrompt(),
        StrictReset(),
        BehavioralDriftPolicy(),
    ):
        registry.register(hook)
    return registry


class SemanticRun:
    """Artifact-aware durable agent environment."""

    def __init__(
        self,
        db_path: str = "semarun.db",
        periodic_checkpoint_interval: int = 0,
        policy_mapping: PolicyMapping | None = None,
        policies: dict[str, PolicyHook] | None = None,
        revalidation_template: str = "",
        assertions: list[str] | None = None,
        async_checkpoints: bool = False,
        **_ignored: Any,
    ) -> None:
        self._storage = SQLiteStorage(db_path)
        self._audit = AuditLog(self._storage)
        self._ledger = SideEffectLedger(self._storage, audit=self._audit)
        self._checkpoint_worker = CheckpointWorker() if async_checkpoints else None
        self._checkpoint_engine = CheckpointEngine(
            self._storage,
            self._audit,
            periodic_interval=periodic_checkpoint_interval,
        )
        self._registry = _default_registry()
        if policies:
            for hook in policies.values():
                self._registry.register(hook)
        self._policy_router = PolicyRouter(self._registry, self._audit)
        self._policy_mapping = policy_mapping or PolicyMapping()
        self._periodic_interval = periodic_checkpoint_interval
        self._revalidation_template = revalidation_template
        self._assertions = list(assertions or [])
        self._db_path = db_path

    @classmethod
    def open(cls, db_path: str | Path, **kwargs: Any) -> SemanticRun:
        return cls(db_path=str(db_path), **kwargs)

    @property
    def storage(self) -> SQLiteStorage:
        return self._storage

    @property
    def audit(self) -> AuditLog:
        return self._audit

    @property
    def ledger(self) -> SideEffectLedger:
        return self._ledger

    @property
    def registry(self) -> PolicyRegistry:
        return self._registry

    def _make_handle(
        self,
        run: RunRecord,
        state: AgentState,
        policy_mapping: PolicyMapping | None = None,
    ) -> RunHandle:
        checkpoint_engine = CheckpointEngine(
            self._storage,
            self._audit,
            periodic_interval=self._periodic_interval,
        )
        return RunHandle(
            run=run,
            state=state,
            policy_mapping=policy_mapping or self._policy_mapping,
            checkpoint_engine=checkpoint_engine,
            policy_router=self._policy_router,
            ledger=self._ledger,
            storage=self._storage,
            audit=self._audit,
            registry=self._registry,
            periodic_interval=self._periodic_interval,
            revalidation_template=self._revalidation_template,
            assertions=self._assertions,
            checkpoint_worker=self._checkpoint_worker,
        )

    def start(
        self,
        intent: str,
        plan: list[str] | None = None,
        policies: PolicyMapping | None = None,
        policy_mapping: PolicyMapping | None = None,
        model_context: ModelContext | None = None,
    ) -> RunHandle:
        """Start a new agent run (preferred launch API)."""
        return self.create_run(
            intent=intent,
            plan=plan,
            policy_mapping=policies or policy_mapping,
            model_context=model_context,
        )

    def create_run(
        self,
        intent: str,
        plan: list[str] | None = None,
        policy_mapping: PolicyMapping | None = None,
        model_context: ModelContext | None = None,
    ) -> RunHandle:
        mapping = policy_mapping or self._policy_mapping
        run = RunRecord(
            intent=intent,
            status=RunStatus.RUNNING,
            model_context=model_context or ModelContext(),
        )
        self._storage.create_run(run)
        state = AgentState.create(intent=intent, plan=list(plan or []))
        self._audit.emit(run.id, "run_created", {"intent": intent})
        return self._make_handle(run, state, mapping)

    def resume(
        self,
        run_id: str,
        artifacts: ResumeArtifacts | None = None,
        *,
        enforce_policies: bool = True,
    ) -> RunHandle:
        """Load latest checkpoint, hydrate handle, optionally enforce policies."""
        run = self._storage.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        checkpoint = self._storage.get_latest_checkpoint(run_id)
        if checkpoint is None:
            raise ValueError(f"No checkpoint found for run: {run_id}")
        mapping = PolicyMapping.from_dict(checkpoint.policy_mapping_json)
        state = checkpoint.state.model_copy(deep=True)
        if run.status not in (RunStatus.COMPLETED, RunStatus.ABORTED):
            if (
                run.status == RunStatus.WAITING_APPROVAL
                or checkpoint.status == RunStatus.WAITING_APPROVAL
            ):
                run.status = RunStatus.WAITING_APPROVAL
            else:
                run.status = RunStatus.RUNNING
        self._storage.update_run(run)
        handle = self._make_handle(run, state, mapping)
        handle.hydrate_from_checkpoint(checkpoint)
        if artifacts is not None:
            handle.set_resume_artifacts(artifacts)
        handle.mark_resumed()
        if enforce_policies and artifacts is not None:
            matrix = handle.compute_divergence_matrix(artifacts)
            if matrix.has_divergence:
                handle.apply_policies(matrix, artifacts)
        return handle

    def complete(self, run: RunHandle) -> None:
        run._run.status = RunStatus.COMPLETED
        if self._checkpoint_worker:
            self._checkpoint_worker.drain()
        run.checkpoint()
        run._audit.emit(run.id, "run_completed", {})
        run._sync_run()

    def abort(self, run: RunHandle, reason: str = "") -> None:
        run._run.status = RunStatus.ABORTED
        if self._checkpoint_worker:
            self._checkpoint_worker.drain()
        run.checkpoint()
        run._audit.emit(run.id, "run_aborted", {"reason": reason})
        run._sync_run()

    def close(self) -> None:
        if self._checkpoint_worker:
            self._checkpoint_worker.stop()
        self._storage.close()

    @classmethod
    def in_memory(cls, periodic_checkpoint_interval: int = 0, **kwargs: Any) -> SemanticRun:
        return cls(
            db_path=":memory:",
            periodic_checkpoint_interval=periodic_checkpoint_interval,
            async_checkpoints=False,
            **kwargs,
        )


# Backward-compatible alias (deprecated).
SemarunRuntime = SemanticRun
