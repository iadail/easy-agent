from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import IO, Any


class SandboxMode(StrEnum):
    OFF = "off"
    AUTO = "auto"
    PROCESS = "process"
    WINDOWS_SANDBOX = "windows_sandbox"


class SandboxTarget(StrEnum):
    COMMAND_SKILL = "command_skill"
    STDIO_MCP = "stdio_mcp"


@dataclass(slots=True)
class SandboxRequest:
    command: list[str]
    cwd: Path
    env: dict[str, str]
    timeout_seconds: float
    target: SandboxTarget


@dataclass(slots=True)
class SandboxResult:
    stdout: str
    stderr: str
    returncode: int


class ProcessHandle:
    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process
        self.stdin: IO[bytes] | None = process.stdin
        self.stdout: IO[bytes] | None = process.stdout
        self.stderr: IO[bytes] | None = process.stderr

    def terminate(self) -> None:
        self._process.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self._process.wait(timeout=timeout)


class BaseSandboxRunner:
    def run(self, request: SandboxRequest) -> SandboxResult:
        raise NotImplementedError

    def start(self, request: SandboxRequest) -> ProcessHandle:
        raise NotImplementedError


class DirectSandboxRunner(BaseSandboxRunner):
    def run(self, request: SandboxRequest) -> SandboxResult:
        result = subprocess.run(
            request.command,
            cwd=request.cwd,
            env={**os.environ, **request.env},
            capture_output=True,
            text=True,
            check=False,
            timeout=request.timeout_seconds,
            shell=False,
        )
        return SandboxResult(
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            returncode=result.returncode,
        )

    def start(self, request: SandboxRequest) -> ProcessHandle:
        process = subprocess.Popen(
            request.command,
            cwd=request.cwd,
            env={**os.environ, **request.env},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        return ProcessHandle(process)


class ProcessSandboxRunner(BaseSandboxRunner):
    def __init__(self, env_allowlist: list[str], working_root: Path | None = None) -> None:
        self.env_allowlist = set(env_allowlist)
        self.working_root = working_root.resolve() if working_root is not None else None

    def run(self, request: SandboxRequest) -> SandboxResult:
        resolved_cwd = self._resolve_cwd(request.cwd)
        result = subprocess.run(
            request.command,
            cwd=resolved_cwd,
            env=self._filtered_env(request.env),
            capture_output=True,
            text=True,
            check=False,
            timeout=request.timeout_seconds,
            shell=False,
        )
        return SandboxResult(
            stdout=result.stdout.strip(),
            stderr=result.stderr.strip(),
            returncode=result.returncode,
        )

    def start(self, request: SandboxRequest) -> ProcessHandle:
        resolved_cwd = self._resolve_cwd(request.cwd)
        process = subprocess.Popen(
            request.command,
            cwd=resolved_cwd,
            env=self._filtered_env(request.env),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
        )
        return ProcessHandle(process)

    def _resolve_cwd(self, cwd: Path) -> Path:
        resolved = cwd.resolve()
        if self.working_root is not None:
            try:
                resolved.relative_to(self.working_root)
            except ValueError as exc:
                raise PermissionError(f"Sandbox cwd escapes working root: {resolved}") from exc
        return resolved

    def _filtered_env(self, extra_env: dict[str, str]) -> dict[str, str]:
        base_env = {key: value for key, value in os.environ.items() if key in self.env_allowlist}
        for key, value in extra_env.items():
            if key in self.env_allowlist:
                base_env[key] = value
        return base_env


class WindowsSandboxRunner(BaseSandboxRunner):
    def __init__(self, env_allowlist: list[str], working_root: Path | None = None) -> None:
        self.env_allowlist = env_allowlist
        self.working_root = working_root

    @staticmethod
    def is_available() -> bool:
        sandbox_exe = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
        return sandbox_exe.exists()

    def run(self, request: SandboxRequest) -> SandboxResult:
        if not self.is_available():
            raise RuntimeError("Windows Sandbox is not available on this host")
        if request.target is SandboxTarget.STDIO_MCP:
            raise RuntimeError("Windows Sandbox cannot host stdio MCP transport")

        job_id = uuid.uuid4().hex
        host_root = Path(tempfile.gettempdir()) / f"easy-agent-wsb-{job_id}"
        host_root.mkdir(parents=True, exist_ok=True)
        stdout_path = host_root / "stdout.txt"
        stderr_path = host_root / "stderr.txt"
        wrapper_path = host_root / "run.ps1"
        wrapper_path.write_text(
            "\n".join(
                [
                    "$ErrorActionPreference = 'Stop'",
                    f"$envMap = @{self._powershell_env(request.env)}",
                    "foreach ($item in $envMap.GetEnumerator()) {",
                    "  [System.Environment]::SetEnvironmentVariable($item.Key, $item.Value, 'Process')",
                    "}",
                    "$command = @(" + ", ".join(self._quoted_token(token) for token in request.command) + ")",
                    "$arguments = @()",
                    "if ($command.Length -gt 1) { $arguments = $command[1..($command.Length - 1)] }",
                    "& $command[0] @arguments 1> C:\\Users\\WDAGUtilityAccount\\Desktop\\stdout.txt 2> C:\\Users\\WDAGUtilityAccount\\Desktop\\stderr.txt",
                    "exit $LASTEXITCODE",
                ]
            ),
            encoding="utf-8",
        )
        sandbox_file = host_root / "session.wsb"
        sandbox_file.write_text(
            "\n".join(
                [
                    "<Configuration>",
                    "  <MappedFolders>",
                    "    <MappedFolder>",
                    f"      <HostFolder>{host_root}</HostFolder>",
                    "      <SandboxFolder>C:\\Users\\WDAGUtilityAccount\\Desktop</SandboxFolder>",
                    "      <ReadOnly>false</ReadOnly>",
                    "    </MappedFolder>",
                    "  </MappedFolders>",
                    "  <LogonCommand>",
                    "    <Command>powershell.exe -ExecutionPolicy Bypass -File C:\\Users\\WDAGUtilityAccount\\Desktop\\run.ps1</Command>",
                    "  </LogonCommand>",
                    "</Configuration>",
                ]
            ),
            encoding="utf-8",
        )
        sandbox_exe = Path(os.environ.get("WINDIR", r"C:\Windows")) / "System32" / "WindowsSandbox.exe"
        launch = subprocess.Popen([str(sandbox_exe), str(sandbox_file)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.monotonic() + request.timeout_seconds
        while time.monotonic() < deadline:
            if stdout_path.exists() or stderr_path.exists():
                stdout = stdout_path.read_text(encoding="utf-8") if stdout_path.exists() else ""
                stderr = stderr_path.read_text(encoding="utf-8") if stderr_path.exists() else ""
                launch.terminate()
                return SandboxResult(stdout=stdout.strip(), stderr=stderr.strip(), returncode=0)
            time.sleep(1)
        launch.terminate()
        raise TimeoutError("Windows Sandbox execution timed out")

    def start(self, request: SandboxRequest) -> ProcessHandle:
        del request
        raise RuntimeError("Windows Sandbox does not support streaming stdio processes")

    @staticmethod
    def _quoted_token(token: str) -> str:
        return "'" + token.replace("'", "''") + "'"

    def _powershell_env(self, env_map: dict[str, str]) -> str:
        entries = []
        for key, value in env_map.items():
            escaped_key = key.replace("'", "''")
            escaped_value = value.replace("'", "''")
            entries.append(f"'{escaped_key}' = '{escaped_value}'")
        return "; ".join(entries)


class SandboxManager:
    def __init__(
        self,
        mode: SandboxMode,
        targets: list[SandboxTarget],
        env_allowlist: list[str],
        working_root: Path | None = None,
        windows_sandbox_fallback: SandboxMode = SandboxMode.PROCESS,
    ) -> None:
        self.mode = mode
        self.targets = set(targets)
        self.direct_runner = DirectSandboxRunner()
        self.process_runner = ProcessSandboxRunner(env_allowlist, working_root)
        self.windows_runner = WindowsSandboxRunner(env_allowlist, working_root)
        self.windows_sandbox_fallback = windows_sandbox_fallback

    def run(self, request: SandboxRequest) -> SandboxResult:
        return self._resolve_runner(request.target).run(request)

    def start(self, request: SandboxRequest) -> ProcessHandle:
        return self._resolve_runner(request.target).start(request)

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode.value,
            "targets": sorted(target.value for target in self.targets),
            "windows_sandbox_available": self.windows_runner.is_available(),
            "windows_sandbox_fallback": self.windows_sandbox_fallback.value,
        }

    def _resolve_runner(self, target: SandboxTarget) -> BaseSandboxRunner:
        if self.mode is SandboxMode.OFF or target not in self.targets:
            return self.direct_runner
        if self.mode in (SandboxMode.AUTO, SandboxMode.PROCESS):
            return self.process_runner
        if self.mode is SandboxMode.WINDOWS_SANDBOX:
            if target is SandboxTarget.STDIO_MCP or not self.windows_runner.is_available():
                if self.windows_sandbox_fallback is SandboxMode.PROCESS:
                    return self.process_runner
                raise RuntimeError("Windows Sandbox requested but unavailable for this target")
            return self.windows_runner
        return self.process_runner

