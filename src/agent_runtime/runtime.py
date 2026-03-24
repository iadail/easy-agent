from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_common.models import ToolSpec
from agent_common.tools import ToolHandler, ToolRegistry
from agent_config.app import AppConfig, McpServerConfig, load_config
from agent_graph import AgentOrchestrator, GraphScheduler
from agent_integrations.mcp import McpClientManager, build_mcp_tool_name
from agent_integrations.plugins import InlineRuntimePlugin, RuntimePlugin, RuntimePluginHost
from agent_integrations.sandbox import SandboxManager, SandboxMode
from agent_integrations.skills import SkillLoader, SkillMetadata
from agent_integrations.storage import SQLiteRunStore
from agent_protocols.client import HttpModelClient


class EasyAgentRuntime:
    def __init__(
        self,
        config: AppConfig,
        model_client: Any,
        registry: ToolRegistry,
        store: SQLiteRunStore,
        sandbox_manager: SandboxManager,
        mcp_manager: McpClientManager,
        orchestrator: AgentOrchestrator,
        scheduler: GraphScheduler,
        skills: list[SkillMetadata] | None = None,
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.registry = registry
        self.store = store
        self.sandbox_manager = sandbox_manager
        self.mcp_manager = mcp_manager
        self.orchestrator = orchestrator
        self.scheduler = scheduler
        self.skills = skills or []
        self.loaded_sources: list[str] = []
        self._loaded_skill_paths: set[Path] = set()
        self._plugin_host = RuntimePluginHost(self)
        self._started = False
        self._bound_mcp_tools: set[str] = set()

    def load(self, source: str | Path | RuntimePlugin) -> EasyAgentRuntime:
        descriptor = self._plugin_host.load(source)
        if descriptor not in self.loaded_sources:
            self.loaded_sources.append(descriptor)
        return self

    def register_skill_path(self, path: Path) -> list[SkillMetadata]:
        if self._started:
            raise RuntimeError('Skills must be registered before runtime.start()')
        resolved_path = path.resolve()
        if resolved_path in self._loaded_skill_paths:
            return []
        loader = SkillLoader([resolved_path], self.config.security.allowed_commands, self.sandbox_manager)
        loaded = loader.register(self.registry)
        self.skills.extend(loaded)
        self._loaded_skill_paths.add(resolved_path)
        return loaded

    def register_mcp_server(self, config: McpServerConfig) -> None:
        if self._started:
            raise RuntimeError('MCP servers must be registered before runtime.start()')
        self.mcp_manager.add_server(config)

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self.registry.register(spec, handler)

    def set_sandbox_mode(self, mode: str | SandboxMode) -> None:
        self.sandbox_manager.mode = SandboxMode(mode)

    async def start(self) -> None:
        await self.mcp_manager.start()
        await self._bind_mcp_tools()
        self._started = True

    async def _bind_mcp_tools(self) -> None:
        servers = await self.mcp_manager.list_servers()
        for server_name, tools in servers.items():
            for tool in tools:
                registry_name = build_mcp_tool_name(server_name, tool.name)
                if registry_name in self._bound_mcp_tools:
                    continue

                async def _handler(
                    arguments: dict[str, Any],
                    context: Any,
                    *,
                    bound_server: str = server_name,
                    bound_tool: str = tool.name,
                ) -> Any:
                    del context
                    return await self.mcp_manager.call_tool(bound_server, bound_tool, arguments)

                self.registry.register(
                    ToolSpec(
                        name=registry_name,
                        description=f"MCP tool {server_name}/{tool.name}: {tool.description}",
                        input_schema=tool.input_schema,
                    ),
                    _handler,
                )
                self._bound_mcp_tools.add(registry_name)

    async def run(self, input_text: str) -> dict[str, Any]:
        if not self._started:
            await self.start()
        return await self.scheduler.run(input_text)

    async def aclose(self) -> None:
        await self.mcp_manager.aclose()
        await self.model_client.aclose()
        self._started = False


def build_runtime_from_config(config: AppConfig) -> EasyAgentRuntime:
    working_root = Path(config.security.sandbox.working_root) if config.security.sandbox.working_root else None
    sandbox_manager = SandboxManager(
        mode=config.security.sandbox.mode,
        targets=config.security.sandbox.targets,
        env_allowlist=config.security.sandbox.env_allowlist,
        working_root=working_root,
        windows_sandbox_fallback=config.security.sandbox.windows_sandbox_fallback,
    )
    registry = ToolRegistry()
    store = SQLiteRunStore(Path(config.storage.path), config.storage.database)
    mcp_manager = McpClientManager([], sandbox_manager)
    model_client = HttpModelClient(config.model)
    orchestrator = AgentOrchestrator(config, model_client, registry, store)
    scheduler = GraphScheduler(config, registry, orchestrator, store, mcp_manager)
    runtime = EasyAgentRuntime(config, model_client, registry, store, sandbox_manager, mcp_manager, orchestrator, scheduler)
    for plugin_source in config.plugins:
        runtime.load(plugin_source)
    if config.skills:
        runtime.load(InlineRuntimePlugin(skill_paths=[Path(item.path) for item in config.skills]))
    if config.mcp:
        runtime.load(InlineRuntimePlugin(mcp_servers=config.mcp))
    orchestrator.register_subagent_tools()
    return runtime


def build_runtime(config_path: str | Path) -> EasyAgentRuntime:
    return build_runtime_from_config(load_config(config_path))

