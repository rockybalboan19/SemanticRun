"""Mechanical artifact references for diffing."""

from __future__ import annotations

from pydantic import BaseModel, Field


class ToolSchemaRef(BaseModel):
    tool_name: str
    schema_hash: str


class FileTreeSnapshot(BaseModel):
    root: str
    merkle_hash: str
    file_count: int = 0


class ModelIdRef(BaseModel):
    model_family: str = ""
    model_version: str = ""
    fingerprint: str = ""


class ResumeArtifacts(BaseModel):
    """Current environment artifacts supplied by the host agent at resume time."""

    model_id: ModelIdRef | None = None
    tool_schemas: dict[str, ToolSchemaRef] = Field(default_factory=dict)
    tool_results: dict[str, object] = Field(default_factory=dict)
    file_tree: FileTreeSnapshot | None = None
    intent_text: str | None = None
    plan: list[str] | None = None
    approval_status: str | None = None
    behavioral_drift_flagged: bool = False
    behavioral_drift_reason: str = ""
    outbound_payloads: dict[str, object] = Field(default_factory=dict)
