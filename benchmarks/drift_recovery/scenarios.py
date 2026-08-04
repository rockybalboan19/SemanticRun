"""Drift scenarios: pause mid-run, inject world change, score three resume strategies."""

from __future__ import annotations

import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from semanticrun import PolicyAbort, PolicyMapping, SemanticRun
from semanticrun.kernel.artifact_diff import hash_file_tree, hash_schema
from semanticrun.models.artifacts import ModelIdRef, ResumeArtifacts, ToolSchemaRef

from . import openrouter_client

POLICIES = PolicyMapping(
    tool_result_hash_mismatch="revalidate",
    tool_schema_changed="revalidate",
    model_id_changed="fail_fast",
    file_tree_hash_mismatch="fail_fast",
    outbound_payload_divergence="fail_fast",
)

CRM_SCHEMA_V1 = {"name": "crm_lookup", "fields": ["lead_id", "name", "company", "email"]}
CRM_SCHEMA_V2 = {
    "name": "crm_lookup",
    "fields": ["lead_id", "name", "company", "email", "account_tier"],
}


@dataclass
class ScenarioResult:
    scenario: str
    strategy: str
    drift_detected: bool
    expected_drift: bool
    steps_reentered: int
    steps_skipped: int
    side_effect_blocked: bool | None
    safe: bool
    correct: bool
    detail: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _crm(company: str = "Acme") -> dict[str, Any]:
    return {
        "lead_id": "lead_42",
        "name": "Jane Doe",
        "company": company,
        "email": "jane@acme.test",
        "created_at": "2026-08-01T00:00:00Z",
        "request_id": "req_1",
    }


def _draft_with_llm(lead: dict[str, Any], model_label: str) -> tuple[str, str]:
    prompt = (
        f"Write one short onboarding email (max 2 sentences) to {lead['name']} "
        f"at {lead['company']}. No subject line."
    )
    resp = openrouter_client.chat(prompt, model=model_label)
    if not resp["ok"]:
        return f"Hi {lead['name']}, welcome to {lead['company']}!", f"fallback/{model_label}"
    return resp["text"], resp["model"]


def _run_until_approval(
    db: Path,
    *,
    workspace: Path | None,
    model_label: str,
    company: str = "Acme",
    schema: dict[str, Any] | None = None,
    seed_outbound: dict[str, Any] | None = None,
) -> tuple[SemanticRun, Any, str]:
    schema = schema or CRM_SCHEMA_V1
    env = SemanticRun.open(db, policy_mapping=POLICIES)
    run = env.start(intent="Onboard lead_42", plan=["research", "draft", "approve", "send"])
    draft_text = ""
    lead: dict[str, Any] = {}
    for step in run.steps():
        if step.name == "research":
            lead = step.tool(
                "crm_lookup",
                lambda: _crm(company),
                hash_exclude=["created_at", "request_id"],
                schema=schema,
            )
            if workspace is not None:
                (workspace / "notes.md").write_text(
                    f"Lead: {lead['name']} @ {lead['company']}\n",
                    encoding="utf-8",
                )
                run.set_file_tree(hash_file_tree(workspace))
                run.checkpoint()
        elif step.name == "draft":
            text, used = _draft_with_llm(lead, model_label)
            draft_text = text
            step.llm(lambda: text, model=used)
            step.remember("draft_email", text)
            step.remember("model_used", used)
            if seed_outbound is not None:
                # Commit the outbound bytes that were approved for send.
                with run.step("tool_call", name="send_email") as ctx:
                    ctx.set_tool_result(
                        "send_email",
                        {"status": "queued"},
                        explicit_side_effect="external",
                        outbound_request=seed_outbound,
                    )
        elif step.name == "approve":
            step.require_approval(
                "send_email",
                {"draft": run.state.working_memory.get("draft_email")},
            )
            break
    return env, run, draft_text


def _finish_send(
    run: Any,
    payload: dict[str, Any],
    *,
    enforce_outbound_gate: bool = True,
) -> int:
    """Approve if needed and execute remaining plan steps; return count re-entered."""
    if run.status.value == "waiting_approval":
        run.approve()
    reentered = 0
    for step in run.steps():
        reentered += 1
        if step.name == "send":
            if enforce_outbound_gate:
                step.tool(
                    "send_email",
                    lambda: {"status": "sent"},
                    side_effect="external",
                    outbound=payload,
                )
            else:
                # Simulate checkpointer-only resume: fire side effect with no gate.
                step.tool(
                    "send_email",
                    lambda: {"status": "sent"},
                    side_effect="external",
                )
    return reentered


# --- strategies -----------------------------------------------------------------


