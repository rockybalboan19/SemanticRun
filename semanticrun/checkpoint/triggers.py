"""Checkpoint trigger rules."""

from __future__ import annotations

from enum import Enum

from semanticrun.kernel.skip_rules import ActionClass, classify_action


class CheckpointTrigger(str, Enum):
    TOOL_BOUNDARY = "tool_boundary"
    SIDE_EFFECT_BOUNDARY = "side_effect_boundary"
    APPROVAL_GATE = "approval_gate"
    MANUAL = "manual"
    PERIODIC = "periodic"


def should_checkpoint(
    trigger: CheckpointTrigger | str,
    step_type: str | None = None,
    step_count: int = 0,
    periodic_interval: int = 0,
    *,
    tool_name: str = "",
    tool_args: object = None,
    explicit_side_effect: str | None = None,
    recovery_relevant: bool | None = None,
) -> bool:
    t = CheckpointTrigger(trigger) if isinstance(trigger, str) else trigger
    if t == CheckpointTrigger.MANUAL:
        return True
    if t == CheckpointTrigger.TOOL_BOUNDARY:
        return step_type == "tool_call"
    if t == CheckpointTrigger.SIDE_EFFECT_BOUNDARY:
        if step_type != "tool_call":
            return False
        if recovery_relevant is not None:
            return recovery_relevant
        if explicit_side_effect in ("filesystem", "process", "external"):
            return True
        if tool_name:
            return classify_action(
                tool_name, tool_args, explicit_side_effect=explicit_side_effect
            ) == ActionClass.RECOVERY_RELEVANT
        return False
    if t == CheckpointTrigger.APPROVAL_GATE:
        return step_type == "human_approval"
    if t == CheckpointTrigger.PERIODIC:
        return periodic_interval > 0 and step_count % periodic_interval == 0
    return False
