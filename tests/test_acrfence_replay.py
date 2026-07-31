"""ACRFence: divergent re-synthesized retry payloads must be flagged, not replayed."""

from __future__ import annotations

from semarun import SemarunRuntime
from semarun.kernel.ledger import SideEffectKind


def test_identical_payload_allows_replay():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="payment")
    payload = {"recipient": "vendor@co.com", "amount_cents": 5000, "idempotency_key": "k1"}
    with run.step("tool_call", name="charge") as step:
        step.set_tool_result(
            "charge",
            {"status": "ok"},
            outbound_request=payload,
            explicit_side_effect="external",
        )
    auth = run.authorize_replay("charge", payload, kind=SideEffectKind.EXTERNAL.value)
    assert auth.allowed is True
    assert auth.flagged is False
    runtime.close()


def test_divergent_resynthesized_payload_is_flagged():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="payment retry")
    original = {"recipient": "vendor@co.com", "amount_cents": 5000, "idempotency_key": "k1"}
    with run.step("tool_call", name="charge") as step:
        step.set_tool_result(
            "charge",
            {"status": "ok"},
            outbound_request=original,
            explicit_side_effect="external",
        )
    resynthesized = {
        "recipient": "vendor@co.com",
        "amount_cents": 5000,
        "idempotency_key": "k1",
        "memo": "retry after restore",
    }
    auth = run.authorize_replay("charge", resynthesized, kind=SideEffectKind.EXTERNAL.value)
    assert auth.allowed is False
    assert auth.flagged is True
    assert auth.reason == "payload_divergence"
    assert auth.expected_hash != auth.actual_hash
    runtime.close()


def test_no_prior_record_flags_replay():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="fresh call")
    auth = run.authorize_replay(
        "unknown_api",
        {"action": "delete"},
        kind=SideEffectKind.EXTERNAL.value,
    )
    assert auth.allowed is False
    assert auth.flagged is True
    runtime.close()
