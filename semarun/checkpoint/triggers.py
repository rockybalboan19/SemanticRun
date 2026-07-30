"""Checkpoint trigger rules."""

from __future__ import annotations

from enum import Enum


class CheckpointTrigger(str, Enum):
    TOOL_BOUNDARY = "tool_boundary"
    APPROVAL_GATE = "approval_gate"
    MANUAL = "manual"
    PERIODIC = "periodic"


def should_checkpoint(
    trigger: CheckpointTrigger | str,
    step_type: str | None = None,
    step_count: int = 0,
    periodic_interval: int = 0,
) -> bool:
    t = CheckpointTrigger(trigger) if isinstance(trigger, str) else trigger
    if t == CheckpointTrigger.MANUAL:
        return True
    if t == CheckpointTrigger.TOOL_BOUNDARY:
        return step_type == "tool_call"
    if t == CheckpointTrigger.APPROVAL_GATE:
        return step_type == "human_approval"
    if t == CheckpointTrigger.PERIODIC:
        return periodic_interval > 0 and step_count % periodic_interval == 0
    return False
