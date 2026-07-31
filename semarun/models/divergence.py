"""Deterministic divergence matrix - mechanical booleans only."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DivergenceMatrix(BaseModel):
    tool_schema_changed: bool = False
    tool_result_hash_mismatch: dict[str, bool] = Field(default_factory=dict)
    file_tree_hash_mismatch: bool = False
    model_id_changed: bool = False
    intent_string_changed: bool = False
    plan_sequence_changed: bool = False
    approval_state_changed: bool = False
    behavioral_drift_flagged: bool = False
    deltas: dict[str, Any] = Field(default_factory=dict)

    @property
    def has_divergence(self) -> bool:
        if self.behavioral_drift_flagged:
            return True
        if any(
            (
                self.tool_schema_changed,
                self.file_tree_hash_mismatch,
                self.model_id_changed,
                self.intent_string_changed,
                self.plan_sequence_changed,
                self.approval_state_changed,
            )
        ):
            return True
        return any(self.tool_result_hash_mismatch.values())

    def triggered_flags(self) -> list[str]:
        flags: list[str] = []
        if self.tool_schema_changed:
            flags.append("tool_schema_changed")
        if any(self.tool_result_hash_mismatch.values()):
            flags.append("tool_result_hash_mismatch")
        if self.file_tree_hash_mismatch:
            flags.append("file_tree_hash_mismatch")
        if self.model_id_changed:
            flags.append("model_id_changed")
        if self.intent_string_changed:
            flags.append("intent_string_changed")
        if self.plan_sequence_changed:
            flags.append("plan_sequence_changed")
        if self.approval_state_changed:
            flags.append("approval_state_changed")
        if self.behavioral_drift_flagged:
            flags.append("behavioral_drift_flagged")
        return flags
