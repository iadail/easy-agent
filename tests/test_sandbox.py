import sys
from pathlib import Path

from easy_agent.sandbox import (
    ProcessSandboxRunner,
    SandboxManager,
    SandboxMode,
    SandboxRequest,
    SandboxTarget,
)


def test_process_sandbox_filters_environment(tmp_path: Path) -> None:
    manager = SandboxManager(
        mode=SandboxMode.PROCESS,
        targets=[SandboxTarget.COMMAND_SKILL],
        env_allowlist=["SAFE_VAR"],
        working_root=tmp_path,
    )

    request = SandboxRequest(
        command=[
            sys.executable,
            "-c",
            "import os; print(os.environ.get('SAFE_VAR', 'missing')); print(os.environ.get('SECRET_VAR', 'missing'))",
        ],
        cwd=tmp_path,
        env={"SAFE_VAR": "visible", "SECRET_VAR": "hidden"},
        timeout_seconds=5,
        target=SandboxTarget.COMMAND_SKILL,
    )

    result = manager.run(request)

    assert result.returncode == 0
    assert result.stdout.splitlines() == ["visible", "missing"]


def test_windows_sandbox_stdio_target_falls_back_to_process() -> None:
    manager = SandboxManager(
        mode=SandboxMode.WINDOWS_SANDBOX,
        targets=[SandboxTarget.STDIO_MCP],
        env_allowlist=["PATH"],
    )

    runner = manager._resolve_runner(SandboxTarget.STDIO_MCP)

    assert isinstance(runner, ProcessSandboxRunner)
