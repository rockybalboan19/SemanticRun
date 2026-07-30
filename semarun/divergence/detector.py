"""Divergence detection on resume."""

from __future__ import annotations

from typing import Any

from semarun.checkpoint.hashing import hash_tool_result
from semarun.models.policy import DivergenceEvent, DivergenceKind, DivergenceReport
from semarun.models.state import (
    ApprovalStatus,
    Checkpoint,
    ModelContext,
    ToolResultRef,
)


class DivergenceDetector:
    def detect(
        self,
        checkpoint: Checkpoint,
        *,
        current_model: ModelContext | None = None,
        current_intent: str | None = None,
        current_plan: list[str] | None = None,
        fresh_tool_results: dict[str, Any] | None = None,
        fresh_facts: dict[str, str] | None = None,
    ) -> DivergenceReport:
        events: list[DivergenceEvent] = []

        if checkpoint.risk_flags:
            events.append(
                DivergenceEvent(
                    kind=DivergenceKind.UNSAFE_BRANCH,
                    message="Risk flags present in checkpoint",
                    details={"risk_flags": checkpoint.risk_flags},
                )
            )

        if current_model is not None:
            ckpt_model = checkpoint.model_context
            if (
                ckpt_model.model_family != current_model.model_family
                or ckpt_model.model_version != current_model.model_version
            ):
                events.append(
                    DivergenceEvent(
                        kind=DivergenceKind.MODEL_CHANGE,
                        message="Model context changed since checkpoint",
                        details={
                            "checkpoint": ckpt_model.model_dump(),
                            "current": current_model.model_dump(),
                        },
                    )
                )

        if current_intent is not None and current_intent != checkpoint.intent:
            events.append(
                DivergenceEvent(
                    kind=DivergenceKind.USER_INSTRUCTION_CHANGE,
                    message="Intent changed since checkpoint",
                    details={"checkpoint_intent": checkpoint.intent, "current_intent": current_intent},
                )
            )

        if current_plan is not None and current_plan != checkpoint.plan:
            events.append(
                DivergenceEvent(
                    kind=DivergenceKind.USER_INSTRUCTION_CHANGE,
                    message="Plan changed since checkpoint",
                    details={"checkpoint_plan": checkpoint.plan, "current_plan": current_plan},
                )
            )

        if fresh_tool_results:
            for name, result in fresh_tool_results.items():
                ref = checkpoint.tool_state.get(name)
                if ref is None:
                    continue
                new_hash = self._rehash(result, ref)
                if new_hash != ref.result_hash:
                    events.append(
                        DivergenceEvent(
                            kind=DivergenceKind.TOOL_DRIFT,
                            message=f"Tool result drift detected for '{name}'",
                            details={"tool": name, "expected_hash": ref.result_hash, "actual_hash": new_hash},
                        )
                    )

        if fresh_facts:
            for fact in checkpoint.established_facts:
                if fact.fact in fresh_facts and fresh_facts[fact.fact] != fact.source:
                    events.append(
                        DivergenceEvent(
                            kind=DivergenceKind.STALE_EVIDENCE,
                            message=f"Fact source changed: {fact.fact}",
                            details={"fact": fact.fact, "old_source": fact.source, "new_source": fresh_facts[fact.fact]},
                        )
                    )

        if checkpoint.approval_state is not None:
            if checkpoint.approval_state.status == ApprovalStatus.REJECTED:
                events.append(
                    DivergenceEvent(
                        kind=DivergenceKind.APPROVAL_INVALIDATED,
                        message="Human approval was rejected",
                        details={"action": checkpoint.approval_state.action},
                    )
                )
            elif checkpoint.approval_state.status == ApprovalStatus.PENDING:
                events.append(
                    DivergenceEvent(
                        kind=DivergenceKind.APPROVAL_INVALIDATED,
                        message="Approval still pending",
                        details={"action": checkpoint.approval_state.action},
                    )
                )

        events.extend(self._semantic_contradictions(checkpoint))
        return DivergenceReport(events=events)

    def _rehash(self, result: Any, ref: ToolResultRef) -> str:
        return hash_tool_result(result, hash_exclude=ref.hash_exclude)

    def _semantic_contradictions(self, checkpoint: Checkpoint) -> list[DivergenceEvent]:
        events: list[DivergenceEvent] = []
        fact_texts = {f.fact.lower() for f in checkpoint.established_facts}
        for action in checkpoint.pending_actions:
            if action.action == "send_email" and any("unsubscribed" in f for f in fact_texts):
                events.append(
                    DivergenceEvent(
                        kind=DivergenceKind.SEMANTIC_CONTRADICTION,
                        message="Cannot send email: lead has unsubscribed",
                        details={"pending_action": action.action},
                    )
                )
        return events