def strategy_naive_restart(scenario: str, prepare: Callable[[Path], dict[str, Any]]) -> ScenarioResult:
    """Redo the entire plan from step 1 — token-wasteful baseline."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = prepare(root)
        ctx["inject"](root)
        env, run, _ = _run_until_approval(
            root / "naive.db",
            workspace=ctx.get("workspace"),
            model_label=ctx.get("resume_model", ctx["model"]),
            company=ctx.get("resume_company", ctx.get("company", "Acme")),
            schema=ctx.get("resume_schema", CRM_SCHEMA_V1),
            seed_outbound=ctx.get("original_outbound"),
        )
        payload = ctx.get("outbound_payload") or {
            "to": "jane@acme.test",
            "body": run.state.working_memory.get("draft_email"),
        }
        reentered = len(run.completed_steps) + 1  # approve pending
        reentered += _finish_send(run, payload, enforce_outbound_gate=False)
        if scenario == "outbound_resynthesis":
            side_blocked: bool | None = False
        else:
            side_blocked = None
        env.close()
        # Naive never detects drift; unsafe whenever drift was expected.
        safe = not ctx["expected_drift"]
        return ScenarioResult(
            scenario=scenario,
            strategy="naive_restart",
            drift_detected=False,
            expected_drift=ctx["expected_drift"],
            steps_reentered=reentered,
            steps_skipped=0,
            side_effect_blocked=side_blocked,
            safe=safe,
            correct=safe,
            detail="Full plan re-executed; no drift awareness",
        )


def strategy_blind_resume(scenario: str, prepare: Callable[[Path], dict[str, Any]]) -> ScenarioResult:
    """Reload checkpoint and continue — checkpointer style, ignores world drift."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = prepare(root)
        env, run, draft = _run_until_approval(
            root / "blind.db",
            workspace=ctx.get("workspace"),
            model_label=ctx["model"],
            company=ctx.get("company", "Acme"),
            schema=ctx.get("schema", CRM_SCHEMA_V1),
            seed_outbound=ctx.get("original_outbound"),
        )
        run_id = run.id
        completed = list(run.completed_steps)
        env.close()

        ctx["inject"](root)

        env2 = SemanticRun.open(root / "blind.db", policy_mapping=POLICIES)
        resumed = env2.resume(run_id, enforce_policies=False)
        payload = ctx.get("outbound_payload") or {
            "to": "jane@acme.test",
            "body": resumed.state.working_memory.get("draft_email") or draft,
        }
        blocked: bool | None = None
        reentered = _finish_send(resumed, payload, enforce_outbound_gate=False)
        if scenario == "outbound_resynthesis":
            blocked = False
        env2.close()

        safe = not ctx["expected_drift"]
        return ScenarioResult(
            scenario=scenario,
            strategy="blind_resume",
            drift_detected=False,
            expected_drift=ctx["expected_drift"],
            steps_reentered=reentered,
            steps_skipped=len(completed),
            side_effect_blocked=blocked,
            safe=safe,
            correct=safe,
            detail="Reloaded state and continued; ignored world drift",
        )


