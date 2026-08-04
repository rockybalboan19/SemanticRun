"""User-declared matrix flag to policy hook mapping."""

from __future__ import annotations

from pydantic import BaseModel, field_validator


DEFAULT_POLICY_MAPPING: dict[str, str] = {
    "tool_schema_changed": "RevalidateWithPrompt",
    "tool_result_hash_mismatch": "RevalidateWithPrompt",
    "file_tree_hash_mismatch": "FailFast",
    "model_id_changed": "FailFast",
    "intent_string_changed": "StrictReset",
    "plan_sequence_changed": "StrictReset",
    "approval_state_changed": "FailFast",
    "behavioral_drift_flagged": "BehavioralDriftPolicy",
    "outbound_payload_divergence": "FailFast",
}

# Short aliases for launch API (normalized to registered hook names).
HOOK_ALIASES: dict[str, str] = {
    "fail_fast": "FailFast",
    "FailFast": "FailFast",
    "revalidate": "RevalidateWithPrompt",
    "RevalidateWithPrompt": "RevalidateWithPrompt",
    "strict_reset": "StrictReset",
    "StrictReset": "StrictReset",
    "behavioral_drift": "BehavioralDriftPolicy",
    "BehavioralDriftPolicy": "BehavioralDriftPolicy",
}


def normalize_hook_name(name: str) -> str:
    return HOOK_ALIASES.get(name, name)


class PolicyMapping(BaseModel):
    tool_schema_changed: str = "RevalidateWithPrompt"
    tool_result_hash_mismatch: str = "RevalidateWithPrompt"
    file_tree_hash_mismatch: str = "FailFast"
    model_id_changed: str = "FailFast"
    intent_string_changed: str = "StrictReset"
    plan_sequence_changed: str = "StrictReset"
    approval_state_changed: str = "FailFast"
    behavioral_drift_flagged: str = "BehavioralDriftPolicy"
    outbound_payload_divergence: str = "FailFast"

    @field_validator(
        "tool_schema_changed",
        "tool_result_hash_mismatch",
        "file_tree_hash_mismatch",
        "model_id_changed",
        "intent_string_changed",
        "plan_sequence_changed",
        "approval_state_changed",
        "behavioral_drift_flagged",
        "outbound_payload_divergence",
        mode="before",
    )
    @classmethod
    def _normalize_hooks(cls, value: object) -> object:
        if isinstance(value, str):
            return normalize_hook_name(value)
        return value

    def hook_for_flag(self, flag: str) -> str | None:
        raw = getattr(self, flag, None)
        if raw is None:
            return None
        return normalize_hook_name(raw)

    def as_dict(self) -> dict[str, str]:
        return {k: normalize_hook_name(v) for k, v in self.model_dump().items()}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> PolicyMapping:
        normalized = {k: normalize_hook_name(v) for k, v in data.items()}
        return cls.model_validate({**DEFAULT_POLICY_MAPPING, **normalized})
