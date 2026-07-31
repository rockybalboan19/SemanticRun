<p align="center">
  <img src="https://raw.githubusercontent.com/ysharmcode/SemaRun/master/semarun-logo.png" alt="Semarun" width="360"/>
</p>

<p align="center">
  <strong>Your agent loop decides what to do. Semarun remembers what already happened.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://pypi.org/project/semarun/"><img src="https://img.shields.io/pypi/v/semarun.svg" alt="PyPI"/></a>
  <img src="https://img.shields.io/badge/status-pre--1.0-orange.svg" alt="Status: pre-1.0"/>
</p>

<p align="center">
  Vendor-neutral mechanical state kernel for long-running Python agents.<br/>
  Pause for hours, survive crashes and deploys, resume under a different model or tool result — without redoing completed work.
</p>

<p align="center"><em>Status: early / pre-1.0 — APIs may change. Not yet recommended for production execution paths without your own evaluation.</em></p>

<br/>

```python
from semarun import (
    FailFast,
    PolicyMapping,
    RevalidateWithPrompt,
    SemarunRuntime,
)
from semarun.models.artifacts import ModelIdRef, ResumeArtifacts

runtime = SemarunRuntime(
    policy_mapping=PolicyMapping(
        tool_result_hash_mismatch="RevalidateWithPrompt",
        model_id_changed="FailFast",
        outbound_payload_divergence="FailFast",
    ),
    revalidation_template="assert_tools.py",
)
runtime.registry.register(RevalidateWithPrompt(template="assert_tools.py"))

run = runtime.create_run(
    intent="Complete onboarding outreach sequence",
    plan=["research lead", "draft email", "request approval", "send email"],
)

with run.step("tool_call", name="crm_lookup") as step:
    result = crm.lookup("lead_42")
    step.set_tool_result("crm_lookup", result, hash_exclude=["created_at", "request_id"])

with run.step("llm_call", model="gpt-4.1-2026-07"):
    run.state.working_memory.set_slot("draft_email", llm.generate(...), step_id=step.step_id)

run.request_approval(action="send_email", payload={"draft": run.state.working_memory.get("draft_email")})
# ... process exits, days pass ...

run = runtime.resume(run.id)
matrix = run.compute_divergence_matrix(
    ResumeArtifacts(
        tool_results={"crm_lookup": fresh_crm_data},
        model_id=ModelIdRef(model_family="gpt-4.1", model_version="2026-07"),
    )
)
if matrix.has_divergence:
    outcomes = run.route_policies(matrix)  # declarative only — your agent executes them
```

## What Semarun Is

Semarun is a **standalone in-process state kernel** — not a LangGraph plugin, not a Temporal worker, not an orchestration framework.

| Layer | Responsibility | Semarun |
|-------|----------------|---------|
| **Kernel** (mechanical) | Ledger, artifact diffing, divergence matrix | Booleans + exact deltas only — zero AI, zero heuristics |
| **Policies** (your contract) | Named hooks map matrix flags to outcomes | `FailFast`, `RevalidateWithPrompt`, `StrictReset`, custom hooks |
| **Host agent** | Plan, LLM calls, tool execution | Reads `PolicyOutcome` and acts — kernel never mutates intent or replans |

```mermaid
flowchart LR
    Agent[Your Agent Loop] --> Step[Step Boundary]
    Step --> Ledger[Side-Effect Ledger]
    Step --> Checkpoint[Checkpoint Freeze]
    Pause[Pause / Crash / Deploy] --> Resume[Resume]
    Resume --> Matrix[Divergence Matrix]
    Matrix --> Router[Policy Router]
    Router --> Outcomes[PolicyOutcome list]
    Outcomes --> Agent
```

## Why this exists

**Why hasn't OpenAI or Anthropic just built this?**

Three reasons, and they're structural rather than technical:

1. **Multi-vendor neutrality.** Semarun checkpoints *your* agent loop's state — tool commitments, file-tree hashes, model IDs — regardless of which provider you call next. A frontier lab building checkpointing would naturally optimize for their own stack, not a vendor-agnostic execution contract you can carry across GPT, Claude, Gemini, and local models.

2. **Plumbing vs. frontier focus.** Checkpointing, ledger diffing, and replay guards are infrastructure. Labs prioritize model capability and API surface; durable agent *execution* state is something you embed, not something they sell as a product.

3. **Enterprise lock-in resistance.** Teams that need auditable, policy-governed resumption across crashes, deploys, and human approval gates often cannot depend on a single vendor's opaque session store. Semarun is MIT-licensed, in-process, and inspectable — you own the checkpoint JSON and the policy hooks.

Semarun is the mechanical layer beneath your loop. It does not replace your framework, your model provider, or your orchestrator.

## The Problem

- Agents crash, deploy, wait for humans, and change models — replay-first systems cannot detect artifact drift.
- Re-running from step 1 wastes tokens, time, and corrupts intent.
- You need **mechanical divergence detection** plus **explicit reconciliation policies** — not kernel-side guessing.

