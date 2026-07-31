"""Side-effect ledger for mechanical execution truth."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Any

from semarun.checkpoint.hashing import hash_outbound_request, hash_tool_result
from semarun.kernel.skip_rules import ActionClass, classify_action
from semarun.models.state import SideEffectClass, SideEffectRecord

if TYPE_CHECKING:
    from semarun.audit.log import AuditLog
    from semarun.storage.base import StorageBackend


class ReplayVerdict(str, Enum):
    PERMIT = "permit"
    FLAG_DIVERGENCE = "flag_divergence"
    NO_PRIOR_RECORD = "no_prior_record"


class SideEffectLedger:
    def __init__(
        self,
        storage: StorageBackend,
        audit: AuditLog | None = None,
    ) -> None:
        self._storage = storage
        self._audit = audit

    def record(
        self,
        run_id: str,
        step_id: str,
        kind: str,
        target: str,
        payload_hash: str = "",
        schema_hash: str = "",
        outbound_request_hash: str = "",
        side_effect_class: str = SideEffectClass.READ_ONLY.value,
        replay_permitted: bool = True,
    ) -> SideEffectRecord:
        record = SideEffectRecord(
            run_id=run_id,
            step_id=step_id,
            kind=kind,
            target=target,
            payload_hash=payload_hash,
            schema_hash=schema_hash,
            outbound_request_hash=outbound_request_hash,
            side_effect_class=side_effect_class,
            replay_permitted=replay_permitted,
        )
        return self._storage.append_side_effect(record)

    def record_tool_side_effect(
        self,
        run_id: str,
        step_id: str,
        tool_name: str,
        result: Any,
        *,
        outbound_request: Any = None,
        hash_exclude: list[str] | None = None,
        schema_hash: str = "",
        tool_args: Any = None,
        explicit_side_effect: str | None = None,
    ) -> SideEffectRecord:
        action_class = classify_action(
            tool_name, tool_args, explicit_side_effect=explicit_side_effect
        )
        if explicit_side_effect in ("filesystem", "process", "external"):
            side_class = explicit_side_effect
        elif action_class == ActionClass.READ_ONLY:
            side_class = SideEffectClass.READ_ONLY.value
        else:
            side_class = SideEffectClass.EXTERNAL.value

        outbound_hash = ""
        if outbound_request is not None:
            outbound_hash = hash_outbound_request(outbound_request)

        return self.record(
            run_id=run_id,
            step_id=step_id,
            kind="tool_call",
            target=tool_name,
            payload_hash=hash_tool_result(result, hash_exclude),
            schema_hash=schema_hash,
            outbound_request_hash=outbound_hash,
            side_effect_class=side_class,
        )

    def list_for_run(self, run_id: str) -> list[SideEffectRecord]:
        return self._storage.list_side_effects(run_id)

    def list_for_step(self, run_id: str, step_id: str) -> list[SideEffectRecord]:
        return self._storage.list_side_effects(run_id, step_id=step_id)

    def list_recovery_relevant(self, run_id: str) -> list[SideEffectRecord]:
        return [
            r
            for r in self.list_for_run(run_id)
            if r.side_effect_class != SideEffectClass.READ_ONLY.value
        ]

    def compare_outbound(
        self,
        run_id: str,
        target: str,
        outbound_payload: Any,
    ) -> bool:
        """Return True if outbound payload diverges from last committed record."""
        prior = self._storage.get_latest_side_effect_for_target(run_id, target)
        if prior is None or not prior.outbound_request_hash:
            return False
        current_hash = hash_outbound_request(outbound_payload)
        return current_hash != prior.outbound_request_hash

    def permit_replay(
        self,
        run_id: str,
        target: str,
        outbound_payload: Any,
    ) -> ReplayVerdict:
        """ACRFence: compare outbound payload hash vs last committed record."""
        prior = self._storage.get_latest_side_effect_for_target(run_id, target)
        if prior is None or not prior.outbound_request_hash:
            return ReplayVerdict.NO_PRIOR_RECORD

        if not self.compare_outbound(run_id, target, outbound_payload):
            return ReplayVerdict.PERMIT

        prior.replay_permitted = False
        self._storage.update_side_effect(prior)
        if self._audit:
            self._audit.emit(
                run_id,
                "outbound_payload_divergence",
                {
                    "target": target,
                    "expected_hash": prior.outbound_request_hash,
                    "actual_hash": hash_outbound_request(outbound_payload),
                },
            )
        return ReplayVerdict.FLAG_DIVERGENCE

    def check_outbound_divergence(
        self,
        run_id: str,
        outbound_payloads: dict[str, Any],
    ) -> dict[str, bool]:
        """Build outbound divergence map for resume artifacts (read-only)."""
        divergent: dict[str, bool] = {}
        for target, payload in outbound_payloads.items():
            if self.compare_outbound(run_id, target, payload):
                divergent[target] = True
        return divergent
