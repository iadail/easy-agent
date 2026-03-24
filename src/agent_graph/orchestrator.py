from __future__ import annotations

from typing import Any

from agent_common.models import ChatMessage, RunContext, ToolSpec
from agent_common.tools import ToolHandler, ToolRegistry
from agent_config.app import AgentConfig, AppConfig
from agent_integrations.storage import SQLiteRunStore


class AgentOrchestrator:
    def __init__(
        self,
        config: AppConfig,
        model_client: Any,
        registry: ToolRegistry,
        store: SQLiteRunStore,
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.registry = registry
        self.store = store
        self.agents: dict[str, AgentConfig] = config.agent_map

    def register_subagent_tools(self) -> None:
        for agent in self.config.graph.agents:
            for sub_agent_name in agent.sub_agents:
                tool_name = f"subagent__{sub_agent_name}"
                if self.registry.has(tool_name):
                    continue
                self.registry.register(self._subagent_spec(tool_name, sub_agent_name), self._subagent_runner(sub_agent_name))

    def _subagent_runner(self, target_name: str) -> ToolHandler:
        async def _run(arguments: dict[str, Any], context: RunContext) -> Any:
            prompt = str(arguments.get("prompt", ""))
            next_context = RunContext(
                run_id=context.run_id,
                workdir=context.workdir,
                node_id=context.node_id,
                shared_state=context.shared_state,
                depth=context.depth + 1,
            )
            return await self.run_agent(target_name, prompt, next_context)

        return _run

    @staticmethod
    def _subagent_spec(tool_name: str, agent_name: str) -> ToolSpec:
        return ToolSpec(
            name=tool_name,
            description=f"Delegate work to sub-agent '{agent_name}'.",
            input_schema={
                "type": "object",
                "properties": {"prompt": {"type": "string"}},
                "required": ["prompt"],
            },
        )

    async def run_agent(self, name: str, prompt: str, context: RunContext) -> Any:
        if context.depth > 6:
            raise RuntimeError("Maximum sub-agent depth exceeded")
        agent = self.agents[name]
        tool_names = agent.tools + [f"subagent__{item}" for item in agent.sub_agents]
        tool_specs = self.registry.list_specs(tool_names)
        messages = [
            ChatMessage(role="system", content=agent.system_prompt),
            ChatMessage(role="user", content=prompt),
        ]
        for iteration in range(agent.max_iterations):
            self.store.record_event(
                context.run_id,
                "agent_request",
                {"agent": name, "iteration": iteration + 1, "prompt": prompt},
            )
            response = await self.model_client.complete(messages, tool_specs)
            self.store.record_event(
                context.run_id,
                "agent_response",
                {
                    "agent": name,
                    "text": response.text,
                    "tool_calls": [item.model_dump() for item in response.tool_calls],
                },
            )
            if not response.tool_calls:
                return response.text
            messages.append(ChatMessage(role="assistant", content=response.text, tool_calls=response.tool_calls))
            for tool_call in response.tool_calls:
                output = await self.registry.call(tool_call.name, tool_call.arguments, context)
                messages.append(
                    ChatMessage(
                        role="tool",
                        content=str(output),
                        name=tool_call.name,
                        tool_call_id=tool_call.id,
                    )
                )
        raise RuntimeError(f"Agent '{name}' exceeded max_iterations")

