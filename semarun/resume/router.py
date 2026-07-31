"""Route divergence matrix flags to explicit policy hooks - no guessing."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semarun.models.artifacts import ResumeArtifacts
from semarun.models.checkpoint import Checkpoint
from semarun.models.divergence import DivergenceMatrix
from semarun.policies.contract import PolicyContext, PolicyOutcome, PolicyRegistry
from semarun.policies.mapping import PolicyMapping

if TYPE_CHECKING:
    from semarun.audit.log import AuditLog


class PolicyRouter:
    def __init__(
        self,
        registry: PolicyRegistry,
        audit: AuditLog,
    ) -> None:
        self._registry = registry
        self._audit = audit

    def route(
        self,
        run_id: str,
        matrix: DivergenceMatrix,
        checkpoint: Checkpoint,
        mapping: PolicyMapping,
        current: ResumeArtifacts | None = None,
        last_green_checkpoint_id: str | None = None,
        revalidation_template: str = "",
        assertions: list[str] | None = None,
    ) -> list[PolicyOutcome]:
        if not matrix.has_divergence:
            outcome = PolicyOutcome(
                action="continue",
                hook_name="none",
                flag="none",
                message="No divergence; transparent resume",
            )
            self._audit.emit(run_id, "policy_applied", outcome.model_dump(mode="json"))
            return [outcome]

        outcomes: list[PolicyOutcome] = []
        artifacts = current or ResumeArtifacts()

        self._audit.emit(
            run_id,
            "divergence_detected",
            {"matrix": matrix.model_dump(mode="json")},
        )

        for flag in matrix.triggered_flags():
            hook_name = mapping.hook_for_flag(flag)
            if hook_name is None:
                continue
            hook = self._registry.get(hook_name)
            if hook is None:
                outcomes.append(
                    PolicyOutcome(
                        action="halt_for_human",
                        hook_name=hook_name,
                        flag=flag,
                        message=f"Policy hook '{hook_name}' not registered",
                    )
                )
                continue
            ctx = PolicyContext(
                run_id=run_id,
                flag=flag,
                matrix=matrix,
                checkpoint=checkpoint,
                current=artifacts,
                last_green_checkpoint_id=last_green_checkpoint_id,
                revalidation_template=revalidation_template,
                assertions=list(assertions or []),
            )
            outcome = hook.execute(ctx)
            outcomes.append(outcome)
            self._audit.emit(run_id, "policy_applied", outcome.model_dump(mode="json"))

        return outcomes
