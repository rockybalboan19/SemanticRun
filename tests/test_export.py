"""Tests for export_checkpoint_json."""

from semanticrun import SemanticRun


def test_export_returns_json_string():
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="export")
    run.checkpoint()
    data = run.export_checkpoint_json()
    assert "export" in data
    runtime.close()
