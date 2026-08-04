"""CRAB sparsity: checkpoint writes fire on well under half of turns."""

from __future__ import annotations

from semanticrun import SemanticRun


def _run_synthetic_trace() -> tuple[int, int]:
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="Synthetic trace", plan=["mixed tools"])

    read_only_tools = [
        ("grep", {"pattern": "foo"}),
        ("cat", {"path": "/tmp/x"}),
        ("crm_lookup", {"lead_id": "1"}),
        ("git", {"args": "diff HEAD~1"}),
        ("find", {"path": "."}),
        ("ls", {}),
        ("head", {"file": "a.txt"}),
        ("grep", {"pattern": "bar"}),
        ("read_file", {"path": "b.txt"}),
        ("search", {"q": "test"}),
        ("glob", {"pattern": "*.py"}),
        ("crm_lookup", {"lead_id": "2"}),
        ("git", {"args": "status"}),
        ("cat", {"path": "/tmp/y"}),
        ("grep", {"pattern": "baz"}),
    ]
    recovery_tools = [
        ("write_file", {"path": "out.txt", "content": "x"}, "filesystem"),
        ("send_email", {"to": "a@b.com"}, "external"),
        ("run_shell", {"cmd": "deploy"}, "process"),
        ("write_file", {"path": "out2.txt", "content": "y"}, "filesystem"),
        ("send_payment", {"amount": 10}, "external"),
    ]

    turn_count = 0
    for tool, args in read_only_tools:
        with run.step("tool_call", name=tool) as step:
            step.set_tool_result(tool, {"ok": True}, tool_args=args)
        turn_count += 1

    for tool, args, effect in recovery_tools:
        with run.step("tool_call", name=tool) as step:
            step.set_tool_result(
                tool,
                {"done": True},
                tool_args=args,
                explicit_side_effect=effect,
                outbound_request=args,
            )
        turn_count += 1

    checkpoints = runtime.storage.list_checkpoints(run.id)
    checkpoint_count = len(checkpoints)
    runtime.close()
    return turn_count, checkpoint_count


def test_checkpoint_sparsity_under_half():
    turn_count, checkpoint_count = _run_synthetic_trace()
    assert turn_count == 20
    assert checkpoint_count < turn_count * 0.5
    assert checkpoint_count == 5


def test_read_only_turns_have_ledger_no_extra_checkpoints():
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="Read only")
    with run.step("tool_call", name="grep") as step:
        step.set_tool_result("grep", {"matches": []}, tool_args={"pattern": "x"})
    effects = runtime.storage.list_side_effects(run.id)
    assert len(effects) == 1
    assert effects[0].side_effect_class == "read_only"
    assert len(runtime.storage.list_checkpoints(run.id)) == 0
    runtime.close()


def test_recovery_relevant_turns_checkpoint():
    runtime = SemanticRun.in_memory()
    run = runtime.create_run(intent="Side effect")
    with run.step("tool_call", name="write_file") as step:
        step.set_tool_result(
            "write_file",
            {"written": True},
            explicit_side_effect="filesystem",
            outbound_request={"path": "a.txt"},
        )
    assert len(runtime.storage.list_checkpoints(run.id)) == 1
    effects = runtime.storage.list_side_effects(run.id)
    assert effects[0].side_effect_class == "filesystem"
    runtime.close()
