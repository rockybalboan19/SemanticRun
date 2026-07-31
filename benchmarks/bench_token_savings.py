"""Benchmark token/cost savings on resume vs full restart."""

from __future__ import annotations

TOKENS_PER_STEP = 2000


def savings_ratio(total_steps: int, fail_at: int) -> float:
    baseline = total_steps * TOKENS_PER_STEP
    semarun = (total_steps - fail_at + 1) * TOKENS_PER_STEP
    return (baseline - semarun) / baseline


def run_benchmark() -> dict[str, float]:
    return {
        "8_step_run_fail_at_7": savings_ratio(8, 7),
        "20_step_run_fail_at_10": savings_ratio(20, 10),
        "100_step_run_fail_at_99": savings_ratio(100, 99),
    }


if __name__ == "__main__":
    print(run_benchmark())
