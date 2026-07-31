"""Tool result hashing with flexible canonicalization."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from typing import Any


def _strip_keys(value: Any, exclude: list[str]) -> Any:
    if not exclude:
        return value
    if isinstance(value, dict):
        result = {}
        for key, val in value.items():
            if key in exclude:
                continue
            nested_exclude = [e.split(".", 1)[1] for e in exclude if e.startswith(f"{key}.")]
            if nested_exclude:
                result[key] = _strip_keys(val, nested_exclude)
            else:
                result[key] = val
        return result
    if isinstance(value, list):
        return [_strip_keys(item, exclude) for item in value]
    return value


def canonicalize_for_hash(
    result: Any,
    hash_exclude: list[str] | None = None,
    canonicalizer: Callable[[Any], Any] | None = None,
) -> Any:
    if canonicalizer is not None:
        return canonicalizer(result)
    return _strip_keys(result, hash_exclude or [])


def hash_tool_result(
    result: Any,
    hash_exclude: list[str] | None = None,
    canonicalizer: Callable[[Any], Any] | None = None,
) -> str:
    canonical = canonicalize_for_hash(result, hash_exclude, canonicalizer)
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def hash_outbound_request(payload: Any) -> str:
    """ACRFence: hash literal outbound request payload for replay comparison."""
    return hash_tool_result(payload)
