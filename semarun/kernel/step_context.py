"""Step context manager - records side effects to ledger."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from semarun.kernel.artifact_diff import hash_schema
from semarun.models.artifacts import ToolSchemaRef
from semarun.models.state import StepType, ToolResultCommitment


class StepContext:
    def __init__(
        self,
        handle: Any,
        step_type: StepType,
        name: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self._handle = handle
        self.step_type = step_type
        self.name = name
        self.metadata = metadata or {}
        self.step_id: str | None = None
        self.tool_args: Any = None
        self.explicit_side_effect: str | None = None
        self.recovery_relevant: bool = False

    def set_tool_result(
        self,
        tool_name: str,
        result: Any,
        hash_exclude: list[str] | None = None,
        canonicalizer: Callable[[Any], Any] | None = None,
        schema: dict[str, Any] | str | None = None,
        outbound_request: Any = None,
        tool_args: Any = None,
        explicit_side_effect: str | None = None,
    ) -> None:
        from semarun.checkpoint.hashing import hash_tool_result
        from semarun.kernel.skip_rules import ActionClass, classify_action

        self.tool_args = tool_args if tool_args is not None else self.metadata.get("args")
        self.explicit_side_effect = explicit_side_effect
        action_class = classify_action(
            tool_name,
            self.tool_args,
            explicit_side_effect=explicit_side_effect,
        )
        self.recovery_relevant = (
            explicit_side_effect in ("filesystem", "process", "external")
            or action_class == ActionClass.RECOVERY_RELEVANT
        )

        schema_hash = hash_schema(schema) if schema is not None else ""
        result_hash = hash_tool_result(result, hash_exclude, canonicalizer)
        commitment = ToolResultCommitment(
            tool_name=tool_name,
            schema_hash=schema_hash,
            result_hash=result_hash,
            hash_exclude=list(hash_exclude or []),
            step_id=self.step_id or "",
            raw_result=result,
        )
        self._handle.state.tool_commitments[tool_name] = commitment
        if schema is not None:
            self._handle._tool_schemas[tool_name] = ToolSchemaRef(
                tool_name=tool_name,
                schema_hash=schema_hash,
            )
        if self.step_id:
            self._handle._ledger.record_tool_side_effect(
                run_id=self._handle.id,
                step_id=self.step_id,
                tool_name=tool_name,
                result=result,
                outbound_request=outbound_request,
                hash_exclude=hash_exclude,
                schema_hash=schema_hash,
                tool_args=self.tool_args,
                explicit_side_effect=explicit_side_effect,
            )

    def __enter__(self) -> StepContext:
        self.step_id = self._handle._begin_step(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._handle._end_step(self, exc_type is not None)