## Performance & Overhead

All numbers below come from scripts in [`benchmarks/`](benchmarks/). Reproduce them locally before trusting them in your environment:

```bash
python benchmarks/resume_savings.py   # resume vs naive restart
python benchmarks/overhead.py           # checkpoint snapshot latency
```

**Worked example** (8-step run, crash during step 7 — output of `benchmarks/resume_savings.py`):

```
Naive restart (step 7 crash):  ████████  8 LLM steps re-executed
Semarun resume:                █         1 LLM step re-executed
Steps skipped:                 7 of 8 (87.5%)
```

Resuming from a mid-run checkpoint avoids re-running completed steps entirely. The exact savings depend on where your checkpoint boundary falls and which steps are LLM vs. tool-only.

**Checkpoint latency** (output of `benchmarks/overhead.py` on local SQLite, 300 samples):

| Metric | Value |
|--------|-------|
| median | ~0.5 ms |
| p95 | ~0.8 ms |
| min / max | ~0.3 / varies ms |

LLM API calls typically take **1,000–3,000+ ms**. Semarun adds sub-millisecond checkpoint writes on local SQLite at step boundaries — negligible relative to model latency. Your numbers will vary by disk, load, and checkpoint size; run the script.

## Architecture

### Mechanical kernel (`semarun/kernel/`)

- **Side-effect ledger** — append-only record of tool calls, file writes, model invocations at step boundaries
- **Artifact diff** — schema hashes, file-tree merkle hashes, model IDs, tool result hashes (reuses canonical hashing with `hash_exclude`)
- **Divergence matrix** — pure boolean flags + exact before/after deltas; no semantic inference

```python
class DivergenceMatrix:
    tool_schema_changed: bool
    tool_result_hash_mismatch: dict[str, bool]   # tool_name → changed
    file_tree_hash_mismatch: bool
    model_id_changed: bool
    intent_string_changed: bool
    plan_sequence_changed: bool
    approval_state_changed: bool
    behavioral_drift_flagged: bool                 # set by YOUR consistency check — never inferred by kernel
    outbound_payload_divergence: dict[str, bool]  # re-synthesized outbound retry detected
    deltas: dict[str, Any]                         # mechanical diffs only
```

`behavioral_drift_flagged` is set by your own output-consistency check when you pass `behavioral_drift_flagged=True` in `ResumeArtifacts` — Semarun never infers this itself.

Read-only tools (`grep`, `cat`, `git diff`, `crm_lookup`, etc.) append to the ledger but **skip full checkpoint**. Recovery-relevant tools checkpoint via `SIDE_EFFECT_BOUNDARY`.

### Outbound replay guard

Before replaying an external side-effecting call after restore, the ledger hashes the outbound request and compares it to the pre-checkpoint version. Divergent payloads are flagged — never silently replayed.

```python
runtime = SemarunRuntime(
    policy_mapping=PolicyMapping(outbound_payload_divergence="FailFast"),
)

with run.step("tool_call", name="send_payment") as step:
    step.set_tool_result(
        "send_payment",
        result,
        explicit_side_effect="external",
        outbound_request={"amount": 100, "idempotency_key": "abc"},
    )

# After restore, agent re-synthesizes a subtly different payload:
resynthesized = {"amount": 100, "idempotency_key": "abc", "memo": "retry"}
verdict = runtime.ledger.permit_replay(run.id, "send_payment", resynthesized)
# ReplayVerdict.FLAG_DIVERGENCE — never silently replayed

matrix = run.compute_divergence_matrix(
    ResumeArtifacts(outbound_payloads={"send_payment": resynthesized})
)
outcomes = run.route_policies(matrix)
for o in outcomes:
    if o.flag == "outbound_payload_divergence":
        # PolicyOutcome(action="abort", hook_name="FailFast", ...)
        # Your agent halts — payment is NOT retried with the divergent payload
        runtime.abort(run, reason="outbound payload mismatch")
```

### Policy contract (`semarun/policies/`)

You declare which hook runs for each matrix flag. The kernel routes — it does not guess.

| Hook | Outcome | Use when |
|------|---------|----------|
| **FailFast** | `abort` | Model swap, critical file-tree change, outbound payload divergence |
| **RevalidateWithPrompt** | `run_assertions` | Tool result or schema drift — returns your template + assertion list |
| **StrictReset** | `load_checkpoint` | Roll back to last-green checkpoint (you mark which) |
| **BehavioralDriftPolicy** | `halt_for_human` | You explicitly set `behavioral_drift_flagged=True` with reason |

```python
runtime = SemarunRuntime(
    policy_mapping=PolicyMapping(
        tool_result_hash_mismatch="RevalidateWithPrompt",
        model_id_changed="FailFast",
        file_tree_hash_mismatch="FailFast",
        behavioral_drift_flagged="BehavioralDriftPolicy",
        outbound_payload_divergence="FailFast",
    ),
)
```

