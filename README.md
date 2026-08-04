<p align="center">
  <img src="https://raw.githubusercontent.com/rockybalboan19/SemanticRun/master/semanticrun-logo.png" alt="SemanticRun" width="360"/>
</p>

<p align="center">
  <strong>Temporal replays code. LangGraph orchestrates graphs.<br/>SemanticRun freezes what your agent already committed — and resumes with proof of what drifted.</strong>
</p>

<p align="center">
  <a href="https://www.python.org/downloads/"><img src="https://img.shields.io/badge/python-3.11+-blue.svg" alt="Python 3.11+"/></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-MIT-yellow.svg" alt="License: MIT"/></a>
  <a href="https://pypi.org/project/SemanticRun/"><img src="https://img.shields.io/pypi/v/SemanticRun.svg" alt="PyPI"/></a>
  <img src="https://img.shields.io/badge/status-0.4.0-orange.svg" alt="Status: 0.4.0"/>
</p>

<p align="center">
  Artifact-aware durable agent environment for Python.<br/>
  Survive crashes, human waits, model swaps, and tool drift — without redoing completed work or silently replaying side effects.
</p>

```python
from semanticrun import SemanticRun, PolicyMapping

env = SemanticRun.open("./runs.db")

run = env.start(
    intent="Onboard lead_42",
    plan=["research", "draft", "approve", "send"],
    policies=PolicyMapping(
        tool_result_hash_mismatch="revalidate",
        model_id_changed="fail_fast",
        outbound_payload_divergence="fail_fast",
    ),
)

for step in run.steps():  # completed steps skipped on resume
    if step.name == "research":
        lead = step.tool("crm_lookup", lambda: crm.lookup("lead_42"),
                         hash_exclude=["created_at"])
    elif step.name == "draft":
        draft = step.llm(lambda: llm.draft(lead), model="gpt-4.1")
        step.remember("draft_email", draft)
    elif step.name == "approve":
        step.require_approval("send_email", {"draft": draft})
    elif step.name == "send":
        step.tool("send_email", lambda: mail.send(draft),
                  side_effect="external",
                  outbound={"to": lead["email"], "body": draft})

run = env.resume(run_id, artifacts=...)  # matrix + policies; cursor continues
```

## Why SemanticRun

| | Temporal | LangGraph | **SemanticRun** |
|--|----------|-----------|-----------------|
| Core idea | Replay workflow code | Orchestrate agent graphs | Diff committed **artifacts** on resume |
| Durability | Event history | Checkpointers | Sync checkpoints + plan cursor |
| Drift (tools / models / files) | App-level | App-level | Divergence matrix + enforced policies |
| Side effects | Activity semantics | App-managed | Outbound payload hash gate |

Not a Temporal or LangGraph plugin — the environment your agent runs in when the question is *“what already happened, and is it still true?”*

## Install

```bash
pip install SemanticRun

# from source:
pip install -e ".[dev]"
pytest
python examples/survive_the_swap.py
```

## What you get

- **Durable plan cursor** — `run.steps()` skips finished work after resume  
- **Sync checkpoints** — written before the step returns (SQLite)  
- **Divergence matrix** — mechanical diffs; no LLM guessing  
- **Enforced policies** — `fail_fast` / `revalidate` / `strict_reset`  
- **Outbound replay gate** — refuse divergent external side effects  

## License

MIT
