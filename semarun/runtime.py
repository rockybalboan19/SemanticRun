"""Semarun runtime facade."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from semarun.audit.log import AuditLog
from semarun.checkpoint.engine import CheckpointEngine
from semarun.kernel.checkpoint_worker import CheckpointWorker
from semarun.kernel.ledger import SideEffectLedger
from semarun.kernel.run_handle import RunHandle
from semarun.kernel.snapshot_index import SnapshotIndex
from semarun.models.state import AgentState, ModelContext, RunRecord, RunStatus
from semarun.policies.behavioral import BehavioralDriftPolicy
from semarun.policies.builtin import FailFast, RevalidateWithPrompt, StrictReset
from semarun.policies.contract import PolicyHook, PolicyRegistry
from semarun.policies.mapping import PolicyMapping
from semarun.resume.router import PolicyRouter
from semarun.storage.ledger_store import SQLiteLedgerStore
from semarun.storage.sqlite import SQLiteStorage

if TYPE_CHECKING:
    from semarun.kernel.runtime import DaemonProxyRuntime


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


class SemarunRuntime:
    def __init__(
        self,
        db_path: str = "semarun.db",
        periodic_checkpoint_interval: int = 0,
        policy_mapping: PolicyMapping | None = None,
        policies: dict[str, PolicyHook] | None = None,
        revalidation_template: str = "",
        assertions: list[str] | None = None,
        ledger_dir: str | Path | None = None,
        daemon_proxy: DaemonProxyRuntime | None = None,
        async_checkpoints: bool = True,
    ) -> None:
        self._storage = SQLiteStorage(db_path)
        self._audit = AuditLog(self._storage)
        self._ledger = SideEffectLedger(self._storage, audit=self._audit)
        self._ledger_dir = Path(ledger_dir) if ledger_dir else Path(tempfile.gettempdir()) / "semarun_ledger"
        self._ledger_stores: dict[str, SQLiteLedgerStore] = {}
        self._checkpoint_worker = CheckpointWorker() if async_checkpoints else None
        self._snapshot_indices: dict[str, SnapshotIndex] = {}
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
        self._daemon_proxy = daemon_proxy

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

    @property
    def daemon_proxy(self) -> DaemonProxyRuntime | None:
        return self._daemon_proxy

    def _ledger_store_for_run(self, run_id: str) -> SQLiteLedgerStore:
        if run_id not in self._ledger_stores:
            self._ledger_stores[run_id] = SQLiteLedgerStore.for_run(self._ledger_dir, run_id)
        return self._ledger_stores[run_id]

    def _snapshot_index_for_run(self, run_id: str) -> SnapshotIndex:
        if run_id not in self._snapshot_indices:
            store = self._ledger_store_for_run(run_id)
            self._snapshot_indices[run_id] = SnapshotIndex(store)
        return self._snapshot_indices[run_id]

    def _make_handle(
        self,
        run: RunRecord,
        state: AgentState,
        policy_mapping: PolicyMapping | None = None,
    ) -> RunHandle:
        snapshot_index = self._snapshot_index_for_run(run.id)
        checkpoint_engine = CheckpointEngine(
            self._storage,
            self._audit,
            periodic_interval=self._periodic_interval,
            snapshot_index=snapshot_index,
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
            snapshot_index=snapshot_index,
            daemon_proxy=self._daemon_proxy,
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

    def resume(self, run_id: str) -> RunHandle:
        run = self._storage.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        checkpoint = self._storage.get_latest_checkpoint(run_id)
        if checkpoint is None:
            raise ValueError(f"No checkpoint found for run: {run_id}")
        mapping = PolicyMapping.from_dict(checkpoint.policy_mapping_json)
        state = checkpoint.state.model_copy(deep=True)
        run.status = RunStatus.RUNNING
        self._storage.update_run(run)
        handle = self._make_handle(run, state, mapping)
        handle.mark_resumed()
        return handle

    def complete(self, run: RunHandle) -> None:
        run._run.status = RunStatus.COMPLETED
        if self._checkpoint_worker:
            self._checkpoint_worker.drain()
        run.checkpoint()
        run._audit.emit(run.id, "run_completed", {})
        run._sync_run()
        if run._snapshot_index and run._run.latest_checkpoint_id:
            ckpt = self._storage.get_checkpoint(run._run.latest_checkpoint_id)
            if ckpt and ckpt.snapshot_node_id:
                run._snapshot_index.unpin(ckpt.snapshot_node_id)

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
        for idx in self._snapshot_indices.values():
            idx.stop()
        for store in self._ledger_stores.values():
            store.close()
        self._storage.close()

    @classmethod
    def in_memory(cls, periodic_checkpoint_interval: int = 0, **kwargs) -> SemarunRuntime:
        return cls(
            db_path=":memory:",
            periodic_checkpoint_interval=periodic_checkpoint_interval,
            async_checkpoints=False,
            **kwargs,
        )
