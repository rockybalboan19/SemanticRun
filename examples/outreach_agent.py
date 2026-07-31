"""End-to-end outreach agent demo with explicit policy hooks."""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun import (
    PolicyMapping,
    RevalidateWithPrompt,
    SemarunRuntime,
)
from semarun.models.artifacts import ModelIdRef, ResumeArtifacts
from semarun.models.state import VerifiedClaim


def mock_crm_lookup(lead_id: str) -> dict:
    return {
        "lead_id": lead_id,
        "name": "Jane Doe",
        "company": "Company X",
        "created_at": "2026-07-30T12:00:00Z",
        "request_id": "req_abc",
    }


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "outreach.db"
        runtime = SemarunRuntime(
            str(db),
            policy_mapping=PolicyMapping(
                tool_result_hash_mismatch="RevalidateWithPrompt",
                model_id_changed="FailFast",
            ),
            revalidation_template="assert_tools.py",
            assertions=["crm_lookup.status == success"],
        )
        runtime.registry.register(RevalidateWithPrompt(template="assert_tools.py"))

        run = runtime.create_run(
            intent="Complete onboarding outreach sequence",
            plan=["research lead", "draft email", "request approval", "send email"],
        )
        print(f"Created run: {run.id}")

        with run.step("tool_call", name="crm_lookup") as step:
            result = mock_crm_lookup("lead_42")
            step.set_tool_result(
                "crm_lookup",
                result,
                hash_exclude=["created_at", "request_id"],
                schema={"name": "crm_lookup", "fields": ["lead_id", "name", "company"]},
            )
            run.state.verified_claims.append(
                VerifiedClaim(
                    claim="Lead is at Company X",
                    source="crm",
                    content_hash="claim_hash_1",
                )
            )

        with run.step("llm_call", model="gpt-4.1-2026-07") as step:
            draft = f"Hi {result['name']}, welcome to {result['company']}!"
            run.state.working_memory.set_slot("draft_email", draft, step_id=step.step_id or "")

        run.request_approval(
            action="send_email",
            payload={"draft": run.state.working_memory.get("draft_email")},
        )
        run.approve()
        run_id = run.id
        runtime.close()

        runtime2 = SemarunRuntime(str(db))
        resumed = runtime2.resume(run_id)

        changed_crm = mock_crm_lookup("lead_42")
        changed_crm["company"] = "Company Y"
        matrix = resumed.compute_divergence_matrix(
            ResumeArtifacts(
                tool_results={"crm_lookup": changed_crm},
                model_id=ModelIdRef(model_family="gpt-4.1", model_version="2026-07"),
            )
        )
        if matrix.has_divergence:
            outcomes = resumed.route_policies(matrix)
            for outcome in outcomes:
                print(f"Policy routed: {outcome.hook_name} -> {outcome.action}")

        runtime2.complete(resumed)
        print(f"Run completed: {resumed.id}")
        runtime2.close()


if __name__ == "__main__":
    main()
