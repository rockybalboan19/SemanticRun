#!/usr/bin/env python3
"""
Reproduce resume-vs-restart LLM step counts for the README worked example.

Scenario: 8-step agent run, crash during step 7.
- Naive restart: re-executes all 8 LLM steps.
- Semarun resume: reloads checkpoint after step 6, re-runs step 7 only.

Run:
    python benchmarks/resume_savings.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semarun import SemarunRuntime


def simulate_llm_steps(run, steps: range) -> int:
    """Return count of llm_call steps executed."""
    count = 0
    for i in steps:
        with run.step("llm_call", name=f"step_{i}") as step:
            run.state.working_memory.set_slot(f"output_{i}", f"result_{i}", step_id=step.step_id or "")
            # Force checkpoint so resume has a known-good boundary (simulates side-effect step).
            if i < 7:
                run.checkpoint()
        count += 1
    return count


def main() -> None:
    total_steps = 8
    crash_at = 7  # 1-indexed: crash during step 7 after steps 1-6 completed

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "bench.db"
        runtime = SemarunRuntime(str(db), async_checkpoints=False)

        # --- Naive restart: full re-run from step 1 ---
        naive_run = runtime.create_run(intent="8-step workflow", plan=[f"step_{i}" for i in range(1, total_steps + 1)])
        naive_calls = simulate_llm_steps(naive_run, range(1, total_steps + 1))
        runtime.complete(naive_run)

        # --- Semarun: run through step 6, checkpoint, resume, redo step 7 only ---
        sem_run = runtime.create_run(intent="8-step workflow", plan=[f"step_{i}" for i in range(1, total_steps + 1)])
        completed_before_crash = simulate_llm_steps(sem_run, range(1, crash_at))
        run_id = sem_run.id
        runtime.close()

        runtime2 = SemarunRuntime(str(db), async_checkpoints=False)
        resumed = runtime2.resume(run_id)
        resume_calls = simulate_llm_steps(resumed, range(crash_at, crash_at + 1))
        runtime2.complete(resumed)
        runtime2.close()

    steps_skipped = total_steps - resume_calls
    pct_saved = 100.0 * steps_skipped / total_steps

    print("Resume savings benchmark (reproducible)")
    print("=" * 44)
    print(f"Scenario:       {total_steps}-step run, crash during step {crash_at}")
    print(f"Naive restart:  {naive_calls} LLM steps re-executed")
    print(f"Semarun resume: {resume_calls} LLM step re-executed ({completed_before_crash} prior steps restored from checkpoint)")
    print(f"Steps skipped:  {steps_skipped} of {total_steps} ({pct_saved:.1f}%)")
    print()
    print("ASCII (from script output):")
    bar_naive = "?" * naive_calls
    bar_resume = "?" * resume_calls
    print(f"Naive restart (step {crash_at} crash):  {bar_naive}  {naive_calls} LLM calls")
    print(f"Semarun resume:                {bar_resume}  {resume_calls} LLM call(s)")


if __name__ == "__main__":
    main()
