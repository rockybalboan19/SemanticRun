"""Read-only action classifier - explicit editable pattern list (DeltaBox-style)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from typing import Any


class ActionClass(str, Enum):
    READ_ONLY = "read_only"
    RECOVERY_RELEVANT = "recovery_relevant"


@dataclass(frozen=True)
class ReadOnlyPattern:
    """Match tool/command name and optional argument sub-patterns."""

    tool: str
    arg_patterns: tuple[str, ...] = ()
    command_regex: str | None = None


# Editable static list - no runtime judgment beyond pattern match.
READ_ONLY_PATTERNS: list[ReadOnlyPattern] = [
    ReadOnlyPattern("grep"),
    ReadOnlyPattern("cat"),
    ReadOnlyPattern("find"),
    ReadOnlyPattern("ls"),
    ReadOnlyPattern("head"),
    ReadOnlyPattern("tail"),
    ReadOnlyPattern("wc"),
    ReadOnlyPattern("git", arg_patterns=("diff", "status", "log", "show")),
    ReadOnlyPattern("pytest", arg_patterns=("--collect-only",)),
    ReadOnlyPattern("read_file"),
    ReadOnlyPattern("list_dir"),
    ReadOnlyPattern("search"),
    ReadOnlyPattern("glob"),
    ReadOnlyPattern("crm_lookup"),  # demo read-only tool surface
]


def _normalize_tool_name(name: str) -> str:
    base = name.strip().lower()
    if " " in base:
        base = base.split()[0]
    return base


def _args_text(args: Any) -> str:
    if args is None:
        return ""
    if isinstance(args, str):
        return args.lower()
    if isinstance(args, dict):
        parts = [str(v).lower() for v in args.values()]
        parts.extend(str(k).lower() for k in args.keys())
        return " ".join(parts)
    if isinstance(args, (list, tuple)):
        return " ".join(str(a).lower() for a in args)
    return str(args).lower()


def classify_action(
    tool_name: str,
    args: Any = None,
    *,
    explicit_side_effect: str | None = None,
) -> ActionClass:
    """
    Classify intended action before checkpointing.

    explicit_side_effect: caller-declared 'filesystem' | 'process' forces recovery path.
    """
    if explicit_side_effect in ("filesystem", "process", "external"):
        return ActionClass.RECOVERY_RELEVANT
    normalized = _normalize_tool_name(tool_name)
    args_text = _args_text(args)
    for pattern in READ_ONLY_PATTERNS:
        if normalized != pattern.tool and not normalized.endswith(f".{pattern.tool}"):
            continue
        if pattern.arg_patterns:
            if not any(p.lower() in args_text for p in pattern.arg_patterns):
                continue
        if pattern.command_regex and not re.search(pattern.command_regex, args_text):
            continue
        return ActionClass.READ_ONLY
    return ActionClass.RECOVERY_RELEVANT


def skips_full_checkpoint(tool_name: str, args: Any = None, **kwargs: Any) -> bool:
    return classify_action(tool_name, args, **kwargs) == ActionClass.READ_ONLY
