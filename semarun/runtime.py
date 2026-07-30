"""Semarun runtime facade."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from semarun.audit.log import AuditLog
from semarun.checkpoint.engine import CheckpointEngine
from semarun.kernel.run_handle import RunHandle
from semarun.models.policy import ContinuationPolicy
from semarun.models.state import AgentState, ModelContext, RunRecord, RunStatus
from semarun.resume.engine import ResumeEngine
from semarun.storage.sqlite import SQLiteStorage


class SemarunRuntime:
    def __init__(
        self,
        db_path: str = "semarun.db",
        periodic_checkpoint_interval: int = 0,
    ) -> None:
        self._storage = SQLiteStorage(db_path)
        self._audit = AuditLog(self._storage)
        self._checkpoint_engine = CheckpointEngine(
            self._storage,
            self._audit,
            periodic_interval=periodic_checkpoint_interval,
        )
        self._resume_engine = ResumeEngine(self._audit)
        self._periodic_interval = periodic_checkpoint_interval

    @property
    def storage(self) -> SQLiteStorage:
        return self._storage

    @property
    def audit(self) -> AuditLog:
        return self._audit

    def create_run(
        self,
        intent: str,
        plan: list[str] | None = None,
        continuation_policy: ContinuationPolicy | None = None,
        model_context: ModelContext | None = None,
    ) -> RunHandle:
        policy = continuation_policy or ContinuationPolicy()
        run = RunRecord(
            intent=intent,
            status=RunStatus.RUNNING,
            model_context=model_context or ModelContext(),
        )
        self._storage.create_run(run)
        state = AgentState(intent=intent, plan=list(plan or []))
        self._audit.emit(run.id, "run_created", {"intent": intent})
        return RunHandle(
            run=run,
            state=state,
            policy=policy,
            checkpoint_engine=self._checkpoint_engine,
            resume_engine=self._resume_engine,
            storage=self._storage,
            audit=self._audit,
            periodic_interval=self._periodic_interval,
        )

    def resume(self, run_id: str) -> RunHandle:
        run = self._storage.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        checkpoint = self._storage.get_latest_checkpoint(run_id)
        if checkpoint is None:
            raise ValueError(f"No checkpoint found for run: {run_id}")
        policy = ContinuationPolicy.model_validate(checkpoint.continuation_policy_json)
        state = self._resume_engine.reconstruct_state(checkpoint)
        run.status = RunStatus.RUNNING
        self._storage.update_run(run)
        handle = RunHandle(
            run=run,
            state=state,
            policy=policy,
            checkpoint_engine=self._checkpoint_engine,
            resume_engine=self._resume_engine,
            storage=self._storage,
            audit=self._audit,
            periodic_interval=self._periodic_interval,
        )
        handle.mark_resumed()
        return handle

    def complete(self, run: RunHandle) -> None:
        run._run.status = RunStatus.COMPLETED
        run.checkpoint()
        run._audit.emit(run.id, "run_completed", {})
        run._sync_run()

    def abort(self, run: RunHandle, reason: str = "") -> None:
        run._run.status = RunStatus.ABORTED
        run.checkpoint()
        run._audit.emit(run.id, "run_aborted", {"reason": reason})
        run._sync_run()

    def close(self) -> None:
        self._storage.close()

    @classmethod
    def in_memory(cls, periodic_checkpoint_interval: int = 0) -> SemarunRuntime:
        return cls(db_path=":memory:", periodic_checkpoint_interval=periodic_checkpoint_interval)
