"""Tests for human approval gates."""

from semaflow import SemaFlowRuntime
from semaflow.models.state import RunStatus


def test_approval_gate_pauses_run():
    runtime = SemaFlowRuntime.in_memory()
    run = runtime.create_run(intent="approval test")
    run.request_approval("send_email", payload={"draft": "hi"})
    assert run.status == RunStatus.WAITING_APPROVAL
    assert run.state.approval_state is not None
    ckpt = runtime.storage.get_latest_checkpoint(run.id)
    assert ckpt.status == RunStatus.WAITING_APPROVAL
    runtime.close()


def test_approve_resumes_run():
    runtime = SemaFlowRuntime.in_memory()
    run = runtime.create_run(intent="approve test")
    run.request_approval("send_email")
    run.approve()
    assert run.status == RunStatus.RUNNING
    assert run.state.approval_state.status.value == "approved"
    runtime.close()


def test_reject_triggers_divergence_on_resume():
    runtime = SemaFlowRuntime.in_memory()
    run = runtime.create_run(intent="reject test")
    run.request_approval("send_email")
    run.reject()
    assert run.status == RunStatus.PAUSED
    resumed = runtime.resume(run.id)
    report = resumed.detect_divergence()
    assert report.has_divergence
    runtime.close()
