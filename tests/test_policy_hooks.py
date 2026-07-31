"""Tests for policy hooks."""

from semarun.models.artifacts import ResumeArtifacts
from semarun.models.checkpoint import Checkpoint
from semarun.models.divergence import DivergenceMatrix
from semarun.models.state import AgentState, RunStatus
from semarun.policies.behavioral import BehavioralDriftPolicy
from semarun.policies.builtin import FailFast, RevalidateWithPrompt, StrictReset
from semarun.policies.contract import PolicyContext


def _ctx(flag: str = "model_id_changed") -> PolicyContext:
    state = AgentState.create("intent")
    checkpoint = Checkpoint(run_id="run_1", status=RunStatus.PAUSED, state=state)
    matrix = DivergenceMatrix(model_id_changed=True)
    return PolicyContext(
        run_id="run_1",
        flag=flag,
        matrix=matrix,
        checkpoint=checkpoint,
        current=ResumeArtifacts(),
        last_green_checkpoint_id="ckpt_green",
    )


def test_fail_fast_aborts():
    outcome = FailFast().execute(_ctx())
    assert outcome.action == "abort"
    assert outcome.payload["require_human_reauth"] is True


def test_revalidate_with_prompt_returns_assertions():
    outcome = RevalidateWithPrompt(
        template="assert_tools.py",
        assertions=["crm.status == ok"],
    ).execute(_ctx("tool_result_hash_mismatch"))
    assert outcome.action == "run_assertions"
    assert outcome.payload["template"] == "assert_tools.py"


def test_strict_reset_loads_green_checkpoint():
    outcome = StrictReset().execute(_ctx())
    assert outcome.action == "load_checkpoint"
    assert outcome.payload["checkpoint_id"] == "ckpt_green"


def test_behavioral_drift_halts_for_human():
    ctx = _ctx("behavioral_drift_flagged")
    ctx.current.behavioral_drift_reason = "output style changed"
    outcome = BehavioralDriftPolicy().execute(ctx)
    assert outcome.action == "halt_for_human"
