"""SemaFlow — semantic checkpointing runtime for long-running agents."""

from semaflow.kernel.run_handle import RunHandle, StateMutationError
from semaflow.models import (
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
from semaflow.runtime import SemaFlowRuntime

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
    "SemaFlowRuntime",
    "StateMutationError",
]

__version__ = "0.1.0"
