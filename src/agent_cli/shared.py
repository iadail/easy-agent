from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from agent_runtime import EasyAgentRuntime, build_runtime


async def with_runtime(config_path: str, callback: Callable[[EasyAgentRuntime], Awaitable[Any]]) -> Any:
    runtime = build_runtime(config_path)
    try:
        await runtime.start()
        return await callback(runtime)
    finally:
        await runtime.aclose()

