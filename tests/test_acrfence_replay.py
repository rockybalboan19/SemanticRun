"""ACRFence: divergent re-synthesized outbound payloads are flagged, not replayed."""

from __future__ import annotations

from semarun import SemarunRuntime
from semarun.kernel.ledger import ReplayVerdict
from semarun.models.artifacts import ResumeArtifacts


def test_divergent_payload_flagged_not_replayed():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="Payment flow")

    original = {"amount": 100, "idempotency_key": "abc"}
    with run.step("tool_call", name="send_payment") as step:
        step.set_tool_result(
            "send_payment",
            {"status": "sent"},
            explicit_side_effect="external",
            outbound_request=original,
        )

    prior = runtime.storage.get_latest_side_effect_for_target(run.id, "send_payment")
    assert prior is not None
    assert prior.replay_permitted is True
    prior_hash = prior.outbound_request_hash

    resynthesized = {"amount": 100, "idempotency_key": "abc", "memo": "retry"}
    verdict = runtime._ledger.permit_replay(run.id, "send_payment", resynthesized)
    assert verdict == ReplayVerdict.FLAG_DIVERGENCE

    updated = runtime.storage.get_latest_side_effect_for_target(run.id, "send_payment")
    assert updated.replay_permitted is False
    assert updated.outbound_request_hash == prior_hash

    events = runtime.audit.get_run_history(run.id)
    assert any(e["event_type"] == "outbound_payload_divergence" for e in events)

    matrix = run.compute_divergence_matrix(
        ResumeArtifacts(outbound_payloads={"send_payment": resynthesized})
    )
    assert matrix.outbound_payload_divergence.get("send_payment") is True
    runtime.close()


def test_matching_payload_permits_replay():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="Idempotent retry")
    payload = {"amount": 50, "idempotency_key": "xyz"}
    with run.step("tool_call", name="charge") as step:
        step.set_tool_result(
            "charge",
            {"ok": True},
            explicit_side_effect="external",
            outbound_request=payload,
        )
    verdict = runtime._ledger.permit_replay(run.id, "charge", payload)
    assert verdict == ReplayVerdict.PERMIT
    runtime.close()
