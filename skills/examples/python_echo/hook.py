from __future__ import annotations

from agent_common.models import RunContext


def run(arguments: dict[str, object], context: RunContext) -> dict[str, object]:
    return {
        "echo": arguments.get("prompt", ""),
        "node_id": context.node_id,
        "run_id": context.run_id,
    }

