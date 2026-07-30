"""End-to-end outreach agent demo."""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun import ContinuationPolicy, SemarunRuntime
from semarun.models.state import Fact


def mock_crm_lookup(lead_id: str) -> dict:
    return {
        "lead_id": lead_id,
        "name": "Jane Doe",
        "company": "Company X",
        "created_at": "2026-07-30T12:00:00Z",
        "request_id": "req_abc",
    }


def mock_llm_draft(profile: dict) -> str:
    return f"Hi {profile['name']}, welcome to {profile['company']}!"


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "outreach.db"
        runtime = SemarunRuntime(str(db))
        run = runtime.create_run(
            intent="Complete onboarding outreach sequence",
            plan=["research lead", "draft email", "request approval", "send email"],
            continuation_policy=ContinuationPolicy(
                on_tool_drift="revalidate",
                on_model_change="resume_with_warning",
                on_user_change="replan",
            ),
        )
        print(f"Created run: {run.id}")

        with run.step("tool_call", name="crm_lookup") as step:
            result = mock_crm_lookup("lead_42")
            step.set_tool_result(
                "crm_lookup",
                result,
                hash_exclude=["created_at", "request_id"],
            )
            run.state.established_facts.append(
                Fact(
                    fact="Lead is at Company X",
                    source="crm",
                    confidence=0.94,
                )
            )

        with run.step("llm_call", model="gpt-4.1-2026-07") as step:
            draft = mock_llm_draft(result)
            run.state.working_memory["draft_email"] = draft

        run.request_approval(action="send_email", payload={"draft": run.state.working_memory["draft_email"]})
        print("Paused for approval...")

        run.approve()
        run_id = run.id
        runtime.close()

        runtime2 = SemarunRuntime(str(db))
        resumed = runtime2.resume(run_id)

        changed_crm = mock_crm_lookup("lead_42")
        changed_crm["company"] = "Company Y"
        report = resumed.detect_divergence(fresh_tool_results={"crm_lookup": changed_crm})
        if report.has_divergence:
            action = resumed.apply_continuation(report)
            print(f"Divergence detected: {action.mode.value}")
            if action.mode.value == "revalidated":
                resumed.revalidate_stale(action.revalidation_checklist)

        resumed.complete_step("send_email")
        runtime2.complete(resumed)
        print(f"Run completed: {resumed.id}")
        runtime2.close()


if __name__ == "__main__":
    main()
