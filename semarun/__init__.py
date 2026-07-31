"""Semarun - vendor-neutral mechanical state kernel for stochastic agent execution."""

from semarun.kernel.ledger import ReplayAuthorization, SideEffectKind, SideEffectLedger
from semarun.kernel.runtime import DaemonProxyRuntime, InFlightBuffer, RequestKind
from semarun.kernel.run_handle import RunHandle, StateMutationError
from semarun.kernel.skip_rules import ActionClass, classify_action, skips_full_checkpoint
from semarun.models import (
    ActiveIntent,
    AgentState,
    Checkpoint,
    DivergenceMatrix,
    PolicyMapping,
    PolicyOutcome,
    ResumeArtifacts,
    RunStatus,
    ToolResultCommitment,
)
from semarun.policies.behavioral import BehavioralDriftPolicy
from semarun.policies.builtin import FailFast, RevalidateWithPrompt, StrictReset
from semarun.runtime import SemarunRuntime

__all__ = [
    "ActionClass",
    "ActiveIntent",
    "AgentState",
    "BehavioralDriftPolicy",
    "Checkpoint",
    "DaemonProxyRuntime",
    "DivergenceMatrix",
    "FailFast",
    "InFlightBuffer",
    "PolicyMapping",
    "PolicyOutcome",
    "ReplayAuthorization",
    "RevalidateWithPrompt",
    "RequestKind",
    "ResumeArtifacts",
    "RunHandle",
    "RunStatus",
    "SemarunRuntime",
    "SideEffectKind",
    "SideEffectLedger",
    "StateMutationError",
    "StrictReset",
    "ToolResultCommitment",
    "classify_action",
    "skips_full_checkpoint",
]

__version__ = "0.3.0"
