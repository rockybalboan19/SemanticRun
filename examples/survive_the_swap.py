"""
HN-style demo: pause for approval, resume after tool/model/outbound drift.

Shows SemanticRun as an environment — durable cursor skips completed work,
divergence matrix is mechanical, policies are enforced (not host boilerplate).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from semarun import PolicyAbort, PolicyMapping, SemanticRun
from semarun.models.artifacts import ModelIdRef, ResumeArtifacts


def crm_lookup(lead_id: str, company: str = "Company X") -> dict:
    return {
        "lead_id": lead_id,
        "name": "Jane Doe",
        "company": company,
        "email": "jane@example.com",
        "created_at": "2026-07-30T12:00:00Z",
        "request_id": "req_abc",
    }


def draft_email(lead: dict) -> str:
    return f"Hi {lead['name']}, welcome to {lead['company']}!"


def main() -> None:
    tmp = tempfile.mkdtemp()
    db = Path(tmp) / "survive.db"
    env = SemanticRun.open(
        db,
        policy_mapping=PolicyMapping(
            tool_result_hash_mismatch="revalidate",
            model_id_changed="fail_fast",
            outbound_payload_divergence="fail_fast",
        ),
    )

    try:
        run = env.start(
            intent="Onboard lead_42",
            plan=["research", "draft", "approve", "send"],
        )
        print(f"Started run {run.id}")

        lead = None
        for step in run.steps():
            if step.name == "research":
                lead = step.tool(
                    "crm_lookup",
                    lambda: crm_lookup("lead_42"),
                    hash_exclude=["created_at", "request_id"],
                    schema={"name": "crm_lookup", "fields": ["lead_id", "name", "company"]},
                )
                print(f"  research -> {lead['company']}")
            elif step.name == "draft":
                assert lead is not None
                draft = step.llm(lambda: draft_email(lead), model="gpt-4.1-2026-07")
                step.remember("draft_email", draft)
                print(f"  draft -> {draft!r}")
            elif step.name == "approve":
                step.require_approval(
                    "send_email",
                    {"draft": run.state.working_memory.get("draft_email")},
                )
                print("  approve -> waiting (process will exit)")
                break

        print(f"Cursor after pause: index={run.plan_index} completed={run.completed_steps}")
        run_id = run.id
    finally:
        env.close()

    # --- days later: CRM drifted ---
    print("\n--- resume under drifted world ---")
    env2 = SemanticRun.open(
        db,
        policy_mapping=PolicyMapping(
            tool_result_hash_mismatch="revalidate",
            model_id_changed="fail_fast",
            outbound_payload_divergence="fail_fast",
        ),
    )
    try:
        resumed = env2.resume(
            run_id,
            ResumeArtifacts(
                tool_results={
                    "crm_lookup": crm_lookup("lead_42", company="Company Y"),
                },
                model_id=ModelIdRef(
                    model_family="gpt",
                    model_version="gpt-4.1-2026-07",
                ),
            ),
            enforce_policies=True,
        )

        print(f"Resumed cursor: index={resumed.plan_index} completed={resumed.completed_steps}")
        print(f"Pending revalidations: {resumed.pending_revalidations}")
        for outcome in resumed.last_policy_outcomes:
            print(f"  policy: {outcome.hook_name} -> {outcome.action} ({outcome.flag})")

        resumed.approve()
        skipped = list(resumed.completed_steps)
        for step in resumed.steps():
            if step.name == "send":
                outbound = {
                    "to": "jane@example.com",
                    "body": resumed.state.working_memory.get("draft_email"),
                }
                step.tool(
                    "send_email",
                    lambda: {"status": "sent"},
                    side_effect="external",
                    outbound=outbound,
                )
                print("  send -> ok")

        print(f"Steps never re-executed (cursor skipped): {skipped}")
        env2.complete(resumed)
        print("Done.")
    except PolicyAbort as exc:
        print(f"Aborted: {exc.outcome.hook_name} / {exc.outcome.flag}")
    finally:
        env2.close()


if __name__ == "__main__":
    main()
