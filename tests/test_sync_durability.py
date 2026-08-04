"""Default path is sync-durable: checkpoints exist before step returns."""

from __future__ import annotations

from semarun import SemanticRun


def test_default_async_checkpoints_false():
    env = SemanticRun.in_memory()
    assert env._checkpoint_worker is None
    env.close()


def test_side_effect_checkpoint_visible_immediately(tmp_path):
    db = tmp_path / "sync.db"
    env = SemanticRun.open(db)
    run = env.start(intent="sync")
    with run.step("tool_call", name="send") as step:
        step.set_tool_result(
            "send",
            {"ok": True},
            explicit_side_effect="external",
            outbound_request={"id": 1},
        )
    assert len(env.storage.list_checkpoints(run.id)) >= 1
    env.close()


def test_crash_simulation_resume_restores_completed_work(tmp_path):
    """Close process after N steps; reopen and resume with state + cursor intact."""
    db = tmp_path / "crash.db"
    env = SemanticRun.open(db)
    run = env.start(intent="crash", plan=["a", "b", "c"])
    for step in run.steps():
        if step.name == "a":
            step.remember("slot_a", "value_a")
        elif step.name == "b":
            with run.step("llm_call", name="draft") as ctx:
                ctx.remember("draft", "hello")
            step.complete()
            run.pause()
            break
    run_id = run.id
    assert run.state.working_memory.get("slot_a") == "value_a"
    assert run.state.working_memory.get("draft") == "hello"
    env.close()  # simulate process exit

    env2 = SemanticRun.open(db)
    resumed = env2.resume(run_id)
    assert resumed.state.working_memory.get("slot_a") == "value_a"
    assert resumed.state.working_memory.get("draft") == "hello"
    assert resumed.completed_steps == ["a", "b"]
    nxt = next(iter(resumed.steps()))
    assert nxt.name == "c"
    env2.close()
