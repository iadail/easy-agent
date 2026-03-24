from pathlib import Path
from textwrap import dedent

import pytest

from easy_agent.config import AppConfig, ModelConfig
from easy_agent.models import ToolSpec
from easy_agent.plugins import FunctionRuntimePlugin, RuntimePluginHost
from easy_agent.runtime import build_runtime_from_config


class FakeEntryPoint:
    def __init__(self, name: str, plugin: object) -> None:
        self.name = name
        self._plugin = plugin

    def load(self) -> object:
        return self._plugin


class EntryPointsList(list[FakeEntryPoint]):
    def select(self, *, group: str, name: str) -> list[FakeEntryPoint]:
        if group != "easy_agent.plugins":
            return []
        return [item for item in self if item.name == name]


def build_config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "model": ModelConfig().model_dump(),
            "graph": {
                "entrypoint": "coordinator",
                "agents": [{"name": "coordinator", "tools": [], "sub_agents": []}],
                "nodes": [],
            },
            "skills": [],
            "mcp": [],
            "storage": {"path": str(tmp_path / ".easy-agent"), "database": "state.db"},
            "security": {"allowed_commands": [["cmd", "/c", "echo"]]},
        }
    )


def test_runtime_loads_local_manifest(tmp_path: Path) -> None:
    skill_root = tmp_path / "skills"
    skill_dir = skill_root / "custom_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "skill.yaml").write_text(
        dedent(
            """
            name: custom_skill
            description: Custom skill from manifest
            entry_type: python
            hook: hook.py:run
            input_schema:
              type: object
            """
        ).strip(),
        encoding="utf-8",
    )
    (skill_dir / "hook.py").write_text(
        dedent(
            """
            def run(arguments, context):
                return {"value": arguments.get("prompt", "")}
            """
        ).strip(),
        encoding="utf-8",
    )
    manifest_path = tmp_path / "easy-agent-plugin.yaml"
    manifest_path.write_text(
        dedent(
            f"""
            skills:
              - {skill_root.name}
            mcp:
              - name: mounted
                transport: http_sse
                rpc_url: http://127.0.0.1:9000/rpc
                sse_url: http://127.0.0.1:9000/sse
            """
        ).strip(),
        encoding="utf-8",
    )

    runtime = build_runtime_from_config(build_config(tmp_path))
    runtime.load(manifest_path)

    assert any(skill.name == "custom_skill" for skill in runtime.skills)
    assert "mounted" in runtime.mcp_manager._clients


def test_runtime_loads_entry_point_plugin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = build_runtime_from_config(build_config(tmp_path))

    def register(host: RuntimePluginHost) -> None:
        host.register_tool(
            spec=ToolSpec(
                name="entry_point_tool",
                description="loaded from entry point",
                input_schema={"type": "object"},
            ),
            handler=lambda arguments, context: {"arguments": arguments, "node": context.node_id},
        )

    monkeypatch.setattr(
        "easy_agent.plugins.importlib_metadata.entry_points",
        lambda: EntryPointsList([FakeEntryPoint("demo_plugin", FunctionRuntimePlugin(register))]),
    )

    runtime.load("demo_plugin")

    assert runtime.registry.has("entry_point_tool")


