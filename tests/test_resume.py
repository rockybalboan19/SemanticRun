"""Tests for resume and policy routing integration."""

from semarun import PolicyMapping, SemarunRuntime
from semarun.models.artifacts import ModelIdRef, ResumeArtifacts


def test_transparent_resume():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="resume test", plan=["a", "b"])
    run.checkpoint()
    run.pause()
    resumed = runtime.resume(run.id)
    matrix = resumed.compute_divergence_matrix(
        ResumeArtifacts(intent_text="resume test", plan=["a", "b"])
    )
    assert not matrix.has_divergence
    outcomes = resumed.route_policies(matrix)
    assert outcomes[0].action == "continue"
    runtime.close()


def test_state_reconstruction():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="state test", plan=["research", "draft"])
    with run.step("tool_call", name="crm") as step:
        step.set_tool_result("crm", {"lead": "42"})
        run.state.working_memory.set_slot("draft", "hello", step_id=step.step_id or "")
    run.pause()
    resumed = runtime.resume(run.id)
    assert resumed.state.intent == "state test"
    assert resumed.state.working_memory.get("draft") == "hello"
    assert "crm" in resumed.state.tool_commitments
    runtime.close()


def test_strict_reset_policy_on_model_change():
    runtime = SemarunRuntime.in_memory(
        policy_mapping=PolicyMapping(model_id_changed="StrictReset"),
    )
    run = runtime.create_run(intent="model test")
    run.checkpoint()
    run.mark_green_checkpoint()
    run.pause()
    resumed = runtime.resume(run.id)
    matrix = resumed.compute_divergence_matrix(
        ResumeArtifacts(model_id=ModelIdRef(model_family="gpt-4.1", model_version="2026-07"))
    )
    outcomes = resumed.route_policies(matrix)
    assert any(o.hook_name == "StrictReset" for o in outcomes)
    runtime.close()
