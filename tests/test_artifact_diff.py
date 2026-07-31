"""Tests for mechanical artifact diffing."""

from semarun.kernel.artifact_diff import (
    diff_file_trees,
    diff_model_ids,
    diff_tool_results,
    diff_tool_schemas,
    hash_schema,
)
from semarun.models.artifacts import FileTreeSnapshot, ModelIdRef, ToolSchemaRef
from semarun.models.state import ToolResultCommitment


def test_hash_schema_stable():
    assert hash_schema({"a": 1}) == hash_schema({"a": 1})


def test_diff_tool_schemas_detects_change():
    ckpt = {"crm": ToolSchemaRef(tool_name="crm", schema_hash="abc")}
    cur = {"crm": ToolSchemaRef(tool_name="crm", schema_hash="def")}
    changed, deltas = diff_tool_schemas(ckpt, cur)
    assert changed
    assert "crm" in deltas


def test_diff_tool_results_respects_hash_exclude():
    commitment = ToolResultCommitment(
        tool_name="crm",
        result_hash=__import__("semarun.checkpoint.hashing", fromlist=["hash_tool_result"]).hash_tool_result(
            {"id": 1, "ts": "a"}, hash_exclude=["ts"]
        ),
        hash_exclude=["ts"],
    )
    mismatches, _ = diff_tool_results(
        {"crm": commitment},
        {"crm": {"id": 1, "ts": "b"}},
    )
    assert mismatches["crm"] is False


def test_diff_model_ids():
    changed, _ = diff_model_ids(
        ModelIdRef(model_family="gpt-4", model_version="2026-01"),
        ModelIdRef(model_family="gpt-4.1", model_version="2026-07"),
    )
    assert changed


def test_diff_file_trees():
    a = FileTreeSnapshot(root="/tmp", merkle_hash="aaa")
    b = FileTreeSnapshot(root="/tmp", merkle_hash="bbb")
    changed, deltas = diff_file_trees(a, b)
    assert changed
    assert deltas["checkpoint_hash"] == "aaa"
