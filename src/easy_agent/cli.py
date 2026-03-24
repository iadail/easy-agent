from __future__ import annotations

import asyncio
import json
import platform
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.table import Table

from easy_agent.models import RunContext
from easy_agent.protocols import resolve_protocol
from easy_agent.runtime import EasyAgentRuntime, build_runtime

app = typer.Typer(help="Colorful CLI for the easy-agent foundation.")
skills_app = typer.Typer()
mcp_app = typer.Typer()
plugins_app = typer.Typer()
app.add_typer(skills_app, name="skills")
app.add_typer(mcp_app, name="mcp")
app.add_typer(plugins_app, name="plugins")
console = Console()


async def _with_runtime(
    config_path: str,
    callback: Callable[[EasyAgentRuntime], Awaitable[Any]],
) -> Any:
    runtime = build_runtime(config_path)
    try:
        await runtime.start()
        return await callback(runtime)
    finally:
        await runtime.aclose()


@app.command()
def doctor(
    config: str = typer.Option("easy-agent.yml", "-c", "--config"),
    smoke: bool = False,
) -> None:
    async def _run(runtime: EasyAgentRuntime) -> None:
        adapter = resolve_protocol(runtime.config.model)
        sandbox = runtime.sandbox_manager.describe()
        table = Table(title="easy-agent doctor")
        table.add_column("Check", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Python", sys.version.split()[0])
        table.add_row("Platform", platform.platform())
        table.add_row("Protocol", adapter.protocol.value)
        table.add_row("Skills", str(len(runtime.skills)))
        table.add_row("MCP Servers", str(len(runtime.mcp_manager._clients)))
        table.add_row("Loaded Sources", str(len(runtime.loaded_sources)))
        table.add_row("Sandbox Mode", sandbox["mode"])
        table.add_row("Sandbox Targets", ", ".join(sandbox["targets"]))
        table.add_row("Windows Sandbox", str(sandbox["windows_sandbox_available"]))
        table.add_row("Storage", str(runtime.store.base_path.resolve()))
        console.print(table)
        if smoke:
            if runtime.config.graph.entrypoint not in runtime.config.agent_map:
                raise typer.BadParameter("Smoke test requires graph.entrypoint to be an agent.")
            context = RunContext(
                run_id="doctor_smoke",
                workdir=Path.cwd(),
                node_id=None,
                shared_state={"input": "smoke"},
            )
            result = await runtime.orchestrator.run_agent(
                runtime.config.graph.entrypoint,
                "Respond with a short confirmation.",
                context,
            )
            console.print(f"[bold green]Smoke response:[/bold green] {result}")

    asyncio.run(_with_runtime(config, _run))


@app.command()
def run(
    input_text: str = typer.Argument(..., help="Input text for the graph or entry agent."),
    config: str = typer.Option("easy-agent.yml", "-c", "--config"),
) -> None:
    async def _run(runtime: EasyAgentRuntime) -> None:
        result = await runtime.run(input_text)
        console.print_json(json.dumps(result, ensure_ascii=False))

    asyncio.run(_with_runtime(config, _run))


@app.command()
def trace(run_id: str, config: str = typer.Option("easy-agent.yml", "-c", "--config")) -> None:
    runtime = build_runtime(config)
    try:
        payload = runtime.store.load_trace(run_id)
        console.print_json(json.dumps(payload, ensure_ascii=False))
    finally:
        asyncio.run(runtime.aclose())


@skills_app.command("list")
def list_skills(config: str = typer.Option("easy-agent.yml", "-c", "--config")) -> None:
    runtime = build_runtime(config)
    try:
        table = Table(title="skills")
        table.add_column("Name", style="cyan")
        table.add_column("Description", style="green")
        for skill in runtime.skills:
            table.add_row(skill.name, skill.description)
        console.print(table)
    finally:
        asyncio.run(runtime.aclose())


@mcp_app.command("list")
def list_mcp(config: str = typer.Option("easy-agent.yml", "-c", "--config")) -> None:
    async def _run(runtime: EasyAgentRuntime) -> None:
        table = Table(title="mcp tools")
        table.add_column("Server", style="cyan")
        table.add_column("Tool", style="green")
        servers = await runtime.mcp_manager.list_servers()
        for server_name, tools in servers.items():
            for tool in tools:
                table.add_row(server_name, tool.name)
        console.print(table)

    asyncio.run(_with_runtime(config, _run))


@plugins_app.command("list")
def list_plugins(config: str = typer.Option("easy-agent.yml", "-c", "--config")) -> None:
    runtime = build_runtime(config)
    try:
        table = Table(title="plugins")
        table.add_column("Source", style="cyan")
        for source in runtime.loaded_sources:
            table.add_row(source)
        console.print(table)
    finally:
        asyncio.run(runtime.aclose())
