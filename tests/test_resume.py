"""Tests for resume engine and resume modes."""

from semarun import ContinuationPolicy, SemarunRuntime
from semarun.models.policy import ResumeMode


def test_transparent_resume():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="resume test", plan=["a", "b"])
    run.checkpoint()
    run.pause()
    resumed = runtime.resume(run.id)
    report = resumed.detect_divergence()
    assert not report.has_divergence
    action = resumed.apply_continuation(report)
    assert action.mode == ResumeMode.TRANSPARENT
    runtime.close()


def test_state_reconstruction():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="state test", plan=["research", "draft"])
    with run.step("tool_call", name="crm") as step:
        step.set_tool_result("crm", {"lead": "42"})
        run.state.working_memory["draft"] = "hello"
    run.pause()
    resumed = runtime.resume(run.id)
    assert resumed.state.intent == "state test"
    assert resumed.state.working_memory.get("draft") == "hello"
    assert "crm" in resumed.state.tool_commitments
    runtime.close()


def test_semantic_replan():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="replan test", plan=["old plan"])
    run.pause()
    resumed = runtime.resume(run.id)
    new_state = resumed.replan(preserve_intent=True)
    assert new_state.intent == "replan test"
    assert new_state.plan == []
    assert new_state.pending_actions == []
    runtime.close()


def test_revalidated_mode_on_tool_drift():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(
        intent="drift test",
        continuation_policy=ContinuationPolicy(),
    )
    with run.step("tool_call", name="crm") as step:
        step.set_tool_result("crm", {"status": "ok"}, hash_exclude=["created_at"])
    run.pause()
    resumed = runtime.resume(run.id)
    report = resumed.detect_divergence(
        fresh_tool_results={"crm": {"status": "changed", "created_at": "x"}}
    )
    assert report.has_divergence
    action = resumed.apply_continuation(report)
    assert action.mode == ResumeMode.REVALIDATED
    assert "crm" in action.revalidation_checklist
    cleared = resumed.revalidate_stale(["crm"])
    assert "crm" in cleared
    runtime.close()
