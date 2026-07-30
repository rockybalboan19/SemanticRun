"""Tests for divergence detector."""

from semaflow import ContinuationPolicy, SemaFlowRuntime
from semaflow.models.policy import DivergenceKind, ResumeMode
from semaflow.models.state import ModelContext


def test_model_change_detection():
    runtime = SemaFlowRuntime.in_memory()
    run = runtime.create_run(
        intent="model test",
        model_context=ModelContext(model_family="gpt-4", model_version="2026-01"),
    )
    run.checkpoint()
    run.pause()
    resumed = runtime.resume(run.id)
    report = resumed.detect_divergence(
        current_model=ModelContext(model_family="gpt-4.1", model_version="2026-07")
    )
    kinds = {e.kind for e in report.events}
    assert DivergenceKind.MODEL_CHANGE in kinds
    action = resumed.apply_continuation(report)
    assert action.mode == ResumeMode.REVALIDATED
    assert action.warnings
    runtime.close()


def test_user_instruction_change():
    runtime = SemaFlowRuntime.in_memory()
    run = runtime.create_run(intent="original intent", plan=["a", "b"])
    run.checkpoint()
    run.pause()
    resumed = runtime.resume(run.id)
    report = resumed.detect_divergence(current_intent="new intent")
    assert any(e.kind == DivergenceKind.USER_INSTRUCTION_CHANGE for e in report.events)
    action = resumed.apply_continuation(report)
    assert action.mode == ResumeMode.SEMANTIC_REPLAN
    runtime.close()


def test_semantic_contradiction():
    runtime = SemaFlowRuntime.in_memory()
    run = runtime.create_run(intent="email test")
    from semaflow.models.state import Fact, PendingAction

    run.state.established_facts.append(Fact(fact="lead unsubscribed", source="crm", confidence=1.0))
    run.state.pending_actions.append(PendingAction(type="human_approval", action="send_email"))
    run.checkpoint()
    run.pause()
    resumed = runtime.resume(run.id)
    report = resumed.detect_divergence()
    assert any(e.kind == DivergenceKind.SEMANTIC_CONTRADICTION for e in report.events)
    runtime.close()


def test_continuation_policy_mapping():
    policy = ContinuationPolicy(on_tool_drift="revalidate", on_user_change="replan")
    assert policy.action_for(DivergenceKind.TOOL_DRIFT).value == "revalidate"
    assert policy.action_for(DivergenceKind.USER_INSTRUCTION_CHANGE).value == "replan"
