from pathlib import Path

import pytest

from easy_agent.models import RunContext
from easy_agent.sandbox import SandboxManager, SandboxMode, SandboxTarget
from easy_agent.skills import SkillLoader
from easy_agent.tools import ToolRegistry


@pytest.mark.asyncio
async def test_skill_loader_registers_python_and_command_skills() -> None:
    registry = ToolRegistry()
    sandbox_manager = SandboxManager(
        mode=SandboxMode.PROCESS,
        targets=[SandboxTarget.COMMAND_SKILL],
        env_allowlist=["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"],
    )
    loader = SkillLoader([Path("examples/skills")], [["cmd", "/c", "echo"]], sandbox_manager)

    skills = loader.register(registry)
    result = await registry.call(
        "python_echo",
        {"prompt": "hello"},
        RunContext(run_id="run_1", workdir=Path.cwd(), node_id="node_1"),
    )

    assert {skill.name for skill in skills} == {"python_echo", "command_echo"}
    assert result["echo"] == "hello"


@pytest.mark.asyncio
async def test_command_skill_requires_whitelist() -> None:
    registry = ToolRegistry()
    sandbox_manager = SandboxManager(
        mode=SandboxMode.PROCESS,
        targets=[SandboxTarget.COMMAND_SKILL],
        env_allowlist=["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"],
    )
    loader = SkillLoader([Path("examples/skills")], [], sandbox_manager)
    loader.register(registry)

    with pytest.raises(PermissionError):
        await registry.call(
            "command_echo",
            {"prompt": "blocked"},
            RunContext(run_id="run_1", workdir=Path.cwd(), node_id="node_1"),
        )
