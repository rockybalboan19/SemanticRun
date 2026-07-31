#!/usr/bin/env python3
"""
Measure checkpoint snapshot latency (local SQLite, single run).

Run:
    python benchmarks/overhead.py
"""

from __future__ import annotations

import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from semarun import SemarunRuntime


def main() -> None:
    samples: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "overhead.db"
        runtime = SemarunRuntime(str(db), async_checkpoints=False)
        run = runtime.create_run(intent="overhead probe")
        for _ in range(20):
            with run.step("tool_call", name="write_file") as step:
                step.set_tool_result(
                    "write_file",
                    {"ok": True},
                    explicit_side_effect="filesystem",
                    outbound_request={"path": "x"},
                )
            # SIDE_EFFECT_BOUNDARY triggers checkpoint; measure last one.
            t0 = time.perf_counter()
            run.checkpoint()
            samples.append((time.perf_counter() - t0) * 1000)
        runtime.close()

    p50 = statistics.median(samples)
    p95 = sorted(samples)[int(len(samples) * 0.95) - 1]
    print("Checkpoint overhead benchmark (reproducible)")
    print("=" * 44)
    print(f"Samples:  {len(samples)} manual checkpoints")
    print(f"p50:      {p50:.2f} ms")
    print(f"p95:      {p95:.2f} ms")
    print(f"min/max:  {min(samples):.2f} / {max(samples):.2f} ms")


if __name__ == "__main__":
    main()