`PolicyOutcome` is declarative: `{ action, payload }`. Your host agent executes outcomes — Semarun never calls an LLM or mutates plan/intent inside the kernel.

### Typed state (`semarun/models/`)

| Type | Purpose |
|------|---------|
| `ActiveIntent` | Versioned intent text with schema version |
| `VerifiedWorkingMemory` | Named slots with schema ref, content hash, source step |
| `ToolResultCommitment` | Tool name, schema hash, result hash, `hash_exclude`, step id |
| `GreenCheckpointRef` | User/policy-marked last-known-good checkpoint |
| `Checkpoint` | Frozen snapshot of typed `AgentState` + artifact refs |

## Where Semarun Sits in the Stack

Semarun is **framework-agnostic infrastructure** — embed it in any Python agent loop.

| Category | Examples | Semarun role |
|----------|----------|--------------|
| Durable execution | Temporal, Restate | In-process alternative: no workers, no replay journal — artifact diff + checkpoint |
| Agent frameworks | LangGraph, PydanticAI, CrewAI | State kernel beneath your loop; you wire policy outcomes back in |
| CI / sandbox checkpoint | OS-level sandbox checkpoint/restore tools | Semarun is application-level state + policy hooks, not OS-level CRIU/fork restore |
| Memory / RAG | Mem0, Zep | Runtime execution state + tool commitments — not long-term user facts |
| Inference serving | vLLM, SGLang | Checkpoints Python agent state, not model KV tensors |

> Unlike distributed orchestrators that replay function calls, Semarun diffs **artifacts** (tool results, schemas, file trees, model IDs) and returns **policy outcomes** for your agent to act on.

## Known limitations & roadmap

| Today | Planned |
|-------|---------|
| Single-writer SQLite for run metadata (WAL mode) | Pluggable `StorageBackend` for multi-sandbox co-location (R3) |
| Per-run ledger SQLite for COW blobs | Shared blob store backend |
| Daemon-proxy tested via mock control plane | Full FIFO daemon integration on Linux |
| Pre-1.0 APIs | Stable 1.0 after community feedback |

The main SQLite backend is single-writer — it will become the bottleneck if many agent sandboxes checkpoint concurrently on one host. The isolated `ledger_store.py` per run is a first step toward swappable storage; a pooled backend is on the roadmap.

## API Reference

| Method | Purpose |
|--------|---------|
| `SemarunRuntime(...)` | Create runtime with `PolicyMapping` and optional hooks |
| `runtime.create_run(intent, plan=...)` | Start a new agent run |
| `runtime.resume(run_id)` | Load latest checkpoint and resume |
| `run.step(type, name=...)` | Context manager for step boundaries (records to ledger) |
| `step.set_tool_result(..., outbound_request=..., explicit_side_effect=...)` | Commit tool output + outbound replay hash |
| `runtime.ledger.permit_replay(run_id, target, payload)` | Outbound replay guard (mechanical hash compare) |
| `run.checkpoint()` | Force a state snapshot |
| `run.pause()` | Pause run and checkpoint |
| `run.request_approval(action, payload)` | Human approval gate |
| `run.compute_divergence_matrix(ResumeArtifacts(...))` | Mechanical boolean matrix vs checkpoint |
| `run.route_policies(matrix)` | Dispatch matrix flags → `list[PolicyOutcome]` |
| `run.export_checkpoint_json(path)` | Export raw checkpoint JSON for debugging |

## Examples

```bash
python examples/outreach_agent.py
python examples/multi_vendor_interrupt.py
```

- **outreach_agent.py** — CRM lookup → LLM draft → approval gate → resume with tool drift → `RevalidateWithPrompt`
- **multi_vendor_interrupt.py** — mock OpenAI + Anthropic model IDs, tool schemas, file tree; crash → resume → matrix → policy routing

## Install & Development

```bash
pip install semarun
# or from source:
pip install -e ".[dev]"
pytest

# Reproduce README benchmark numbers:
python benchmarks/resume_savings.py
python benchmarks/overhead.py
```

## Prior art & acknowledgments

Semarun's design was **informed by** 2026 systems research on agent checkpointing. These are independent implementations — not ports of any released codebase, and correspondence to the papers is approximate rather than one-to-one.

| Paper | arXiv | Related ideas |
|-------|-------|---------------|
| **CRAB** | [2604.28138](https://arxiv.org/abs/2604.28138) | Sparse, side-effect-triggered checkpointing; non-blocking checkpoint writes; isolated per-sandbox storage |
| **DeltaBox** | [2605.22781](https://arxiv.org/abs/2605.22781) | Copy-on-write snapshot trees; daemon-proxy control-plane pattern |
| **ACRFence** | [2603.20625](https://arxiv.org/abs/2603.20625) | Outbound payload hashing to detect semantic rollback on replay |

If you build on published research, cite it. We cite these papers because they shaped our thinking — not because Semarun implements them verbatim.

## License

MIT
