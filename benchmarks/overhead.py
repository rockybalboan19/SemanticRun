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

SAMPLES = 300


def main() -> None:
    samples: list[float] = []
    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "overhead.db"
        runtime = SemarunRuntime(str(db), async_checkpoints=False)
        run = runtime.create_run(intent="overhead probe")
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            run.checkpoint()
            samples.append((time.perf_counter() - t0) * 1000)
        runtime.close()

    ordered = sorted(samples)
    p95_idx = max(0, int(len(ordered) * 0.95) - 1)
    print("Checkpoint overhead benchmark (reproducible)")
    print("=" * 44)
    print(f"Samples:  {len(samples)} manual checkpoints")
    print(f"median:   {statistics.median(samples):.2f} ms")
    print(f"p95:      {ordered[p95_idx]:.2f} ms")
    print(f"min/max:  {min(samples):.2f} / {max(samples):.2f} ms")


if __name__ == "__main__":
    main()
