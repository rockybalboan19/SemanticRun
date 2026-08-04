"""Built-in explicit policy hooks."""

from __future__ import annotations

from dataclasses import dataclass

from semanticrun.policies.contract import PolicyContext, PolicyOutcome


@dataclass
class FailFast:
    name: str = "FailFast"

    def execute(self, ctx: PolicyContext) -> PolicyOutcome:
        return PolicyOutcome(
            action="abort",
            hook_name=self.name,
            flag=ctx.flag,
            payload={"require_human_reauth": True},
            message="FailFast: abort run and require human re-authentication",
        )


@dataclass
class RevalidateWithPrompt:
    template: str = ""
    assertions: list[str] | None = None
    name: str = "RevalidateWithPrompt"

    def execute(self, ctx: PolicyContext) -> PolicyOutcome:
        template = self.template or ctx.revalidation_template
        assertions = list(self.assertions or ctx.assertions)
        tools = [
            tool
            for tool, changed in ctx.matrix.tool_result_hash_mismatch.items()
            if changed
        ]
        return PolicyOutcome(
            action="run_assertions",
            hook_name=self.name,
            flag=ctx.flag,
            payload={
                "template": template,
                "assertions": assertions,
                "tools_to_revalidate": tools,
            },
            message="RevalidateWithPrompt: run developer assertions before continuing",
        )


@dataclass
class StrictReset:
    name: str = "StrictReset"

    def execute(self, ctx: PolicyContext) -> PolicyOutcome:
        checkpoint_id = ctx.last_green_checkpoint_id
        if checkpoint_id is None and ctx.checkpoint.state.green_checkpoint:
            checkpoint_id = ctx.checkpoint.state.green_checkpoint.checkpoint_id
        return PolicyOutcome(
            action="load_checkpoint",
            hook_name=self.name,
            flag=ctx.flag,
            payload={"checkpoint_id": checkpoint_id},
            message="StrictReset: load last known green checkpoint",
        )
