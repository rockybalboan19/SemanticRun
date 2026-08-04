"""Tests for policy router."""

from semanticrun.audit.log import AuditLog
from semanticrun.models.artifacts import ResumeArtifacts
from semanticrun.models.checkpoint import Checkpoint
from semanticrun.models.divergence import DivergenceMatrix
from semanticrun.models.state import AgentState, RunStatus
from semanticrun.policies.builtin import FailFast, RevalidateWithPrompt
from semanticrun.policies.contract import PolicyRegistry
from semanticrun.policies.mapping import PolicyMapping
from semanticrun.resume.router import PolicyRouter
from semanticrun.storage.sqlite import SQLiteStorage


def test_router_dispatches_registered_hook():
    storage = SQLiteStorage(":memory:")
    audit = AuditLog(storage)
    registry = PolicyRegistry()
    registry.register(FailFast())
    registry.register(RevalidateWithPrompt())
    router = PolicyRouter(registry, audit)

    state = AgentState.create("intent")
    checkpoint = Checkpoint(run_id="run_1", status=RunStatus.PAUSED, state=state)
    matrix = DivergenceMatrix(model_id_changed=True)
    mapping = PolicyMapping(model_id_changed="FailFast")

    outcomes = router.route("run_1", matrix, checkpoint, mapping, ResumeArtifacts())
    assert len(outcomes) == 1
    assert outcomes[0].hook_name == "FailFast"
    storage.close()


def test_router_no_guess_on_clean_matrix():
    storage = SQLiteStorage(":memory:")
    audit = AuditLog(storage)
    registry = PolicyRegistry()
    router = PolicyRouter(registry, audit)
    state = AgentState.create("intent")
    checkpoint = Checkpoint(run_id="run_1", status=RunStatus.PAUSED, state=state)
    outcomes = router.route(
        "run_1",
        DivergenceMatrix(),
        checkpoint,
        PolicyMapping(),
        ResumeArtifacts(),
    )
    assert outcomes[0].action == "continue"
    storage.close()
