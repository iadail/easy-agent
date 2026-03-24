from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator

from easy_agent.models import NodeType, Protocol
from easy_agent.sandbox import SandboxMode, SandboxTarget


def _expand_env(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(value)
    if isinstance(value, list):
        return [_expand_env(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand_env(item) for key, item in value.items()}
    return value


class ModelConfig(BaseModel):
    provider: str = "deepseek"
    protocol: Protocol = Protocol.AUTO
    model: str = "deepseek-chat"
    base_url: str = "https://api.deepseek.com"
    api_key_env: str = "DEEPSEEK_API_KEY"
    timeout_seconds: float = 60.0
    max_tokens: int = 2048
    temperature: float = 0.1
    extra_headers: dict[str, str] = Field(default_factory=dict)


class AgentConfig(BaseModel):
    name: str
    system_prompt: str = ""
    tools: list[str] = Field(default_factory=list)
    sub_agents: list[str] = Field(default_factory=list)
    max_iterations: int = 6


class GraphNodeConfig(BaseModel):
    id: str
    type: NodeType
    target: str | None = None
    deps: list[str] = Field(default_factory=list)
    input_template: str = "{input}"
    retries: int = 0
    timeout_seconds: float = 30.0
    arguments: dict[str, Any] = Field(default_factory=dict)


class GraphConfig(BaseModel):
    name: str = "default"
    entrypoint: str
    agents: list[AgentConfig] = Field(default_factory=list)
    nodes: list[GraphNodeConfig] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_entrypoint(self) -> GraphConfig:
        node_ids = {node.id for node in self.nodes}
        agent_names = {agent.name for agent in self.agents}
        if self.entrypoint not in node_ids and self.entrypoint not in agent_names:
            raise ValueError("graph.entrypoint must match a node id or agent name")
        return self


class SkillSourceConfig(BaseModel):
    path: str


class McpServerConfig(BaseModel):
    name: str
    transport: str
    command: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    rpc_url: str | None = None
    sse_url: str | None = None
    timeout_seconds: float = 15.0


class StorageConfig(BaseModel):
    path: str = ".easy-agent"
    database: str = "state.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"


class SandboxConfig(BaseModel):
    mode: SandboxMode = SandboxMode.AUTO
    targets: list[SandboxTarget] = Field(
        default_factory=lambda: [SandboxTarget.COMMAND_SKILL, SandboxTarget.STDIO_MCP]
    )
    env_allowlist: list[str] = Field(
        default_factory=lambda: [
            "PATH",
            "PATHEXT",
            "SYSTEMROOT",
            "WINDIR",
            "COMSPEC",
            "TEMP",
            "TMP",
            "DEEPSEEK_API_KEY",
        ]
    )
    working_root: str | None = None
    windows_sandbox_fallback: SandboxMode = SandboxMode.PROCESS


class SecurityConfig(BaseModel):
    allowed_commands: list[list[str]] = Field(default_factory=list)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)


class AppConfig(BaseModel):
    model: ModelConfig = Field(default_factory=ModelConfig)
    graph: GraphConfig
    plugins: list[str] = Field(default_factory=list)
    skills: list[SkillSourceConfig] = Field(default_factory=list)
    mcp: list[McpServerConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)

    @property
    def agent_map(self) -> dict[str, AgentConfig]:
        return {agent.name: agent for agent in self.graph.agents}



def load_config(path: str | Path) -> AppConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    expanded = _expand_env(raw)
    return AppConfig.model_validate(expanded)

