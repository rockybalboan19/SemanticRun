"""Orchestrate all semarun benchmarks and write results.json."""

from __future__ import annotations

import json
from pathlib import Path

from bench_checkpoint_latency import run_benchmark as bench_latency
from bench_memory_footprint import run_benchmark as bench_memory
from bench_token_savings import run_benchmark as bench_tokens


def main() -> None:
    results = {
        "checkpoint_latency_ms": bench_latency(),
        "memory_rss_mb": bench_memory(),
        "token_savings": bench_tokens(),
    }
    out = Path(__file__).parent / "results.json"
    out.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print("[semarun Overhead Stats]")
    print(f"  Checkpoint Snapshot Latency:  ~{results['checkpoint_latency_ms']['p50']:.1f} ms (SQLite local write)")
    print(f"  Memory Overhead:              < {results['memory_rss_mb']['after_100_steps']:.1f} MB resident set size")
    print(f"  Token Savings on Resume:      up to {results['token_savings']['100_step_run_fail_at_99'] * 100:.0f}% of prior execution steps")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
