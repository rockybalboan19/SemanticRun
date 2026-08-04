"""Tests for checkpoint engine and tool hashing."""

import tempfile
from pathlib import Path

import pytest

from semanticrun import SemanticRun
from semanticrun.checkpoint.hashing import hash_tool_result
from semanticrun.checkpoint.triggers import CheckpointTrigger, should_checkpoint
from semanticrun.policies.mapping import PolicyMapping


def test_hash_tool_result_stable():
    result = {"name": "Alice", "company": "Acme"}
    assert hash_tool_result(result) == hash_tool_result(result)


def test_hash_exclude_ignores_ephemeral_fields():
    base = {"name": "Alice", "created_at": "2026-01-01"}
    changed = {"name": "Alice", "created_at": "2026-07-30"}
    assert hash_tool_result(base, hash_exclude=["created_at"]) == hash_tool_result(
        changed, hash_exclude=["created_at"]
    )


def test_trigger_rules():
    assert should_checkpoint(CheckpointTrigger.TOOL_BOUNDARY, step_type="tool_call")
    assert should_checkpoint(CheckpointTrigger.MANUAL)


def test_checkpoint_created_on_tool_step():
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="test", plan=["step1"])
    with run.step("tool_call", name="lookup") as step:
        step.set_tool_result("lookup", {"id": 1}, schema={"type": "object"})
    ckpt = runtime.storage.get_latest_checkpoint(run.id)
    assert ckpt is not None
    assert "lookup" in ckpt.tool_state
    runtime.close()


def test_export_checkpoint_json(tmp_path):
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="export test")
    run.checkpoint()
    out = tmp_path / "ckpt.json"
    content = run.export_checkpoint_json(str(out))
    assert out.exists()
    assert "export test" in content
    runtime.close()
