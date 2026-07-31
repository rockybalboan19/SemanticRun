"""Build deterministic divergence matrix from mechanical diffs."""

from __future__ import annotations

from semarun.kernel.artifact_diff import (
    diff_file_trees,
    diff_model_ids,
    diff_tool_results,
    diff_tool_schemas,
)
from semarun.models.artifacts import ResumeArtifacts
from semarun.models.checkpoint import Checkpoint
from semarun.models.divergence import DivergenceMatrix
from semarun.models.state import ApprovalStatus


def build_divergence_matrix(
    checkpoint: Checkpoint,
    current: ResumeArtifacts,
) -> DivergenceMatrix:
    matrix = DivergenceMatrix()
    deltas: dict = {}

    schema_changed, schema_deltas = diff_tool_schemas(
        checkpoint.tool_schemas,
        current.tool_schemas,
    )
    matrix.tool_schema_changed = schema_changed
    if schema_deltas:
        deltas["tool_schemas"] = schema_deltas

    mismatches, tool_deltas = diff_tool_results(
        checkpoint.tool_state,
        current.tool_results,
    )
    matrix.tool_result_hash_mismatch = mismatches
    if tool_deltas:
        deltas["tool_results"] = tool_deltas

    file_changed, file_deltas = diff_file_trees(checkpoint.file_tree, current.file_tree)
    matrix.file_tree_hash_mismatch = file_changed
    if file_deltas:
        deltas["file_tree"] = file_deltas

    current_model = current.model_id or checkpoint.model_id
    model_changed, model_deltas = diff_model_ids(checkpoint.model_id, current_model)
    matrix.model_id_changed = model_changed
    if model_deltas:
        deltas["model_id"] = model_deltas

    if current.intent_text is not None:
        matrix.intent_string_changed = current.intent_text != checkpoint.intent
        if matrix.intent_string_changed:
            deltas["intent"] = {
                "checkpoint": checkpoint.intent,
                "current": current.intent_text,
            }

    if current.plan is not None:
        matrix.plan_sequence_changed = current.plan != checkpoint.plan
        if matrix.plan_sequence_changed:
            deltas["plan"] = {"checkpoint": checkpoint.plan, "current": current.plan}

    if current.approval_status is not None:
        ckpt_status = (
            checkpoint.approval_state.status.value
            if checkpoint.approval_state
            else None
        )
        matrix.approval_state_changed = current.approval_status != ckpt_status
        if matrix.approval_state_changed:
            deltas["approval"] = {
                "checkpoint": ckpt_status,
                "current": current.approval_status,
            }
    elif checkpoint.approval_state and checkpoint.approval_state.status in (
        ApprovalStatus.PENDING,
        ApprovalStatus.REJECTED,
    ):
        matrix.approval_state_changed = True
        deltas["approval"] = {"checkpoint": checkpoint.approval_state.status.value}

    matrix.behavioral_drift_flagged = current.behavioral_drift_flagged
    if current.behavioral_drift_flagged:
        deltas["behavioral_drift"] = {"reason": current.behavioral_drift_reason}

    matrix.deltas = deltas
    return matrix
