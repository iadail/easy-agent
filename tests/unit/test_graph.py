from pathlib import Path

import pytest

from agent_common.models import (
    AssistantResponse,
    ChatMessage,
    Protocol,
    RunContext,
    ToolCall,
    ToolSpec,
)
from agent_common.tools import ToolRegistry
from agent_config.app import AppConfig, ModelConfig
from agent_graph import AgentOrchestrator, GraphScheduler
from agent_integrations.storage import SQLiteRunStore


class StubModelClient:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, messages: list[ChatMessage], tools: list[ToolSpec]) -> AssistantResponse:
        del messages, tools
        self.calls += 1
        if self.calls == 1:
            return AssistantResponse(
                text="",
                tool_calls=[ToolCall(id="1", name="python_echo", arguments={"prompt": "from-tool"})],
                protocol=Protocol.OPENAI,
            )
        return AssistantResponse(text="done", protocol=Protocol.OPENAI)

    async def aclose(self) -> None:
        return None


class DummyMcpManager:
    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, str]) -> dict[str, str]:
        return {"server": server_name, "tool": tool_name, **arguments}


@pytest.mark.asyncio
async def test_graph_scheduler_runs_agent_with_tool_loop(tmp_path: Path) -> None:
    config = AppConfig.model_validate(
        {
            "model": ModelConfig().model_dump(),
            "graph": {
                "entrypoint": "coordinator",
                "agents": [
                    {
                        "name": "coordinator",
                        "system_prompt": "system",
                        "tools": ["python_echo"],
                        "sub_agents": [],
                    }
                ],
                "nodes": [],
            },
            "skills": [],
            "mcp": [],
            "storage": {"path": str(tmp_path), "database": "state.db"},
            "security": {"allowed_commands": [["cmd", "/c", "echo"]]},
        }
    )
    registry = ToolRegistry()
    registry.register(
        ToolSpec(name="python_echo", description="Echo", input_schema={"type": "object"}),
        lambda arguments, context: {"echo": arguments["prompt"], "run_id": context.run_id},
    )
    store = SQLiteRunStore(tmp_path, "state.db")
    model_client = StubModelClient()
    orchestrator = AgentOrchestrator(config, model_client, registry, store)
    scheduler = GraphScheduler(config, registry, orchestrator, store, DummyMcpManager())

    result = await scheduler.run("hello")

    assert result["result"] == "done"


@pytest.mark.asyncio
async def test_graph_scheduler_retries_failed_nodes(tmp_path: Path) -> None:
    attempts = {"count": 0}
    config = AppConfig.model_validate(
        {
            "model": ModelConfig().model_dump(),
            "graph": {
                "entrypoint": "prepare",
                "agents": [],
                "nodes": [
                    {
                        "id": "prepare",
                        "type": "skill",
                        "target": "flaky",
                        "retries": 1,
                        "timeout_seconds": 5,
                    }
                ],
            },
            "skills": [],
            "mcp": [],
            "storage": {"path": str(tmp_path), "database": "state.db"},
            "security": {"allowed_commands": []},
        }
    )
    registry = ToolRegistry()

    async def flaky(arguments: dict[str, str], context: RunContext) -> str:
        del arguments, context
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise RuntimeError("boom")
        return "ok"

    registry.register(ToolSpec(name="flaky", description="Retry me", input_schema={"type": "object"}), flaky)
    store = SQLiteRunStore(tmp_path, "state.db")
    model_client = StubModelClient()
    orchestrator = AgentOrchestrator(config, model_client, registry, store)
    scheduler = GraphScheduler(config, registry, orchestrator, store, DummyMcpManager())

    result = await scheduler.run("hello")

    assert result["result"] == "ok"
    assert attempts["count"] == 2


