"""Multi-vendor interrupt demo - mechanical divergence + policy routing."""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun import (
    BehavioralDriftPolicy,
    FailFast,
    PolicyMapping,
    RevalidateWithPrompt,
    SemarunRuntime,
    StrictReset,
)
from semarun.kernel.artifact_diff import hash_file_tree, hash_schema
from semarun.models.artifacts import FileTreeSnapshot, ModelIdRef, ResumeArtifacts, ToolSchemaRef


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = Path(tmp) / "workspace"
        workspace.mkdir()
        (workspace / "main.py").write_text("print('v1')\n", encoding="utf-8")

        db = Path(tmp) / "demo.db"
        mapping = PolicyMapping(
            tool_result_hash_mismatch="RevalidateWithPrompt",
            model_id_changed="StrictReset",
            file_tree_hash_mismatch="FailFast",
            behavioral_drift_flagged="BehavioralDriftPolicy",
        )
        runtime = SemarunRuntime(
            str(db),
            policy_mapping=mapping,
            revalidation_template="scripts/revalidate_tools.py",
            assertions=["search.status == ok", "codegen.output_hash is not None"],
        )
        for hook in (FailFast(), RevalidateWithPrompt(), StrictReset(), BehavioralDriftPolicy()):
            runtime.registry.register(hook)

        run = runtime.create_run(
            intent="Refactor module across vendor models",
            plan=["search docs", "edit code", "run tests"],
        )
        print(f"Run: {run.id}")

        openai_schema = {"tool": "search", "vendor": "openai", "fields": ["query", "top_k"]}
        with run.step("tool_call", name="search", metadata={"vendor": "openai"}) as step:
            step.set_tool_result(
                "search",
                {"query": "sqlite checkpoint", "results": ["doc1"], "request_id": "r1"},
                hash_exclude=["request_id"],
                schema=openai_schema,
            )
            run.set_file_tree(hash_file_tree(workspace))

        with run.step("llm_call", name="codegen", model="gpt-4.1-2026-07") as step:
            run.state.working_memory.set_slot(
                "patch",
                {"file": "main.py", "content": "print('v2')\n"},
                step_id=step.step_id or "",
            )

        run.checkpoint()
        run.mark_green_checkpoint()
        run_id = run.id
        runtime.close()

        # Simulate interrupt: workspace changed, vendor switched, tool output drifted
        (workspace / "main.py").write_text("print('v3')\n", encoding="utf-8")
        runtime2 = SemarunRuntime(str(db))
        resumed = runtime2.resume(run_id)

        current = ResumeArtifacts(
            model_id=ModelIdRef(model_family="claude", model_version="sonnet-4-2026"),
            tool_schemas={
                "search": ToolSchemaRef(
                    tool_name="search",
                    schema_hash=hash_schema({**openai_schema, "vendor": "anthropic"}),
                )
            },
            tool_results={
                "search": {"query": "sqlite checkpoint", "results": ["doc1", "doc2"], "request_id": "r9"},
            },
            file_tree=hash_file_tree(workspace),
            behavioral_drift_flagged=True,
            behavioral_drift_reason="Reviewer flagged tone shift without artifact change",
        )

        matrix = resumed.compute_divergence_matrix(current)
        print("\n[Divergence Matrix]")
        for flag in matrix.triggered_flags():
            print(f"  - {flag}")

        print("\n[Policy Outcomes]")
        for outcome in resumed.route_policies(matrix, current):
            print(f"  {outcome.flag}: {outcome.hook_name} -> {outcome.action}")
            if outcome.payload:
                print(f"    payload: {outcome.payload}")

        runtime2.abort(resumed, reason="demo complete")
        runtime2.close()


if __name__ == "__main__":
    main()
