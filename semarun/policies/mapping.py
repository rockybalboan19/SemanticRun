"""User-declared matrix flag to policy hook mapping."""

from __future__ import annotations

from pydantic import BaseModel, Field


DEFAULT_POLICY_MAPPING: dict[str, str] = {
    "tool_schema_changed": "RevalidateWithPrompt",
    "tool_result_hash_mismatch": "RevalidateWithPrompt",
    "file_tree_hash_mismatch": "FailFast",
    "model_id_changed": "FailFast",
    "intent_string_changed": "StrictReset",
    "plan_sequence_changed": "StrictReset",
    "approval_state_changed": "FailFast",
    "behavioral_drift_flagged": "BehavioralDriftPolicy",
}


class PolicyMapping(BaseModel):
    tool_schema_changed: str = "RevalidateWithPrompt"
    tool_result_hash_mismatch: str = "RevalidateWithPrompt"
    file_tree_hash_mismatch: str = "FailFast"
    model_id_changed: str = "FailFast"
    intent_string_changed: str = "StrictReset"
    plan_sequence_changed: str = "StrictReset"
    approval_state_changed: str = "FailFast"
    behavioral_drift_flagged: str = "BehavioralDriftPolicy"

    def hook_for_flag(self, flag: str) -> str | None:
        return getattr(self, flag, None)

    def as_dict(self) -> dict[str, str]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> PolicyMapping:
        return cls.model_validate({**DEFAULT_POLICY_MAPPING, **data})
