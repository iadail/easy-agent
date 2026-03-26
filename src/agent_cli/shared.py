from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import anyio
from rich.console import Console

from agent_common.models import HumanRequest, HumanRequestStatus
from agent_runtime import EasyAgentRuntime, build_runtime


async def with_runtime(config_path: str, callback: Callable[[EasyAgentRuntime], Awaitable[Any]]) -> Any:
    runtime = build_runtime(config_path)
    try:
        await runtime.start()
        return await callback(runtime)
    finally:
        await runtime.aclose()


def build_cli_inline_resolver(console: Console) -> Callable[[HumanRequest], Awaitable[tuple[HumanRequestStatus, dict[str, Any] | None]]]:
    async def _resolve(request: HumanRequest) -> tuple[HumanRequestStatus, dict[str, Any] | None]:
        def _prompt() -> tuple[HumanRequestStatus, dict[str, Any] | None]:
            console.print(f"[bold yellow]Approval Required:[/bold yellow] {request.title}")
            console.print_json(json.dumps(request.payload, ensure_ascii=False))
            if request.kind == 'mcp_elicitation':
                action = console.input('Action [accept/decline/cancel]: ').strip().lower() or 'accept'
                if action not in {'accept', 'decline', 'cancel'}:
                    action = 'accept'
                content: dict[str, Any] = {}
                if action == 'accept':
                    raw = console.input('Structured JSON content (blank for {}): ').strip()
                    if raw:
                        content = json.loads(raw)
                if action == 'accept':
                    return HumanRequestStatus.APPROVED, {'action': action, 'content': content}
                return HumanRequestStatus.REJECTED, {'action': action, 'content': {}}
            answer = console.input('Approve? [y/N]: ').strip().lower()
            if answer in {'y', 'yes'}:
                return HumanRequestStatus.APPROVED, {'approved': True}
            return HumanRequestStatus.REJECTED, {'approved': False}

        return await anyio.to_thread.run_sync(_prompt)

    return _resolve
