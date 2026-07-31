"""Step context manager - records side effects to ledger."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from semarun.checkpoint.hashing import hash_tool_result
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

    def set_tool_result(
        self,
        tool_name: str,
        result: Any,
        hash_exclude: list[str] | None = None,
        canonicalizer: Callable[[Any], Any] | None = None,
        schema: dict[str, Any] | str | None = None,
        *,
        tool_args: Any = None,
        explicit_side_effect: str | None = None,
        outbound_request: Any | None = None,
    ) -> None:
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
            self._handle._ledger.record(
                run_id=self._handle.id,
                step_id=self.step_id,
                kind="tool_result",
                target=tool_name,
                payload_hash=result_hash,
                schema_hash=schema_hash,
                request_payload=outbound_request,
                tool_args=tool_args if tool_args is not None else self.metadata,
                explicit_side_effect=explicit_side_effect,
            )

    def record_filesystem_effect(
        self,
        path: str,
        operation: str,
        *,
        outbound_request: Any | None = None,
    ) -> None:
        if not self.step_id:
            return
        from semarun.checkpoint.hashing import hash_tool_result

        self._handle._ledger.record(
            run_id=self._handle.id,
            step_id=self.step_id,
            kind="filesystem",
            target=f"{operation}:{path}",
            payload_hash=hash_tool_result({"path": path, "op": operation}),
            request_payload=outbound_request or {"path": path, "operation": operation},
            explicit_side_effect="filesystem",
        )

    def record_process_effect(
        self,
        command: str,
        *,
        outbound_request: Any | None = None,
    ) -> None:
        if not self.step_id:
            return
        from semarun.checkpoint.hashing import hash_tool_result

        self._handle._ledger.record(
            run_id=self._handle.id,
            step_id=self.step_id,
            kind="process",
            target=command,
            payload_hash=hash_tool_result({"command": command}),
            request_payload=outbound_request or {"command": command},
            explicit_side_effect="process",
        )

    def __enter__(self) -> StepContext:
        self.step_id = self._handle._begin_step(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._handle._end_step(self, exc_type is not None)
