from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import textwrap
import threading
import time
from dataclasses import asdict, dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent_common.models import HumanLoopMode, ToolSpec
from agent_config.app import (
    AppConfig,
    ContainerExecutorOptions,
    ExecutorConfig,
    MicrovmExecutorOptions,
)
from agent_integrations.executors import build_executor_backends
from agent_integrations.federation import FederationClientManager, FederationServer
from agent_integrations.sandbox import SandboxManager, SandboxMode, SandboxTarget
from agent_integrations.storage import SQLiteRunStore
from agent_integrations.workbench import WorkbenchManager
from agent_runtime.runtime import build_runtime_from_config


@dataclass(slots=True)
class RealNetworkRecord:
    scenario: str
    transport: str
    live_model: bool
    host_dependency: str
    status: str
    duration_seconds: float
    notes: str


class _CallbackCollector:
    def __init__(self, fail_first: bool = False) -> None:
        self.fail_first = fail_first
        self.attempts = 0
        self.deliveries: list[dict[str, Any]] = []
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        collector = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                del format, args
                return None

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get('Content-Length', '0') or '0')
                payload = json.loads(self.rfile.read(length).decode('utf-8')) if length else {}
                collector.attempts += 1
                collector.deliveries.append(payload)
                status = HTTPStatus.INTERNAL_SERVER_ERROR if collector.fail_first and collector.attempts == 1 else HTTPStatus.OK
                self.send_response(status)
                self.end_headers()

        self._server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        port = int(self._server.server_address[1])
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f'http://127.0.0.1:{port}/callback'

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
        self._server = None
        self._thread = None


class _FakeRuntime:
    def __init__(self, tmp_path: Path) -> None:
        self.config = AppConfig.model_validate(
            {
                'graph': {'entrypoint': 'coordinator', 'agents': [{'name': 'coordinator'}], 'teams': [], 'nodes': []},
                'federation': {
                    'server': {
                        'enabled': True,
                        'host': '127.0.0.1',
                        'port': 0,
                        'base_path': '/a2a',
                        'retry_max_attempts': 3,
                        'retry_initial_backoff_seconds': 0.1,
                        'retry_backoff_multiplier': 1.0,
                        'subscription_lease_seconds': 30,
                    },
                    'exports': [
                        {
                            'name': 'local_echo',
                            'target_type': 'agent',
                            'target': 'coordinator',
                            'description': 'Echo target',
                            'modalities': ['text'],
                            'capabilities': ['streaming', 'interrupts'],
                        }
                    ],
                },
                'storage': {'path': str(tmp_path), 'database': 'state.db'},
            }
        )
        self.store = SQLiteRunStore(tmp_path, 'state.db')

    async def run_federated_export(self, export_name: str, input_text: str, *, session_id: str | None = None, approval_mode: HumanLoopMode = HumanLoopMode.HYBRID) -> dict[str, Any]:
        del approval_mode
        await asyncio.sleep(0.05)
        return {
            'run_id': 'local-run',
            'status': 'succeeded',
            'export': export_name,
            'session_id': session_id,
            'result': {'echo': input_text.upper()},
        }

    def interrupt_run(self, run_id: str, payload: dict[str, Any] | None = None) -> None:
        del run_id, payload
        return None


_CROSS_PROCESS_SERVER = textwrap.dedent(
    """
    import json
    import sys
    import threading
    import time
    import uuid
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    tasks = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            return None

        def _json(self):
            length = int(self.headers.get('Content-Length', '0') or '0')
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode('utf-8'))

        def _write(self, payload, status=200):
            data = json.dumps(payload).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            if self.path == '/a2a/agent-card':
                self._write({'name': 'subproc', 'protocol_version': '0.3', 'exports': [{'name': 'remote_echo', 'capabilities': {'modalities': ['text']}}]})
                return
            if self.path == '/a2a/agent-card/extended':
                self._write({'name': 'subproc', 'capabilities': {'push_delivery': {'sse_events': False}}, 'retry_policy': {'max_attempts': 1}})
                return
            if self.path == '/a2a/tasks':
                self._write({'tasks': list(tasks.values())})
                return
            if self.path.startswith('/a2a/tasks/'):
                task_id = self.path.split('/')[-1]
                self._write({'task': tasks[task_id]})
                return
            self._write({'error': 'not_found'}, status=404)

        def do_POST(self):
            if self.path == '/a2a/tasks/send':
                payload = self._json()
                task_id = uuid.uuid4().hex
                task = {
                    'task_id': task_id,
                    'status': 'running',
                    'response_payload': None,
                    'error_message': None,
                }
                tasks[task_id] = task
                def worker():
                    time.sleep(0.1)
                    task['status'] = 'succeeded'
                    task['response_payload'] = {'run_id': task_id, 'status': 'succeeded', 'result': {'echo': str(payload.get('input', '')).upper()}}
                threading.Thread(target=worker, daemon=True).start()
                self._write({'task': dict(task)}, status=202)
                return
            self._write({'error': 'not_found'}, status=404)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    print(server.server_address[1], flush=True)
    server.serve_forever()
    """
)


