from __future__ import annotations

from pathlib import Path
from typing import Any

from easy_agent.config import AppConfig, McpServerConfig, load_config
from easy_agent.graph import AgentOrchestrator, GraphScheduler
from easy_agent.mcp import McpClientManager
from easy_agent.models import ToolSpec
from easy_agent.plugins import InlineRuntimePlugin, RuntimePlugin, RuntimePluginHost
from easy_agent.protocols import HttpModelClient
from easy_agent.sandbox import SandboxManager, SandboxMode
from easy_agent.skills import SkillLoader, SkillMetadata
from easy_agent.storage import SQLiteRunStore
from easy_agent.tools import ToolHandler, ToolRegistry


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

    def load(self, source: str | Path | RuntimePlugin) -> EasyAgentRuntime:
        descriptor = self._plugin_host.load(source)
        if descriptor not in self.loaded_sources:
            self.loaded_sources.append(descriptor)
        return self

    def register_skill_path(self, path: Path) -> list[SkillMetadata]:
        resolved_path = path.resolve()
        if resolved_path in self._loaded_skill_paths:
            return []
        loader = SkillLoader(
            [resolved_path],
            self.config.security.allowed_commands,
            self.sandbox_manager,
        )
        loaded = loader.register(self.registry)
        self.skills.extend(loaded)
        self._loaded_skill_paths.add(resolved_path)
        return loaded

    def register_mcp_server(self, config: McpServerConfig) -> None:
        self.mcp_manager.add_server(config)

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self.registry.register(spec, handler)

    def set_sandbox_mode(self, mode: str | SandboxMode) -> None:
        self.sandbox_manager.mode = SandboxMode(mode)

    async def start(self) -> None:
        await self.mcp_manager.start()
        self._started = True

    async def run(self, input_text: str) -> dict[str, Any]:
        return await self.scheduler.run(input_text)

    async def aclose(self) -> None:
        await self.mcp_manager.aclose()
        await self.model_client.aclose()
        self._started = False



def build_runtime_from_config(config: AppConfig) -> EasyAgentRuntime:
    working_root = None
    if config.security.sandbox.working_root is not None:
        working_root = Path(config.security.sandbox.working_root)
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
    runtime = EasyAgentRuntime(
        config,
        model_client,
        registry,
        store,
        sandbox_manager,
        mcp_manager,
        orchestrator,
        scheduler,
    )
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