def strategy_semanticrun(scenario: str, prepare: Callable[[Path], dict[str, Any]]) -> ScenarioResult:
    """Resume with ResumeArtifacts + enforced policies + cursor skip."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        ctx = prepare(root)
        env, run, draft = _run_until_approval(
            root / "sr.db",
            workspace=ctx.get("workspace"),
            model_label=ctx["model"],
            company=ctx.get("company", "Acme"),
            schema=ctx.get("schema", CRM_SCHEMA_V1),
            seed_outbound=ctx.get("original_outbound"),
        )
        run_id = run.id
        completed = list(run.completed_steps)
        family = run.model_context.model_family
        version = run.model_context.model_version
        env.close()

        ctx["inject"](root)

        env2 = SemanticRun.open(root / "sr.db", policy_mapping=POLICIES)
        artifacts = ctx["artifacts"](root, family=family, version=version, draft=draft)
        drift_detected = False
        blocked: bool | None = None
        reentered = 0
        detail = ""
        try:
            resumed = env2.resume(run_id, artifacts=artifacts, enforce_policies=True)
            if resumed.last_policy_outcomes:
                drift_detected = any(o.flag != "none" for o in resumed.last_policy_outcomes)
                detail = "; ".join(f"{o.flag}->{o.action}" for o in resumed.last_policy_outcomes)
            payload = ctx.get("outbound_payload") or {
                "to": "jane@acme.test",
                "body": resumed.state.working_memory.get("draft_email") or draft,
            }
            if resumed.status.value != "aborted":
                try:
                    reentered = _finish_send(resumed, payload)
                    if scenario == "outbound_resynthesis":
                        blocked = False
                except PolicyAbort as exc:
                    blocked = True
                    drift_detected = True
                    detail = f"{exc.outcome.flag}->{exc.outcome.action}"
        except PolicyAbort as exc:
            drift_detected = True
            detail = f"{exc.outcome.flag}->{exc.outcome.action}"
        env2.close()

        expected = ctx["expected_drift"]
        if scenario == "outbound_resynthesis":
            # Block at resume matrix and/or at the outbound replay gate.
            safe = blocked is True or (
                drift_detected and "outbound_payload_divergence" in detail
            )
            correct = safe and drift_detected
        elif scenario in {"model_id_swap", "file_edit"}:
            safe = drift_detected
            correct = drift_detected and expected
        else:
            # revalidate: detect drift, keep cursor skip of completed work
            safe = drift_detected == expected
            correct = safe and len(completed) >= 2

        return ScenarioResult(
            scenario=scenario,
            strategy="semanticrun",
            drift_detected=drift_detected,
            expected_drift=expected,
            steps_reentered=reentered,
            steps_skipped=len(completed),
            side_effect_blocked=blocked,
            safe=safe,
            correct=correct,
            detail=detail,
        )


# --- scenario preparers ---------------------------------------------------------


def _base(root: Path) -> dict[str, Any]:
    model = "openrouter/free"
    return {
        "model": model,
        "company": "Acme",
        "schema": CRM_SCHEMA_V1,
        "expected_drift": True,
        "workspace": None,
        "inject": lambda _r: None,
        "artifacts": lambda _r, **_kw: ResumeArtifacts(),
    }


def prepare_model_id_swap(root: Path) -> dict[str, Any]:
    ctx = _base(root)

    def artifacts(_r: Path, **_kw: Any) -> ResumeArtifacts:
        return ResumeArtifacts(
            model_id=ModelIdRef(model_family="swapped", model_version="model-b-999")
        )

    ctx["artifacts"] = artifacts
    return ctx


def prepare_file_edit(root: Path) -> dict[str, Any]:
    ctx = _base(root)
    ws = root / "workspace"
    ws.mkdir()
    ctx["workspace"] = ws

    def inject(_r: Path) -> None:
        (ws / "notes.md").write_text(
            "Lead: Jane Doe @ Acme\nEDITED BY TEAMMATE\n",
            encoding="utf-8",
        )

    def artifacts(_r: Path, **_kw: Any) -> ResumeArtifacts:
        return ResumeArtifacts(file_tree=hash_file_tree(ws))

    ctx["inject"] = inject
    ctx["artifacts"] = artifacts
    return ctx


def prepare_tool_schema_change(root: Path) -> dict[str, Any]:
    ctx = _base(root)

    def artifacts(_r: Path, **_kw: Any) -> ResumeArtifacts:
        return ResumeArtifacts(
            tool_schemas={
                "crm_lookup": ToolSchemaRef(
                    tool_name="crm_lookup",
                    schema_hash=hash_schema(CRM_SCHEMA_V2),
                )
            }
        )

    ctx["artifacts"] = artifacts
    ctx["resume_schema"] = CRM_SCHEMA_V2
    return ctx


def prepare_tool_result_drift(root: Path) -> dict[str, Any]:
    ctx = _base(root)

    def artifacts(_r: Path, **_kw: Any) -> ResumeArtifacts:
        return ResumeArtifacts(tool_results={"crm_lookup": _crm("Globex")})

    ctx["artifacts"] = artifacts
    ctx["resume_company"] = "Globex"
    return ctx


def prepare_outbound_resynthesis(root: Path) -> dict[str, Any]:
    ctx = _base(root)
    ctx["original_outbound"] = {
        "to": "jane@acme.test",
        "body": "Hi Jane, welcome to Acme!",
        "idempotency_key": "send_42",
    }
    ctx["outbound_payload"] = {
        "to": "jane@acme.test",
        "body": "Hi Jane, welcome to Acme!",
        "idempotency_key": "send_42",
        "memo": "retry-after-resume",
    }

    def artifacts(_r: Path, **_kw: Any) -> ResumeArtifacts:
        return ResumeArtifacts(outbound_payloads={"send_email": ctx["outbound_payload"]})

    ctx["artifacts"] = artifacts
    return ctx


SCENARIOS: dict[str, Callable[[Path], dict[str, Any]]] = {
    "model_id_swap": prepare_model_id_swap,
    "file_edit": prepare_file_edit,
    "tool_schema_change": prepare_tool_schema_change,
    "tool_result_drift": prepare_tool_result_drift,
    "outbound_resynthesis": prepare_outbound_resynthesis,
}

STRATEGIES = {
    "naive_restart": strategy_naive_restart,
    "blind_resume": strategy_blind_resume,
    "semanticrun": strategy_semanticrun,
}
