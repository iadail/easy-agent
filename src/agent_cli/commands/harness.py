from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.table import Table

from agent_cli.shared import build_cli_inline_resolver, with_runtime
from agent_common.models import HumanLoopMode
from agent_runtime import EasyAgentRuntime

console = Console()
harness_app = typer.Typer(help='Inspect and run long-running harnesses.')


def _approval_mode(value: str) -> HumanLoopMode:
    return HumanLoopMode(value)


@harness_app.command('list')
def list_harnesses(config: str = typer.Option('easy-agent.yml', '-c', '--config')) -> None:
    async def _run(runtime: EasyAgentRuntime) -> None:
        table = Table(title='harnesses')
        table.add_column('Name', style='cyan')
        table.add_column('Initializer', style='green')
        table.add_column('Worker', style='yellow')
        table.add_column('Evaluator', style='magenta')
        table.add_column('Max Cycles', style='white')
        for harness in runtime.list_harnesses():
            table.add_row(
                harness.name,
                harness.initializer_agent,
                harness.worker_target,
                harness.evaluator_agent,
                str(harness.max_cycles),
            )
        console.print(table)

    asyncio.run(with_runtime(config, _run))


@harness_app.command('run')
def run_harness(
    name: str = typer.Argument(..., help='Configured harness name.'),
    input_text: str = typer.Argument(..., help='User goal for the harness run.'),
    config: str = typer.Option('easy-agent.yml', '-c', '--config'),
    session_id: str | None = typer.Option(None, '--session-id', help='Optional explicit session id for resumable harness state.'),
    stream: str | None = typer.Option(None, '--stream', help='Optional stream format: pretty or ndjson.'),
    approval_mode: str = typer.Option('hybrid', '--approval-mode', help='Approval mode: deferred, inline, or hybrid.'),
) -> None:
    async def _run(runtime: EasyAgentRuntime) -> None:
        resolved_mode = _approval_mode(approval_mode)
        if resolved_mode is not HumanLoopMode.DEFERRED:
            runtime.set_inline_approval_resolver(build_cli_inline_resolver(console))
        if stream:
            async for event in runtime.stream_harness(name, input_text, session_id=session_id, approval_mode=resolved_mode):
                if stream == 'ndjson':
                    console.print(json.dumps(event, ensure_ascii=False))
                else:
                    console.print(
                        f"[{event['sequence']:03d}] {event['scope']}::{event['kind']} run={event['run_id']} "
                        f"payload={json.dumps(event.get('payload', {}), ensure_ascii=False)}"
                    )
            return
        result = await runtime.run_harness(name, input_text, session_id=session_id, approval_mode=resolved_mode)
        console.print_json(json.dumps(result, ensure_ascii=False))

    asyncio.run(with_runtime(config, _run))


@harness_app.command('resume')
def resume_harness(
    run_id: str = typer.Argument(..., help='Existing harness run id to resume from the latest checkpoint.'),
    config: str = typer.Option('easy-agent.yml', '-c', '--config'),
    stream: str | None = typer.Option(None, '--stream', help='Optional stream format: pretty or ndjson.'),
    approval_mode: str = typer.Option('hybrid', '--approval-mode', help='Approval mode: deferred, inline, or hybrid.'),
) -> None:
    async def _run(runtime: EasyAgentRuntime) -> None:
        resolved_mode = _approval_mode(approval_mode)
        if resolved_mode is not HumanLoopMode.DEFERRED:
            runtime.set_inline_approval_resolver(build_cli_inline_resolver(console))
        if stream:
            async for event in runtime.resume_harness_stream(run_id, approval_mode=resolved_mode):
                if stream == 'ndjson':
                    console.print(json.dumps(event, ensure_ascii=False))
                else:
                    console.print(
                        f"[{event['sequence']:03d}] {event['scope']}::{event['kind']} run={event['run_id']} "
                        f"payload={json.dumps(event.get('payload', {}), ensure_ascii=False)}"
                    )
            return
        result = await runtime.resume_harness(run_id, approval_mode=resolved_mode)
        console.print_json(json.dumps(result, ensure_ascii=False))

    asyncio.run(with_runtime(config, _run))
