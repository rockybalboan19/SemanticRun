"""Tests for approval gates."""

from semanticrun import SemanticRun
from semanticrun.models.state import RunStatus


def test_approval_gate_pauses_run():
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="approval test")
    run.request_approval("send_email", payload={"draft": "hi"})
    assert run.status == RunStatus.WAITING_APPROVAL
    ckpt = runtime.storage.get_latest_checkpoint(run.id)
    assert ckpt.status == RunStatus.WAITING_APPROVAL
    runtime.close()


def test_approve_resumes_run():
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="approve test")
    run.request_approval("send_email")
    run.approve()
    assert run.status == RunStatus.RUNNING
    runtime.close()


def test_reject_flags_approval_change_on_resume():
    from semanticrun.models.artifacts import ResumeArtifacts

    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="reject test")
    run.request_approval("send_email")
    run.reject()
    resumed = runtime.resume(run.id)
    matrix = resumed.compute_divergence_matrix(
        ResumeArtifacts(approval_status="approved")
    )
    assert matrix.approval_state_changed
    runtime.close()
