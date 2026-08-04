# Drift-recovery head-to-head

Day-one hook for SemanticRun: pause an agent mid-run, inject world drift, compare three strategies.

## Strategies

| Strategy | Behavior |
|----------|----------|
| `naive_restart` | Re-run the full plan from step 1 |
| `blind_resume` | Reload checkpoint state and continue (checkpointer-style) |
| `semanticrun` | `resume(artifacts=...)` with enforced policies + plan cursor |

## Scenarios

1. **model_id_swap** — model family/version changes between pause and resume  
2. **file_edit** — workspace file edited while waiting on approval  
3. **tool_schema_change** — tool schema hash changes  
4. **tool_result_drift** — CRM tool result no longer matches commitment  
5. **outbound_resynthesis** — re-synthesized outbound payload differs from committed bytes  

## Run

```bash
# stub LLM (CI-safe, no network)
python benchmarks/drift_recovery/run_suite.py

# live free models via OpenRouter
set OPENROUTER_API_KEY=...
python benchmarks/drift_recovery/run_suite.py
```

Writes `results.json` next to this README.
