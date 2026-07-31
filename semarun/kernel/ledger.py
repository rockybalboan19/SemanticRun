"""Sparse incremental hash-based state ledger (CRAB + ACRFence)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from semarun.checkpoint.hashing import hash_tool_result
from semarun.kernel.skip_rules import ActionClass, classify_action
from semarun.kernel.snapshot_index import SnapshotIndex
from semarun.models.state import SideEffectRecord

if TYPE_CHECKING:
    from semarun.storage.ledger_store import LedgerStorage


class SideEffectKind(str, Enum):
    READ_ONLY = "read_only"
    FILESYSTEM = "filesystem"
    PROCESS = "process"
    EXTERNAL = "external"
    TOOL_RESULT = "tool_result"


@dataclass(frozen=True)
class ReplayAuthorization:
    allowed: bool
    flagged: bool = False
    reason: str = ""
    expected_hash: str = ""
    actual_hash: str = ""


class SideEffectLedger:
    """
    Mechanical execution ledger.

    CRAB: checkpoint only on filesystem/process/external recovery-relevant effects.
    ACRFence: outbound request payload hash compared before replay authorization.
    DeltaBox: incremental blobs via SnapshotIndex (COW + background GC).
    """

    RECOVERY_KINDS = frozenset(
        {SideEffectKind.FILESYSTEM, SideEffectKind.PROCESS, SideEffectKind.EXTERNAL}
    )

    def __init__(
        self,
        storage: LedgerStorage,
        *,
        snapshot_index: SnapshotIndex | None = None,
        start_gc: bool = True,
    ) -> None:
        self._storage = storage
        self._snapshot = snapshot_index or SnapshotIndex(storage, start_gc=start_gc)
        self._step_effects: dict[str, list[SideEffectRecord]] = {}
        self._checkpoint_write_count = 0
        self._turn_count = 0

    @property
    def snapshot_index(self) -> SnapshotIndex:
        return self._snapshot

    @property
    def checkpoint_write_count(self) -> int:
        return self._checkpoint_write_count

    @property
    def turn_count(self) -> int:
        return self._turn_count

    def begin_turn(self) -> None:
        self._turn_count += 1

    def record(
        self,
        run_id: str,
        step_id: str,
        kind: str | SideEffectKind,
        target: str,
        *,
        payload_hash: str = "",
        schema_hash: str = "",
        request_payload: Any | None = None,
        tool_args: Any = None,
        explicit_side_effect: str | None = None,
    ) -> SideEffectRecord:
        effect_kind = SideEffectKind(kind) if isinstance(kind, str) else kind
        if explicit_side_effect == "filesystem":
            effect_kind = SideEffectKind.FILESYSTEM
        elif explicit_side_effect == "process":
            effect_kind = SideEffectKind.PROCESS
        elif explicit_side_effect == "external":
            effect_kind = SideEffectKind.EXTERNAL
        elif effect_kind == SideEffectKind.TOOL_RESULT:
            action_class = classify_action(
                target,
                tool_args,
                explicit_side_effect=explicit_side_effect,
            )
            if action_class == ActionClass.READ_ONLY:
                effect_kind = SideEffectKind.READ_ONLY

        request_hash = ""
        if request_payload is not None:
            request_hash = hash_tool_result(request_payload)

        recovery_relevant = effect_kind in self.RECOVERY_KINDS

        record = SideEffectRecord(
            run_id=run_id,
            step_id=step_id,
            kind=effect_kind.value,
            target=target,
            payload_hash=payload_hash,
            schema_hash=schema_hash,
            request_payload_hash=request_hash,
            recovery_relevant=recovery_relevant,
        )
        saved = self._storage.append_side_effect(record)
        self._step_effects.setdefault(step_id, []).append(saved)
        return saved

    def step_requires_checkpoint(self, step_id: str, run_id: str | None = None) -> bool:
        effects = self._step_effects.get(step_id)
        if effects is None and run_id:
            effects = self._storage.list_side_effects(run_id, step_id=step_id)
        if not effects:
            return False
        return any(e.recovery_relevant for e in effects)

    def commit_incremental_snapshot(
        self, run_id: str, payload: dict[str, Any]
    ) -> tuple[str, str]:
        """Store checkpoint blob incrementally; bump write counter."""
        node_id, content_hash = self._snapshot.store_checkpoint_blob(run_id, payload)
        self._checkpoint_write_count += 1
        return node_id, content_hash

    def authorize_replay(
        self,
        run_id: str,
        target: str,
        outbound_payload: Any,
        *,
        kind: str | None = None,
    ) -> ReplayAuthorization:
        """
        ACRFence: compare outbound request hash to pre-checkpoint version.

        Divergent payloads are flagged for policy layer - never silently replayed.
        """
        prior = self._storage.get_last_outbound(run_id, target, kind=kind)
        actual_hash = hash_tool_result(outbound_payload)
        if prior is None:
            return ReplayAuthorization(
                allowed=False,
                flagged=True,
                reason="no_prior_outbound_record",
                actual_hash=actual_hash,
            )
        if not prior.request_payload_hash:
            return ReplayAuthorization(
                allowed=False,
                flagged=True,
                reason="missing_request_payload_hash",
                actual_hash=actual_hash,
            )
        if actual_hash != prior.request_payload_hash:
            return ReplayAuthorization(
                allowed=False,
                flagged=True,
                reason="payload_divergence",
                expected_hash=prior.request_payload_hash,
                actual_hash=actual_hash,
            )
        return ReplayAuthorization(allowed=True)

    def list_for_run(self, run_id: str) -> list[SideEffectRecord]:
        return self._storage.list_side_effects(run_id)

    def list_for_step(self, run_id: str, step_id: str) -> list[SideEffectRecord]:
        return self._storage.list_side_effects(run_id, step_id=step_id)

    def close(self) -> None:
        self._snapshot.stop()
