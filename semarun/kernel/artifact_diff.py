"""Pure mechanical artifact diffing - zero judgment."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from semarun.checkpoint.hashing import hash_tool_result
from semarun.models.artifacts import FileTreeSnapshot, ModelIdRef, ToolSchemaRef
from semarun.models.state import ToolResultCommitment


def hash_schema(schema: dict[str, Any] | str) -> str:
    if isinstance(schema, str):
        payload = schema
    else:
        payload = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_file_tree(root: str | Path) -> FileTreeSnapshot:
    root_path = Path(root)
    hashes: list[str] = []
    file_count = 0
    if root_path.is_dir():
        for path in sorted(root_path.rglob("*")):
            if path.is_file():
                file_count += 1
                content = path.read_bytes()
                rel = str(path.relative_to(root_path))
                hashes.append(f"{rel}:{hashlib.sha256(content).hexdigest()}")
    merkle = hashlib.sha256("\n".join(hashes).encode("utf-8")).hexdigest()
    return FileTreeSnapshot(root=str(root_path), merkle_hash=merkle, file_count=file_count)


def diff_tool_schemas(
    checkpoint: dict[str, ToolSchemaRef],
    current: dict[str, ToolSchemaRef],
) -> tuple[bool, dict[str, Any]]:
    changed = False
    deltas: dict[str, Any] = {}
    all_names = set(checkpoint) | set(current)
    for name in sorted(all_names):
        ckpt = checkpoint.get(name)
        cur = current.get(name)
        if ckpt is None or cur is None or ckpt.schema_hash != cur.schema_hash:
            changed = True
            deltas[name] = {
                "checkpoint_hash": ckpt.schema_hash if ckpt else None,
                "current_hash": cur.schema_hash if cur else None,
            }
    return changed, deltas


def diff_tool_results(
    commitments: dict[str, ToolResultCommitment],
    current_results: dict[str, Any],
) -> tuple[dict[str, bool], dict[str, Any]]:
    mismatches: dict[str, bool] = {}
    deltas: dict[str, Any] = {}
    for name, commitment in commitments.items():
        if name not in current_results:
            continue
        new_hash = hash_tool_result(
            current_results[name],
            hash_exclude=commitment.hash_exclude,
        )
        mismatches[name] = new_hash != commitment.result_hash
        if mismatches[name]:
            deltas[name] = {
                "expected_hash": commitment.result_hash,
                "actual_hash": new_hash,
            }
    return mismatches, deltas


def diff_file_trees(
    checkpoint: FileTreeSnapshot | None,
    current: FileTreeSnapshot | None,
) -> tuple[bool, dict[str, Any]]:
    if checkpoint is None and current is None:
        return False, {}
    if checkpoint is None or current is None:
        return True, {"checkpoint": checkpoint.model_dump() if checkpoint else None, "current": current.model_dump() if current else None}
    changed = checkpoint.merkle_hash != current.merkle_hash
    deltas = {}
    if changed:
        deltas = {
            "checkpoint_hash": checkpoint.merkle_hash,
            "current_hash": current.merkle_hash,
        }
    return changed, deltas


def diff_model_ids(
    checkpoint: ModelIdRef,
    current: ModelIdRef,
) -> tuple[bool, dict[str, Any]]:
    changed = (
        checkpoint.model_family != current.model_family
        or checkpoint.model_version != current.model_version
        or checkpoint.fingerprint != current.fingerprint
    )
    deltas = {}
    if changed:
        deltas = {"checkpoint": checkpoint.model_dump(), "current": current.model_dump()}
    return changed, deltas


def model_context_to_ref(family: str, version: str, fingerprint: str = "") -> ModelIdRef:
    return ModelIdRef(model_family=family, model_version=version, fingerprint=fingerprint)
