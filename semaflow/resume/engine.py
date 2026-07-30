"""Resume engine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from semaflow.divergence.detector import DivergenceDetector
from semaflow.models.policy import (
    ContinuationPolicy,
    ContinuationResult,
    DivergenceAction,
    DivergenceReport,
    ResumeMode,
)
from semaflow.models.state import AgentState, Checkpoint, ModelContext
from semaflow.resume.modes import (
    action_to_mode,
    build_revalidation_checklist,
    resolve_resume_mode,
)

if TYPE_CHECKING:
    from semaflow.audit.log import AuditLog


class ResumeEngine:
    def __init__(self, audit: AuditLog) -> None:
        self._audit = audit
        self._detector = DivergenceDetector()

    @property
    def detector(self) -> DivergenceDetector:
        return self._detector

    def apply_continuation(
        self,
        run_id: str,
        report: DivergenceReport,
        policy: ContinuationPolicy,
    ) -> ContinuationResult:
        if not report.has_divergence:
            result = ContinuationResult(
                mode=ResumeMode.TRANSPARENT,
                action=DivergenceAction.RESUME_SILENTLY,
                message="No divergence detected; transparent resume",
            )
            self._audit.emit(run_id, "policy_applied", result.model_dump(mode="json"))
            return result

        primary = report.events[0]
        action = policy.action_for(primary.kind)
        mode = action_to_mode(action)
        if mode == ResumeMode.TRANSPARENT and report.has_divergence:
            mode = resolve_resume_mode(report.events)

        warnings: list[str] = []
        if any(e.kind.value == "model_change" for e in report.events):
            warnings.append("Model version changed since last checkpoint")

        result = ContinuationResult(
            mode=mode,
            action=action,
            revalidation_checklist=build_revalidation_checklist(report),
            warnings=warnings,
            message=f"Applied policy for {primary.kind.value}",
        )
        self._audit.emit(
            run_id,
            "divergence_detected",
            {"events": [e.model_dump(mode="json") for e in report.events]},
        )
        self._audit.emit(run_id, "policy_applied", result.model_dump(mode="json"))
        return result

    def replan(self, state: AgentState, preserve_intent: bool = True) -> AgentState:
        intent = state.intent if preserve_intent else ""
        facts = list(state.established_facts)
        questions = list(state.open_questions)
        if not questions:
            questions = ["Rebuild plan after divergence"]
        return AgentState(
            intent=intent,
            plan=[],
            working_memory=dict(state.working_memory),
            established_facts=facts,
            open_questions=questions,
            pending_actions=[],
            tool_commitments=dict(state.tool_commitments),
            approval_state=None,
            failure_history=list(state.failure_history),
            risk_flags=list(state.risk_flags),
        )

    def reconstruct_state(self, checkpoint: Checkpoint) -> AgentState:
        return checkpoint.to_agent_state()
