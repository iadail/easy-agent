from __future__ import annotations

import inspect
from collections.abc import Callable
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

import yaml
from pydantic import BaseModel, Field

from agent_common.models import ToolSpec
from agent_common.tools import ToolHandler
from agent_config.app import McpServerConfig


class LocalPluginManifest(BaseModel):
    skills: list[str] = Field(default_factory=list)
    mcp: list[McpServerConfig] = Field(default_factory=list)


@runtime_checkable
class RuntimePlugin(Protocol):
    def register(self, host: RuntimePluginHost) -> None: ...


class InlineRuntimePlugin:
    def __init__(
        self,
        skill_paths: list[Path] | None = None,
        optional_skill_paths: list[Path] | None = None,
        mcp_servers: list[McpServerConfig] | None = None,
    ) -> None:
        self.skill_paths = skill_paths or []
        self.optional_skill_paths = optional_skill_paths or []
        self.mcp_servers = mcp_servers or []

    def register(self, host: RuntimePluginHost) -> None:
        for skill_path in self.skill_paths:
            host.register_skill_path(skill_path, optional=False)
        for skill_path in self.optional_skill_paths:
            host.register_skill_path(skill_path, optional=True)
        for server in self.mcp_servers:
            host.register_mcp_server(server)


class FunctionRuntimePlugin:
    def __init__(self, callback: Callable[[RuntimePluginHost], None]) -> None:
        self.callback = callback

    def register(self, host: RuntimePluginHost) -> None:
        self.callback(host)


class RuntimePluginHost:
    def __init__(self, runtime: Any) -> None:
        self.runtime = runtime

    def load(self, source: str | Path | RuntimePlugin) -> str:
        plugin = self._resolve_plugin(source)
        plugin.register(self)
        return self._describe_source(source)

    def register_skill_path(self, path: Path, optional: bool = False) -> None:
        self.runtime.register_skill_path(path, optional=optional)

    def register_mcp_server(self, config: McpServerConfig) -> None:
        self.runtime.register_mcp_server(config)

    def register_tool(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self.runtime.register_tool(spec, handler)

    def set_sandbox_mode(self, mode: str) -> None:
        self.runtime.set_sandbox_mode(mode)

    def _resolve_plugin(self, source: str | Path | RuntimePlugin) -> RuntimePlugin:
        if isinstance(source, Path):
            return self._resolve_local_path(source)
        if isinstance(source, str):
            candidate = Path(source)
            if candidate.exists():
                return self._resolve_local_path(candidate)
            return self._resolve_entry_point(source)
        if isinstance(source, RuntimePlugin):
            return source
        raise TypeError(f"Unsupported plugin source: {source!r}")

    def _resolve_local_path(self, source: Path) -> RuntimePlugin:
        path = source.resolve()
        if path.is_file():
            if path.name == "skill.yaml":
                return InlineRuntimePlugin(skill_paths=[path.parent])
            return self._resolve_manifest(path)

        manifest = self._find_manifest(path)
        if manifest is not None:
            return self._resolve_manifest(manifest)
        has_skill_manifest = (path / "skill.yaml").exists() or any(
            (child / "skill.yaml").exists() for child in path.iterdir() if child.is_dir()
        )
        if has_skill_manifest:
            return InlineRuntimePlugin(skill_paths=[path])
        raise ValueError(f"Could not infer plugin type from path: {path}")

    def _resolve_manifest(self, manifest_path: Path) -> RuntimePlugin:
        with manifest_path.open("r", encoding="utf-8") as handle:
            payload = yaml.safe_load(handle) or {}
        manifest = LocalPluginManifest.model_validate(payload)
        base_dir = manifest_path.parent
        skill_paths = [(base_dir / entry).resolve() for entry in manifest.skills]
        return InlineRuntimePlugin(skill_paths=skill_paths, mcp_servers=manifest.mcp)

    def _resolve_entry_point(self, source: str) -> RuntimePlugin:
        entry_points = importlib_metadata.entry_points()
        if hasattr(entry_points, "select"):
            matches = list(entry_points.select(group="agent_runtime.plugins", name=source))
        else:
            legacy_entry_points = cast(dict[str, list[Any]], entry_points)
            matches = [item for item in legacy_entry_points.get("agent_runtime.plugins", []) if item.name == source]
        if not matches:
            raise ValueError(f"Plugin entry point not found: {source}")
        loaded = matches[0].load()
        if isinstance(loaded, RuntimePlugin):
            return loaded
        if inspect.isclass(loaded):
            instance = loaded()
            if isinstance(instance, RuntimePlugin):
                return instance
        if callable(loaded):
            try:
                instance = loaded()
            except TypeError:
                return FunctionRuntimePlugin(cast(Callable[[RuntimePluginHost], None], loaded))
            if isinstance(instance, RuntimePlugin):
                return instance
        raise TypeError(f"Entry point '{source}' does not expose a supported plugin object")

    @staticmethod
    def _find_manifest(path: Path) -> Path | None:
        for name in ("easy-agent-plugin.yaml", "easy-agent-plugin.yml", "plugin.yaml", "plugin.yml"):
            candidate = path / name
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _describe_source(source: str | Path | RuntimePlugin) -> str:
        if isinstance(source, Path):
            return str(source)
        if isinstance(source, str):
            return source
        return source.__class__.__name__


