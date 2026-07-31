"""Explicit behavioral drift policy - never inferred by kernel."""

from __future__ import annotations

from dataclasses import dataclass

from semarun.policies.contract import PolicyContext, PolicyOutcome


@dataclass
class BehavioralDriftPolicy:
    require_human: bool = True
    name: str = "BehavioralDriftPolicy"

    def execute(self, ctx: PolicyContext) -> PolicyOutcome:
        return PolicyOutcome(
            action="halt_for_human",
            hook_name=self.name,
            flag=ctx.flag,
            payload={
                "reason": ctx.current.behavioral_drift_reason,
                "require_human": self.require_human,
            },
            message="BehavioralDriftPolicy: explicit behavioral drift flagged by caller",
        )
