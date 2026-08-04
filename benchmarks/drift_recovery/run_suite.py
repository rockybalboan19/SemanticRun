#!/usr/bin/env python3
"""Run SemanticRun drift-recovery head-to-head suite and write results.json."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow `python benchmarks/drift_recovery/run_suite.py` from repo root.
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from benchmarks.drift_recovery import openrouter_client  # noqa: E402
from benchmarks.drift_recovery.scenarios import SCENARIOS, STRATEGIES  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).resolve().parent / "results.json",
    )
    parser.add_argument(
        "--scenarios",
        nargs="*",
        default=list(SCENARIOS),
        help="Subset of scenario names",
    )
    args = parser.parse_args()

    rows = []
    for name in args.scenarios:
        prepare = SCENARIOS[name]
        for strategy_name, strategy_fn in STRATEGIES.items():
            result = strategy_fn(name, prepare)
            rows.append(result.to_dict())
            mark = "OK" if result.correct else "FAIL"
            print(
                f"[{mark}] {name:22} {strategy_name:16} "
                f"safe={result.safe} drift={result.drift_detected} "
                f"skip={result.steps_skipped} reenter={result.steps_reentered} "
                f"{result.detail}"
            )

    by_strategy: dict[str, dict[str, int]] = {}
    for row in rows:
        s = by_strategy.setdefault(
            row["strategy"],
            {"safe": 0, "correct": 0, "total": 0, "drift_detected": 0},
        )
        s["total"] += 1
        s["safe"] += int(row["safe"])
        s["correct"] += int(row["correct"])
        s["drift_detected"] += int(row["drift_detected"])

    summary = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "openrouter": openrouter_client.available(),
        "model": openrouter_client.DEFAULT_MODEL,
        "by_strategy": by_strategy,
        "rows": rows,
    }
    args.out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {args.out}")
    print(json.dumps(by_strategy, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
