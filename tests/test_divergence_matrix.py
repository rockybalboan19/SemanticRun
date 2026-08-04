"""Tests for divergence matrix builder."""

from semanticrun.kernel.divergence_matrix import build_divergence_matrix
from semanticrun.models.artifacts import ModelIdRef, ResumeArtifacts, ToolSchemaRef
from semanticrun.models.checkpoint import Checkpoint
from semanticrun.models.state import AgentState, RunStatus, ToolResultCommitment


def _checkpoint(**kwargs) -> Checkpoint:
    state = AgentState.create(intent="test intent", plan=["a", "b"])
    state.tool_commitments["crm"] = ToolResultCommitment(
        tool_name="crm",
        result_hash="deadbeef",
    )
    return Checkpoint(
        run_id="run_1",
        status=RunStatus.PAUSED,
        state=state,
        model_id=ModelIdRef(model_family="gpt-4", model_version="2026-01"),
        tool_schemas={"crm": ToolSchemaRef(tool_name="crm", schema_hash="schema1")},
        **kwargs,
    )


def test_matrix_no_divergence_when_artifacts_match():
    ckpt = _checkpoint()
    current = ResumeArtifacts(
        model_id=ModelIdRef(model_family="gpt-4", model_version="2026-01"),
        tool_schemas={"crm": ToolSchemaRef(tool_name="crm", schema_hash="schema1")},
        intent_text="test intent",
        plan=["a", "b"],
    )
    matrix = build_divergence_matrix(ckpt, current)
    assert not matrix.has_divergence


def test_matrix_model_id_changed():
    ckpt = _checkpoint()
    current = ResumeArtifacts(
        model_id=ModelIdRef(model_family="gpt-4.1", model_version="2026-07"),
    )
    matrix = build_divergence_matrix(ckpt, current)
    assert matrix.model_id_changed


def test_matrix_behavioral_drift_explicit_only():
    ckpt = _checkpoint()
    current = ResumeArtifacts(behavioral_drift_flagged=True, behavioral_drift_reason="manual flag")
    matrix = build_divergence_matrix(ckpt, current)
    assert matrix.behavioral_drift_flagged
    assert "behavioral_drift_flagged" in matrix.triggered_flags()
