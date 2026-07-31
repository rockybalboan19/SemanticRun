"""Benchmark memory footprint over a 100-step run."""

from __future__ import annotations

import tempfile
import tracemalloc
from pathlib import Path

try:
    import psutil
except ImportError:
    psutil = None

from semarun import SemarunRuntime
from semarun.models.state import Fact


def _rss_mb() -> float:
    if psutil is None:
        return tracemalloc.get_traced_memory()[1] / (1024 * 1024)
    return psutil.Process().memory_info().rss / (1024 * 1024)


def run_benchmark() -> dict[str, float]:
    tracemalloc.start()
    baseline = _rss_mb()
    peak = baseline
    samples: dict[str, float] = {"baseline": baseline}

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "bench.db"
        runtime = SemarunRuntime(str(db))
        run = runtime.create_run(intent="Memory benchmark run", plan=["step"] * 100)

        for i in range(101):
            if i in (0, 25, 50, 75, 100):
                samples[f"step_{i}"] = _rss_mb() - baseline
                peak = max(peak, _rss_mb())
            if i == 100:
                break
            with run.step("tool_call", name=f"tool_{i}") as step:
                step.set_tool_result(f"tool_{i}", {"step": i, "data": "x" * 100})
                run.state.established_facts.append(
                    Fact(fact=f"fact_{i}", source="bench", confidence=0.9)
                )
                run.state.working_memory[f"key_{i}"] = f"value_{i}"

        runtime.close()

    tracemalloc.stop()
    return {
        "baseline": baseline,
        "after_100_steps": samples.get("step_100", samples.get("step_75", 0)),
        "peak": peak - baseline,
    }


if __name__ == "__main__":
    print(run_benchmark())
