"""Resume must restore handle-local artifacts (tool schemas, file tree, cursor)."""

from __future__ import annotations

from semarun import SemanticRun
from semarun.kernel.artifact_diff import hash_file_tree


def test_resume_retains_tool_schemas_on_next_checkpoint(tmp_path):
    db = tmp_path / "hydrate.db"
    env = SemanticRun.open(db)
    run = env.start(intent="hydrate", plan=["a"])
    with run.step("tool_call", name="crm") as step:
        step.set_tool_result(
            "crm",
            {"x": 1},
            schema={"name": "crm", "fields": ["x"]},
            explicit_side_effect="external",
        )
    assert "crm" in run.tool_schemas
    run.pause()
    run_id = run.id
    env.close()

    env2 = SemanticRun.open(db)
    resumed = env2.resume(run_id)
    assert "crm" in resumed.tool_schemas
    resumed.checkpoint()
    ckpt = env2.storage.get_latest_checkpoint(run_id)
    assert ckpt is not None
    assert "crm" in ckpt.tool_schemas
    env2.close()


def test_resume_retains_file_tree(tmp_path):
    root = tmp_path / "tree"
    root.mkdir()
    (root / "a.txt").write_text("hello", encoding="utf-8")
    snapshot = hash_file_tree(root)

    db = tmp_path / "ft.db"
    env = SemanticRun.open(db)
    run = env.start(intent="files")
    run.set_file_tree(snapshot)
    run.checkpoint()
    run.pause()
    run_id = run.id
    env.close()

    env2 = SemanticRun.open(db)
    resumed = env2.resume(run_id)
    assert resumed._file_tree is not None
    assert resumed._file_tree.merkle_hash == snapshot.merkle_hash
    env2.close()


def test_resume_retains_plan_cursor(tmp_path):
    db = tmp_path / "cursor.db"
    env = SemanticRun.open(db)
    run = env.start(intent="cursor", plan=["research", "draft", "send"])
    names = []
    for step in run.steps():
        names.append(step.name)
        if step.name == "research":
            step.tool("crm_lookup", lambda: {"ok": True})
            step.complete()
            run.pause()
            break
    assert names == ["research"]
    assert run.plan_index == 1
    assert "research" in run.completed_steps
    run_id = run.id
    env.close()

    env2 = SemanticRun.open(db)
    resumed = env2.resume(run_id)
    assert resumed.plan_index == 1
    assert resumed.completed_steps == ["research"]
    nxt = next(iter(resumed.steps()))
    assert nxt.name == "draft"
    env2.close()
