"""Tests for export_checkpoint_json."""

from semarun import SemarunRuntime


def test_export_returns_json_string():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="export")
    run.checkpoint()
    data = run.export_checkpoint_json()
    assert '"intent": "export"' in data or '"intent":"export"' in data.replace(" ", "")
    runtime.close()
