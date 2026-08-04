"""Durable plan cursor skips completed steps on resume."""

from __future__ import annotations

from semarun import PolicyMapping, SemanticRun
from semarun.models.artifacts import ModelIdRef, ResumeArtifacts
from semarun.policies.errors import PolicyAbort


def test_steps_skips_completed_on_resume(tmp_path):
    db = tmp_path / "skip.db"
    env = SemanticRun.open(db)
    run = env.start(intent="skip", plan=["research", "draft", "send"])
    executed = []
    for step in run.steps():
        executed.append(step.name)
        if step.name == "draft":
            step.llm(lambda: "body", model="gpt-4.1-test")
            step.complete()
            run.pause()
            break
    assert executed == ["research", "draft"]
    assert run.completed_steps == ["research", "draft"]
    run_id = run.id
    env.close()

    env2 = SemanticRun.open(db)
    resumed = env2.resume(run_id)
    executed2 = []
    for step in resumed.steps():
        executed2.append(step.name)
        step.tool("send_email", lambda: {"sent": True}, side_effect="external")
    assert executed2 == ["send"]
    assert resumed.completed_steps == ["research", "draft", "send"]
    env2.complete(resumed)
    env2.close()


def test_apply_policies_fail_fast_raises(tmp_path):
    db = tmp_path / "ff.db"
    env = SemanticRun.open(
        db,
        policy_mapping=PolicyMapping(model_id_changed="fail_fast"),
    )
    run = env.start(intent="ff", plan=["a"])
    with run.step("llm_call", model="gpt-4.1-old") as step:
        step.remember("x", 1)
    run.pause()
    run_id = run.id
    env.close()

    env2 = SemanticRun.open(
        db,
        policy_mapping=PolicyMapping(model_id_changed="fail_fast"),
    )
    try:
        env2.resume(
            run_id,
            ResumeArtifacts(
                model_id=ModelIdRef(model_family="claude", model_version="opus-4")
            ),
        )
        assert False, "expected PolicyAbort"
    except PolicyAbort as exc:
        assert exc.outcome.action == "abort"
    env2.close()


def test_revalidate_sets_pending(tmp_path):
    db = tmp_path / "rv.db"
    env = SemanticRun.open(
        db,
        policy_mapping=PolicyMapping(tool_result_hash_mismatch="revalidate"),
    )
    run = env.start(intent="rv", plan=["research"])
    for step in run.steps():
        step.tool(
            "crm_lookup",
            lambda: {"company": "X"},
            hash_exclude=["created_at"],
            schema={"name": "crm_lookup"},
        )
    run_id = run.id
    env.close()

    env2 = SemanticRun.open(
        db,
        policy_mapping=PolicyMapping(tool_result_hash_mismatch="revalidate"),
    )
    resumed = env2.resume(
        run_id,
        ResumeArtifacts(tool_results={"crm_lookup": {"company": "Y"}}),
    )
    assert resumed.pending_revalidations
    tools = resumed.pending_revalidations[0].get("tools_to_revalidate", [])
    assert "crm_lookup" in tools
    env2.close()
