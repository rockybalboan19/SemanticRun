"""Benchmark checkpoint write latency."""

from __future__ import annotations

import statistics
import tempfile
import time
from pathlib import Path

from semarun import SemarunRuntime
from semarun.models.state import Fact


def run_benchmark() -> dict[str, float]:
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "bench.db"
        runtime = SemarunRuntime(str(db))
        run = runtime.create_run(
            intent="Benchmark latency run with realistic payload",
            plan=["research", "draft", "approve", "send"],
        )
        run.state.established_facts.append(Fact(fact="Lead at Company X", source="crm", confidence=0.94))
        run.state.working_memory = {
            "lead_profile": "Jane Doe, VP Engineering at Company X",
            "draft_email": "Hi Jane, welcome aboard!" * 20,
            "open_questions": ["Should tone be formal?"],
        }
        with run.step("tool_call", name="crm_lookup") as step:
            step.set_tool_result("crm_lookup", {"id": "lead_42", "company": "Company X"})

        for _ in range(10):
            run.checkpoint()

        timings: list[float] = []
        for _ in range(1000):
            start = time.perf_counter()
            run.checkpoint()
            timings.append((time.perf_counter() - start) * 1000)

        runtime.close()

    timings.sort()
    n = len(timings)
    return {
        "p50": statistics.median(timings),
        "p95": timings[int(n * 0.95)],
        "p99": timings[int(n * 0.99)],
    }


if __name__ == "__main__":
    print(run_benchmark())
