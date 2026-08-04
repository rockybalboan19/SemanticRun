"""Environment-enforced policy exceptions."""

from __future__ import annotations

from semanticrun.policies.contract import PolicyOutcome


class PolicyAbort(RuntimeError):
    """Raised when SemanticRun enforces a fail-fast / abort policy outcome."""

    def __init__(self, outcome: PolicyOutcome, message: str | None = None) -> None:
        self.outcome = outcome
        super().__init__(message or outcome.message or "Policy aborted run")
