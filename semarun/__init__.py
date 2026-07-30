"""Semarun — semantic checkpointing runtime for long-running agents."""

from semarun.kernel.run_handle import RunHandle, StateMutationError
from semarun.models import (
    AgentState,
    Checkpoint,
    ContinuationPolicy,
    ContinuationResult,
    DivergenceAction,
    DivergenceEvent,
    DivergenceKind,
    DivergenceReport,
    ResumeMode,
    RunStatus,
)
from semarun.runtime import SemarunRuntime

__all__ = [
    "AgentState",
    "Checkpoint",
    "ContinuationPolicy",
    "ContinuationResult",
    "DivergenceAction",
    "DivergenceEvent",
    "DivergenceKind",
    "DivergenceReport",
    "ResumeMode",
    "RunHandle",
    "RunStatus",
    "SemarunRuntime",
    "StateMutationError",
]

__version__ = "0.1.0"
