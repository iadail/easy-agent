from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from agent_config.app import ExecutorConfig
from agent_integrations.sandbox import (
    PreparedSubprocess,
    SandboxManager,
    SandboxRequest,
    SandboxResult,
    SandboxTarget,
)


@dataclass(slots=True)
class ExecutorSession:
    session_id: str
    root_path: Path
    executor_name: str
    runtime_state: dict[str, Any]


class ExecutorBackend(Protocol):
    name: str
    kind: str

    def describe(self) -> dict[str, Any]: ...

    def ensure_session(self, session: ExecutorSession) -> dict[str, Any]: ...

    def prepare_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> PreparedSubprocess: ...

    def run_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> SandboxResult: ...

    def sync_to_host(self, session: ExecutorSession) -> dict[str, Any]: ...

    def shutdown_session(self, session: ExecutorSession) -> dict[str, Any]: ...


def _command_exists(executable: str) -> bool:
    return Path(executable).exists() or shutil.which(executable) is not None


def _run_subprocess(
    command: list[str],
    *,
    cwd: Path | None = None,
    timeout_seconds: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout_seconds,
        shell=False,
    )


def _quote_remote_shell(token: str) -> str:
    return "'" + token.replace("'", "'\"'\"'") + "'"


class ProcessExecutorBackend:
    kind = 'process'

    def __init__(self, config: ExecutorConfig, sandbox_manager: SandboxManager) -> None:
        self.name = config.name
        self._sandbox_manager = sandbox_manager

    def describe(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'kind': self.kind,
            'available': True,
            'details': {'mode': self._sandbox_manager.mode.value},
        }

    def ensure_session(self, session: ExecutorSession) -> dict[str, Any]:
        del session
        return {}

    def prepare_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> PreparedSubprocess:
        return self._sandbox_manager.prepare(
            SandboxRequest(
                command=command,
                cwd=session.root_path,
                env=env,
                timeout_seconds=timeout_seconds,
                target=target,
            )
        )

    def run_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> SandboxResult:
        return self._sandbox_manager.run(
            SandboxRequest(
                command=command,
                cwd=session.root_path,
                env=env,
                timeout_seconds=timeout_seconds,
                target=target,
            )
        )

    def sync_to_host(self, session: ExecutorSession) -> dict[str, Any]:
        return dict(session.runtime_state)

    def shutdown_session(self, session: ExecutorSession) -> dict[str, Any]:
        return dict(session.runtime_state)


class ContainerExecutorBackend:
    kind = 'container'

    def __init__(self, config: ExecutorConfig, sandbox_manager: SandboxManager) -> None:
        if config.container is None:
            raise ValueError(f"executor '{config.name}' requires container config")
        self.name = config.name
        self._config = config
        self._sandbox_manager = sandbox_manager
        self._container = config.container

    def describe(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'kind': self.kind,
            'available': _command_exists(self._container.executable),
            'details': {
                'executable': self._container.executable,
                'image': self._container.image,
                'workdir': self._container.workdir,
            },
        }

    def ensure_session(self, session: ExecutorSession) -> dict[str, Any]:
        state = dict(session.runtime_state)
        container_name = str(state.get('container_name') or f'easy-agent-{self.name}-{session.session_id[:12]}')
        state['container_name'] = container_name
        if state.get('status') == 'running':
            return state
        if not _command_exists(self._container.executable):
            state.update({'status': 'unavailable', 'last_error': f"missing executable: {self._container.executable}"})
            return state
        session.root_path.mkdir(parents=True, exist_ok=True)
        command = [
            self._container.executable,
            'run',
            '--detach',
            '--rm',
            '--name',
            container_name,
            '--workdir',
            self._container.workdir,
            '--volume',
            f'{session.root_path.resolve()}:{self._container.workdir}',
            *self._container.run_args,
            self._container.image,
            *self._container.keepalive_command,
        ]
        result = _run_subprocess(command, timeout_seconds=self._config.default_timeout_seconds)
        if result.returncode != 0:
            state.update({'status': 'failed', 'last_error': result.stderr.strip() or result.stdout.strip()})
            return state
        state.update(
            {
                'status': 'running',
                'container_id': result.stdout.strip() or container_name,
                'started_at': time.time(),
            }
        )
        return state

    def prepare_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> PreparedSubprocess:
        state = self.ensure_session(session)
        if state.get('status') != 'running':
            raise RuntimeError(str(state.get('last_error') or 'container executor is unavailable'))
        wrapped = [
            self._container.executable,
            'exec',
            *self._container.exec_args,
            *self._env_args(env),
            str(state['container_name']),
            *command,
        ]
        return self._sandbox_manager.prepare(
            SandboxRequest(
                command=wrapped,
                cwd=session.root_path,
                env={},
                timeout_seconds=timeout_seconds,
                target=target,
            )
        )

    def run_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> SandboxResult:
        prepared = self.prepare_command(
            session,
            command,
            env=env,
            timeout_seconds=timeout_seconds,
            target=target,
        )
        return self._sandbox_manager.run(
            SandboxRequest(
                command=prepared.command,
                cwd=prepared.cwd,
                env=prepared.env,
                timeout_seconds=timeout_seconds,
                target=target,
            )
        )

    def sync_to_host(self, session: ExecutorSession) -> dict[str, Any]:
        return dict(session.runtime_state)

    def shutdown_session(self, session: ExecutorSession) -> dict[str, Any]:
        state = dict(session.runtime_state)
        container_name = state.get('container_name')
        if not container_name or not _command_exists(self._container.executable):
            return state
        _run_subprocess([self._container.executable, 'rm', '--force', str(container_name)], timeout_seconds=10.0)
        state['status'] = 'stopped'
        return state

    @staticmethod
    def _env_args(env: dict[str, str]) -> list[str]:
        arguments: list[str] = []
        for key, value in env.items():
            arguments.extend(['--env', f'{key}={value}'])
        return arguments