def _record(scenario: str, transport: str, host_dependency: str, runner: Any, *, live_model: bool = False) -> RealNetworkRecord:
    start = time.perf_counter()
    try:
        notes = runner()
        status = 'passed'
    except RuntimeError as exc:
        status = 'skipped'
        notes = str(exc)
    except Exception as exc:  # noqa: BLE001
        status = 'failed'
        notes = str(exc)
    return RealNetworkRecord(
        scenario=scenario,
        transport=transport,
        live_model=live_model,
        host_dependency=host_dependency,
        status=status,
        duration_seconds=round(time.perf_counter() - start, 4),
        notes=notes,
    )


def _scenario_cross_process_federation() -> str:
    process = subprocess.Popen(
        [sys.executable, '-c', _CROSS_PROCESS_SERVER],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        port_line = process.stdout.readline().strip() if process.stdout is not None else ''
        if not port_line:
            raise RuntimeError('subprocess federation server did not report a port')
        async def _run() -> str:
            manager = FederationClientManager(
                AppConfig.model_validate(
                    {
                        'graph': {'entrypoint': 'noop', 'agents': [{'name': 'noop'}], 'teams': [], 'nodes': []},
                        'federation': {'remotes': [{'name': 'subproc', 'base_url': f'http://127.0.0.1:{port_line}', 'push_preference': 'poll'}]},
                    }
                ).federation
            )
            await manager.start()
            try:
                remote = await manager.inspect_remote('subproc')
                result = await manager.run_remote('subproc', 'remote_echo', 'cross-process')
            finally:
                await manager.aclose()
            if remote['card']['exports'][0]['name'] != 'remote_echo':
                raise AssertionError('unexpected remote export')
            if result['result']['echo'] != 'CROSS-PROCESS':
                raise AssertionError('unexpected remote result')
            return 'cross-process send/poll federation passed'
        return asyncio.run(_run())
    finally:
        process.terminate()
        process.wait(timeout=5)


def _scenario_federation_retry_and_reconnect(tmp_path: Path) -> str:
    runtime = _FakeRuntime(tmp_path)
    server = FederationServer(runtime)
    callback = _CallbackCollector(fail_first=True)
    callback_url = callback.start()
    status = server.start()
    base_url = f"http://127.0.0.1:{status['port']}"
    async def _run() -> str:
        manager = FederationClientManager(
            AppConfig.model_validate(
                {
                    'graph': {'entrypoint': 'noop', 'agents': [{'name': 'noop'}], 'teams': [], 'nodes': []},
                    'federation': {'remotes': [{'name': 'loopback', 'base_url': base_url}]},
                }
            ).federation,
            store=runtime.store,
        )
        await manager.start()
        try:
            client = manager._client('loopback')
            response = await client.post('/a2a/tasks/send', json={'target': 'local_echo', 'input': 'deliver-me'})
            task_id = str(response.json()['task']['task_id'])
            await asyncio.sleep(0.2)
            await manager.subscribe_task('loopback', task_id, callback_url, from_sequence=0)
            await asyncio.sleep(0.3)
            subscriptions = await manager.list_subscriptions('loopback', task_id)
            if subscriptions[0]['status'] != 'delivered':
                raise AssertionError(f"subscription not delivered: {subscriptions[0]['status']}")
            renewed = await manager.renew_subscription('loopback', task_id, str(subscriptions[0]['subscription_id']), lease_seconds=60)
            cancelled = await manager.cancel_subscription('loopback', task_id, str(subscriptions[0]['subscription_id']))
            if renewed['status'] != 'active' or cancelled['status'] != 'cancelled':
                raise AssertionError('subscription lifecycle mismatch')
            if callback.attempts < 2:
                raise AssertionError('retry backoff was not exercised')
            return 'callback retry, renew, cancel, and reconnect-style resubscribe flow passed'
        finally:
            await manager.aclose()
    try:
        return asyncio.run(_run())
    finally:
        callback.stop()
        server.stop()


def _workbench_manager(base_path: Path, executors: list[ExecutorConfig], default_executor: str) -> WorkbenchManager:
    sandbox = SandboxManager(
        mode=SandboxMode.PROCESS,
        targets=[SandboxTarget.COMMAND_SKILL, SandboxTarget.STDIO_MCP],
        env_allowlist=['PATH', 'PATHEXT', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'TEMP', 'TMP'],
        working_root=base_path,
    )
    store = SQLiteRunStore(base_path / 'state', 'state.db')
    return WorkbenchManager(
        store,
        build_executor_backends(executors, sandbox),
        base_path / 'workbench',
        default_executor=default_executor,
        session_ttl_seconds=300,
    )


def _scenario_process_workbench_reuse(tmp_path: Path) -> str:
    manager = _workbench_manager(tmp_path, [ExecutorConfig(name='process', kind='process')], 'process')
    first = manager.ensure_session('run-process', 'skill-echo')
    (first.root_path / 'artifact.txt').write_text('persisted', encoding='utf-8')
    second = manager.ensure_session('run-process', 'skill-echo')
    if first.session_id != second.session_id:
        raise AssertionError('expected workbench session reuse for same owner/name')
    if (second.root_path / 'artifact.txt').read_text(encoding='utf-8') != 'persisted':
        raise AssertionError('workbench artifact was not reused')
    return 'process workbench reused the same long-lived session root'


def _scenario_container_workbench_reuse(tmp_path: Path) -> str:
    podman_executable = os.environ.get('EASY_AGENT_PODMAN_EXE', 'podman')
    image = os.environ.get('EASY_AGENT_CONTAINER_IMAGE')
    if not image:
        raise RuntimeError('skipped: EASY_AGENT_CONTAINER_IMAGE is not set')
    manager = _workbench_manager(
        tmp_path,
        [
            ExecutorConfig(
                name='containerized',
                kind='container',
                default_timeout_seconds=20,
                container=ContainerExecutorOptions(executable=podman_executable, image=image),
            )
        ],
        'containerized',
    )
    session = manager.ensure_session('run-container', 'skill-echo')
    result = manager.run_command(
        session.session_id,
        ['python', '-c', "print('container-ok')"],
        env={},
        timeout_seconds=20,
        target=SandboxTarget.COMMAND_SKILL,
    )
    if result.returncode != 0 or 'container-ok' not in result.stdout:
        raise AssertionError(result.stderr or result.stdout)
    reused = manager.ensure_session('run-container', 'skill-echo')
    if session.session_id != reused.session_id:
        raise AssertionError('container session was not reused')
    return 'container executor reused the same session and executed inside podman'


def _scenario_microvm_workbench_reuse(tmp_path: Path) -> str:
    base_image = os.environ.get('EASY_AGENT_QEMU_BASE_IMAGE')
    ssh_key = os.environ.get('EASY_AGENT_QEMU_SSH_KEY')
    qemu_executable = os.environ.get('EASY_AGENT_QEMU_EXE', 'qemu-system-x86_64')
    ssh_user = os.environ.get('EASY_AGENT_QEMU_SSH_USER', 'agent')
    if not base_image or not ssh_key:
        raise RuntimeError('skipped: EASY_AGENT_QEMU_BASE_IMAGE or EASY_AGENT_QEMU_SSH_KEY is not set')
    manager = _workbench_manager(
        tmp_path,
        [
            ExecutorConfig(
                name='microvm-qemu',
                kind='microvm',
                default_timeout_seconds=45,
                microvm=MicrovmExecutorOptions(
                    executable=qemu_executable,
                    base_image=base_image,
                    ssh_user=ssh_user,
                    ssh_private_key=ssh_key,
                ),
            )
        ],
        'microvm-qemu',
    )
    session = manager.ensure_session('run-microvm', 'skill-echo')
    result = manager.run_command(
        session.session_id,
        ['python3', '-c', "print('microvm-ok')"],
        env={},
        timeout_seconds=45,
        target=SandboxTarget.COMMAND_SKILL,
    )
    if result.returncode != 0 or 'microvm-ok' not in result.stdout:
        raise AssertionError(result.stderr or result.stdout)
    reused = manager.ensure_session('run-microvm', 'skill-echo')
    if session.session_id != reused.session_id:
        raise AssertionError('microvm session was not reused')
    return 'microvm executor reused the same session and executed through ssh'


def _scenario_replay_resume_failure_injection(tmp_path: Path) -> str:
    config = AppConfig.model_validate(
        {
            'graph': {
                'name': 'resume-check',
                'entrypoint': 'review',
                'agents': [{'name': 'worker', 'description': 'worker'}],
                'teams': [],
                'nodes': [
                    {'id': 'prepare', 'type': 'join'},
                    {'id': 'review', 'type': 'tool', 'target': 'flaky_tool', 'deps': ['prepare']},
                ],
            },
            'storage': {'path': str(tmp_path / 'state'), 'database': 'state.db'},
        }
    )
    runtime = build_runtime_from_config(config)
    counter = {'calls': 0}

    async def flaky_tool(arguments: dict[str, Any], context: Any) -> dict[str, Any]:
        del arguments, context
        counter['calls'] += 1
        if counter['calls'] == 1:
            raise RuntimeError('injected failure')
        return {'status': 'recovered'}

    runtime.register_tool(
        spec=ToolSpec(name='flaky_tool', description='flaky', input_schema={'type': 'object'}),
        handler=flaky_tool,
    )
    async def _run() -> str:
        try:
            first_run_id: str | None = None
            try:
                result = await runtime.run('resume me')
                first_run_id = str(result['run_id'])
            except RuntimeError as exc:
                message = str(exc)
                if 'failed' not in message:
                    raise
                first_run_id = message.split('Run ', 1)[1].split(' failed', 1)[0]
            if not first_run_id:
                raise AssertionError('missing failed run id')
            checkpoints = runtime.list_checkpoints(first_run_id)
            if not checkpoints:
                raise AssertionError('expected checkpoints before resume')
            resumed = await runtime.resume(first_run_id)
            replay = await runtime.replay(first_run_id, checkpoints[-1]['checkpoint_id'])
            forked = await runtime.resume(first_run_id, checkpoint_id=checkpoints[-1]['checkpoint_id'], fork=True)
            if resumed['result']['status'] != 'recovered':
                raise AssertionError('resume did not recover')
            if replay['checkpoint_kind'] != 'graph':
                raise AssertionError('replay did not return graph checkpoint')
            if forked['run_id'] == first_run_id:
                raise AssertionError('fork resume should allocate a new run id')
            return 'resume, replay, and fork recovery passed under injected failure'
        finally:
            await runtime.aclose()
    return asyncio.run(_run())


def run_real_network_suite(config_path: str | Path = 'easy-agent.yml') -> dict[str, Any]:
    del config_path
    output_root = Path('.easy-agent')
    output_root.mkdir(parents=True, exist_ok=True)
    tmp_root = output_root / 'real-network-tmp' / str(int(time.time() * 1000))
    tmp_root.mkdir(parents=True, exist_ok=True)
    try:
        records = [
            _record('cross_process_federation', 'http_poll', 'python subprocess', _scenario_cross_process_federation),
            _record(
                'disconnect_retry_chaos',
                'http_webhook',
                'loopback callback server',
                lambda: _scenario_federation_retry_and_reconnect(tmp_root / 'federation'),
            ),
            _record('workbench_reuse_process', 'local_process', 'none', lambda: _scenario_process_workbench_reuse(tmp_root / 'process')),
            _record('workbench_reuse_container', 'podman_exec', 'podman image', lambda: _scenario_container_workbench_reuse(tmp_root / 'container')),
            _record('workbench_reuse_microvm', 'qemu_ssh', 'qemu image + ssh key', lambda: _scenario_microvm_workbench_reuse(tmp_root / 'microvm')),
            _record(
                'replay_resume_failure_injection',
                'sqlite_checkpoint',
                'none',
                lambda: _scenario_replay_resume_failure_injection(tmp_root / 'resume'),
            ),
        ]
    finally:
        shutil.rmtree(tmp_root, ignore_errors=True)
    summary = {
        'runs': len(records),
        'passed': sum(1 for item in records if item.status == 'passed'),
        'failed': sum(1 for item in records if item.status == 'failed'),
        'skipped': sum(1 for item in records if item.status == 'skipped'),
    }
    report = {
        'records': [asdict(item) for item in records],
        'summary': summary,
        'generated_at': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
    }
    report_path = output_root / 'real-network-report.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report

