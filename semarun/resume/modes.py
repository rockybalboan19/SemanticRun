"""Resume mode resolution."""

from __future__ import annotations

from semarun.models.policy import (
    ContinuationResult,
    DivergenceAction,
    DivergenceEvent,
    DivergenceKind,
    DivergenceReport,
    ResumeMode,
)


def action_to_mode(action: DivergenceAction) -> ResumeMode:
    if action == DivergenceAction.ABORT:
        return ResumeMode.ABORT
    if action in (DivergenceAction.REVALIDATE, DivergenceAction.RESUME_WITH_WARNING):
        return ResumeMode.REVALIDATED
    if action in (DivergenceAction.REPLAN, DivergenceAction.CONFIRM_WITH_HUMAN, DivergenceAction.BRANCH):
        return ResumeMode.SEMANTIC_REPLAN
    return ResumeMode.TRANSPARENT


def resolve_resume_mode(events: list[DivergenceEvent]) -> ResumeMode:
    if not events:
        return ResumeMode.TRANSPARENT
    priority = [
        DivergenceKind.UNSAFE_BRANCH,
        DivergenceKind.SEMANTIC_CONTRADICTION,
        DivergenceKind.USER_INSTRUCTION_CHANGE,
        DivergenceKind.APPROVAL_INVALIDATED,
        DivergenceKind.TOOL_DRIFT,
        DivergenceKind.STALE_EVIDENCE,
        DivergenceKind.MODEL_CHANGE,
        DivergenceKind.BENIGN_CHANGE,
    ]
    kinds = {e.kind for e in events}
    for kind in priority:
        if kind in kinds:
            if kind == DivergenceKind.UNSAFE_BRANCH:
                return ResumeMode.ABORT
            if kind in (
                DivergenceKind.USER_INSTRUCTION_CHANGE,
                DivergenceKind.APPROVAL_INVALIDATED,
                DivergenceKind.SEMANTIC_CONTRADICTION,
            ):
                return ResumeMode.SEMANTIC_REPLAN
            if kind in (DivergenceKind.TOOL_DRIFT, DivergenceKind.STALE_EVIDENCE):
                return ResumeMode.REVALIDATED
            if kind == DivergenceKind.MODEL_CHANGE:
                return ResumeMode.REVALIDATED
    return ResumeMode.TRANSPARENT


def build_revalidation_checklist(report: DivergenceReport) -> list[str]:
    checklist: list[str] = []
    for event in report.events:
        if event.kind == DivergenceKind.TOOL_DRIFT:
            tool = event.details.get("tool")
            if tool:
                checklist.append(tool)
        elif event.kind == DivergenceKind.STALE_EVIDENCE:
            fact = event.details.get("fact")
            if fact:
                checklist.append(f"fact:{fact}")
    return checklist