class MicrovmExecutorBackend:
    kind = 'microvm'

    def __init__(self, config: ExecutorConfig, sandbox_manager: SandboxManager) -> None:
        if config.microvm is None:
            raise ValueError(f"executor '{config.name}' requires microvm config")
        self.name = config.name
        self._config = config
        self._sandbox_manager = sandbox_manager
        self._microvm = config.microvm

    def describe(self) -> dict[str, Any]:
        return {
            'name': self.name,
            'kind': self.kind,
            'available': (
                _command_exists(self._microvm.executable)
                and _command_exists('ssh')
                and _command_exists('scp')
                and bool(self._microvm.base_image)
            ),
            'details': {
                'executable': self._microvm.executable,
                'base_image': self._microvm.base_image or '',
                'ssh_user': self._microvm.ssh_user,
                'ssh_port_base': self._microvm.ssh_port_base,
                'guest_workdir': self._microvm.guest_workdir,
            },
        }

    def ensure_session(self, session: ExecutorSession) -> dict[str, Any]:
        state = dict(session.runtime_state)
        if state.get('status') == 'running':
            return state
        if not _command_exists(self._microvm.executable):
            state.update({'status': 'unavailable', 'last_error': f"missing executable: {self._microvm.executable}"})
            return state
        if not self._microvm.base_image:
            state.update({'status': 'unavailable', 'last_error': 'microvm base_image is required'})
            return state
        if not _command_exists('ssh') or not _command_exists('scp'):
            state.update({'status': 'unavailable', 'last_error': 'ssh/scp executables are required'})
            return state
        overlay_path = session.root_path / 'guest-overlay.qcow2'
        port = int(state.get('ssh_port') or self._allocate_port())
        launch_args = self._build_launch_command(overlay_path, port)
        process = subprocess.Popen(
            launch_args,
            cwd=session.root_path,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
        )
        state.update(
            {
                'status': 'starting',
                'process_id': process.pid,
                'ssh_port': port,
                'overlay_path': str(overlay_path),
                'started_at': time.time(),
            }
        )
        self._wait_for_ssh(port)
        self._sync_to_guest(session.root_path, port)
        state['status'] = 'running'
        return state

    def prepare_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> PreparedSubprocess:
        state = self.ensure_session(session)
        if state.get('status') != 'running':
            raise RuntimeError(str(state.get('last_error') or 'microvm executor is unavailable'))
        self._sync_to_guest(session.root_path, int(state['ssh_port']))
        wrapped = self._build_ssh_command(int(state['ssh_port']), command, env)
        return self._sandbox_manager.prepare(
            SandboxRequest(
                command=wrapped,
                cwd=session.root_path,
                env={},
                timeout_seconds=timeout_seconds,
                target=target,
            )
        )

    def run_command(
        self,
        session: ExecutorSession,
        command: list[str],
        *,
        env: dict[str, str],
        timeout_seconds: float,
        target: SandboxTarget,
    ) -> SandboxResult:
        prepared = self.prepare_command(
            session,
            command,
            env=env,
            timeout_seconds=timeout_seconds,
            target=target,
        )
        result = self._sandbox_manager.run(
            SandboxRequest(
                command=prepared.command,
                cwd=prepared.cwd,
                env=prepared.env,
                timeout_seconds=timeout_seconds,
                target=target,
            )
        )
        state = dict(session.runtime_state)
        ssh_port = int(state.get('ssh_port') or 0)
        if ssh_port:
            self._sync_to_host(session.root_path, ssh_port)
        return result

    def sync_to_host(self, session: ExecutorSession) -> dict[str, Any]:
        state = dict(session.runtime_state)
        ssh_port = int(state.get('ssh_port') or 0)
        if state.get('status') == 'running' and ssh_port:
            self._sync_to_host(session.root_path, ssh_port)
        return state

    def shutdown_session(self, session: ExecutorSession) -> dict[str, Any]:
        state = dict(session.runtime_state)
        ssh_port = int(state.get('ssh_port') or 0)
        if state.get('status') == 'running' and ssh_port:
            self._sync_to_host(session.root_path, ssh_port)
        process_id = state.get('process_id')
        if process_id:
            try:
                os.kill(int(process_id), 15)
            except OSError:
                pass
        state['status'] = 'stopped'
        return state

    def _build_launch_command(self, overlay_path: Path, ssh_port: int) -> list[str]:
        overlay_path.parent.mkdir(parents=True, exist_ok=True)
        qemu_img = self._qemu_img_executable()
        if qemu_img and not overlay_path.exists():
            _run_subprocess(
                [
                    qemu_img,
                    'create',
                    '-f',
                    'qcow2',
                    '-F',
                    'qcow2',
                    '-b',
                    str(self._microvm.base_image),
                    str(overlay_path),
                ],
                timeout_seconds=15.0,
            )
        drive_path = overlay_path if overlay_path.exists() else Path(str(self._microvm.base_image))
        return [
            self._microvm.executable,
            '-machine',
            'microvm,accel=tcg',
            '-m',
            str(self._microvm.memory_mb),
            '-smp',
            str(self._microvm.cpus),
            '-display',
            'none',
            '-nodefaults',
            '-no-user-config',
            '-nic',
            f'user,model=virtio-net-pci,hostfwd=tcp:127.0.0.1:{ssh_port}-:22',
            '-drive',
            f'if=virtio,format=qcow2,file={drive_path}',
            *[item.format(ssh_port=ssh_port, overlay_path=str(overlay_path)) for item in self._microvm.extra_args],
        ]

    def _build_ssh_command(self, ssh_port: int, command: list[str], env: dict[str, str]) -> list[str]:
        env_prefix = ' '.join(f'{key}={_quote_remote_shell(value)}' for key, value in env.items())
        remote_command = ' '.join(_quote_remote_shell(token) for token in command)
        shell_command = f"cd {_quote_remote_shell(self._microvm.guest_workdir)} && {env_prefix + ' ' if env_prefix else ''}{remote_command}"
        return [
            'ssh',
            '-o',
            'BatchMode=yes',
            '-o',
            'StrictHostKeyChecking=no',
            '-o',
            'UserKnownHostsFile=NUL',
            '-p',
            str(ssh_port),
            *self._identity_args(),
            f'{self._microvm.ssh_user}@127.0.0.1',
            shell_command,
        ]

    def _sync_to_guest(self, root_path: Path, ssh_port: int) -> None:
        mkdir = self._build_ssh_command(ssh_port, ['mkdir', '-p', self._microvm.guest_workdir], {})
        _run_subprocess(mkdir, timeout_seconds=20.0)
        copy = [
            'scp',
            '-r',
            '-q',
            '-P',
            str(ssh_port),
            *self._identity_args(),
            str(root_path),
            f'{self._microvm.ssh_user}@127.0.0.1:{self._microvm.guest_workdir}/..',
        ]
        _run_subprocess(copy, timeout_seconds=30.0)

    def _sync_to_host(self, root_path: Path, ssh_port: int) -> None:
        root_path.mkdir(parents=True, exist_ok=True)
        copy = [
            'scp',
            '-r',
            '-q',
            '-P',
            str(ssh_port),
            *self._identity_args(),
            f'{self._microvm.ssh_user}@127.0.0.1:{self._microvm.guest_workdir}/.',
            str(root_path),
        ]
        _run_subprocess(copy, timeout_seconds=30.0)

    def _wait_for_ssh(self, ssh_port: int) -> None:
        deadline = time.monotonic() + self._config.default_timeout_seconds
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(('127.0.0.1', ssh_port), timeout=1):
                    return
            except OSError:
                time.sleep(0.5)
        raise TimeoutError(f'microvm ssh port {ssh_port} did not become ready')

    def _qemu_img_executable(self) -> str | None:
        executable_path = Path(self._microvm.executable)
        if executable_path.exists():
            candidate = executable_path.with_name('qemu-img.exe')
            if candidate.exists():
                return str(candidate)
        return shutil.which('qemu-img')

    def _allocate_port(self) -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(('127.0.0.1', 0))
            port = int(sock.getsockname()[1])
        return max(port, self._microvm.ssh_port_base)

    def _identity_args(self) -> list[str]:
        if not self._microvm.ssh_private_key:
            return []
        return ['-i', self._microvm.ssh_private_key]


def build_executor_backends(configs: list[ExecutorConfig], sandbox_manager: SandboxManager) -> dict[str, ExecutorBackend]:
    backends: dict[str, ExecutorBackend] = {}
    for config in configs:
        if config.kind == 'process':
            backends[config.name] = ProcessExecutorBackend(config, sandbox_manager)
        elif config.kind == 'container':
            backends[config.name] = ContainerExecutorBackend(config, sandbox_manager)
        elif config.kind == 'microvm':
            backends[config.name] = MicrovmExecutorBackend(config, sandbox_manager)
        else:
            raise ValueError(f'Unsupported executor kind: {config.kind}')
    return backends

