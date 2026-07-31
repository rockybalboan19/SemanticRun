"""CRAB sparsity: checkpoint writes fire on well under half of turns."""

from __future__ import annotations

from semarun import SemarunRuntime


def _synthetic_trace(runtime: SemarunRuntime):
    """Mixed read-only and recovery-relevant turns."""
    run = runtime.create_run(intent="sparsity trace", plan=["explore", "write", "verify"])
    steps = [
        ("grep", {"pattern": "TODO"}, None),
        ("cat", {"path": "/tmp/log.txt"}, None),
        ("ls", {"path": "."}, None),
        ("find", {"name": "*.py"}, None),
        ("write_file", None, "filesystem"),
        ("grep", {"pattern": "error"}, None),
        ("git", {"subcommand": "diff"}, None),
        ("run_shell", None, "process"),
        ("read_file", {"path": "README.md"}, None),
        ("pytest", {"args": ["--collect-only"]}, None),
        ("send_email", None, "external"),
        ("head", {"path": "out.txt"}, None),
    ]
    for tool, args, side_effect in steps:
        with run.step("tool_call", name=tool) as step:
            if side_effect == "filesystem":
                step.record_filesystem_effect("/tmp/out.txt", "write")
            elif side_effect == "process":
                step.record_process_effect("pytest -q")
            elif side_effect == "external":
                step.set_tool_result(
                    tool,
                    {"status": "sent"},
                    outbound_request={"to": "user@example.com", "amount": 100},
                    explicit_side_effect="external",
                )
            else:
                step.set_tool_result(tool, {"ok": True}, tool_args=args)
    return run


def test_sparse_checkpoint_writes_under_half_of_turns():
    runtime = SemarunRuntime.in_memory()
    run = _synthetic_trace(runtime)
    turns = runtime._ledger.turn_count
    writes = runtime._ledger.checkpoint_write_count
    assert turns == 12
    assert writes <= 4
    assert writes / turns < 0.5
    runtime.close()


def test_read_only_tools_skip_checkpoint():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="read only")
    with run.step("tool_call", name="grep") as step:
        step.set_tool_result("grep", {"matches": []}, tool_args={"pattern": "x"})
    with run.step("tool_call", name="cat") as step:
        step.set_tool_result("cat", {"content": "hi"}, tool_args={"path": "f.txt"})
    assert runtime._ledger.checkpoint_write_count == 0
    assert runtime.storage.get_latest_checkpoint(run.id) is None
    runtime.close()


def test_recovery_relevant_triggers_checkpoint():
    runtime = SemarunRuntime.in_memory()
    run = runtime.create_run(intent="side effect")
    with run.step("tool_call", name="write_file") as step:
        step.record_filesystem_effect("/data/out.json", "write")
    ckpt = runtime.storage.get_latest_checkpoint(run.id)
    assert ckpt is not None
    assert runtime._ledger.checkpoint_write_count == 1
    runtime.close()
