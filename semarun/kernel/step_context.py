"""Step context manager - records side effects to ledger."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from semarun.kernel.artifact_diff import hash_schema
from semarun.kernel.ledger import ReplayVerdict
from semarun.models.artifacts import ToolSchemaRef
from semarun.models.state import StepType, ToolResultCommitment
from semarun.policies.errors import PolicyAbort
from semarun.policies.contract import PolicyOutcome


class OutboundDivergenceError(PolicyAbort):
    """Raised when SemanticRun refuses to invoke a divergent outbound side-effect."""

    def __init__(self, tool_name: str) -> None:
        outcome = PolicyOutcome(
            action="abort",
            hook_name="FailFast",
            flag="outbound_payload_divergence",
            payload={"tool": tool_name},
            message=f"Outbound payload divergence for {tool_name}; refusing replay",
        )
        super().__init__(outcome)


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
        self._memory_mutated = False

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
        self._handle.clear_revalidation(tool_name)

    def tool(
        self,
        tool_name: str,
        fn: Callable[[], Any],
        *,
        hash_exclude: list[str] | None = None,
        schema: dict[str, Any] | str | None = None,
        side_effect: str | None = None,
        outbound: Any = None,
        tool_args: Any = None,
    ) -> Any:
        """Invoke a tool callable with ledger commit and outbound replay gate."""
        if outbound is not None and side_effect in ("filesystem", "process", "external"):
            verdict = self._handle._ledger.permit_replay(
                self._handle.id, tool_name, outbound
            )
            if verdict == ReplayVerdict.FLAG_DIVERGENCE:
                raise OutboundDivergenceError(tool_name)

        result = fn()
        self.set_tool_result(
            tool_name,
            result,
            hash_exclude=hash_exclude,
            schema=schema,
            outbound_request=outbound,
            tool_args=tool_args,
            explicit_side_effect=side_effect,
        )
        return result

    def llm(self, fn: Callable[[], Any], *, model: str = "") -> Any:
        """Run an LLM callable inside this step (caller supplies the client)."""
        if model:
            self.metadata["model"] = model
            parts = str(model).split("-", 1)
            self._handle._run.model_context.model_family = parts[0]
            self._handle._run.model_context.model_version = str(model)
        return fn()

    def remember(self, key: str, value: Any, *, schema_ref: str = "") -> None:
        self._handle.state.working_memory.set_slot(
            key,
            value,
            step_id=self.step_id or "",
            schema_ref=schema_ref,
        )
        self._memory_mutated = True

    def require_approval(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self._handle.request_approval(action, payload)

    def __enter__(self) -> StepContext:
        self.step_id = self._handle._begin_step(self)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self._handle._end_step(
            self,
            exc_type is not None,
            memory_mutated=self._memory_mutated,
        )


class PlanStepHandle:
    """One plan step yielded by ``run.steps()`` — skips completed cursor entries."""

    def __init__(self, handle: Any, name: str, index: int) -> None:
        self._handle = handle
        self.name = name
        self.index = index
        self._completed = False
        self._failed = False

    def tool(
        self,
        tool_name: str,
        fn: Callable[[], Any],
        *,
        hash_exclude: list[str] | None = None,
        schema: dict[str, Any] | str | None = None,
        side_effect: str | None = None,
        outbound: Any = None,
        tool_args: Any = None,
    ) -> Any:
        with self._handle.step("tool_call", name=tool_name) as ctx:
            return ctx.tool(
                tool_name,
                fn,
                hash_exclude=hash_exclude,
                schema=schema,
                side_effect=side_effect,
                outbound=outbound,
                tool_args=tool_args,
            )

    def llm(self, fn: Callable[[], Any], *, model: str = "") -> Any:
        with self._handle.step("llm_call", name=self.name, model=model) as ctx:
            return ctx.llm(fn, model=model)

    def remember(self, key: str, value: Any, *, schema_ref: str = "") -> None:
        self._handle.state.working_memory.set_slot(key, value, schema_ref=schema_ref)
        # Durable boundary after memory write outside an open step.
        self._handle.checkpoint()

    def require_approval(self, action: str, payload: dict[str, Any] | None = None) -> None:
        self._handle.request_approval(action, payload)

    def complete(self) -> None:
        if self._completed:
            return
        self._handle.advance_cursor(self.name, self.index)
        self._completed = True

    def fail(self, error: str = "") -> None:
        self._failed = True
        self._handle.record_step_failure(self.name, error)
