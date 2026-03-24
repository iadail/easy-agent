from __future__ import annotations

import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from agent_runtime.longrun import run_longrun_suite

console = Console()
integration_app = typer.Typer(help='Run real integration and long-run validations.')


@integration_app.command('longrun')
def run_longrun(
    config: str = typer.Option('configs/longrun.example.yml', '-c', '--config'),
    cycles: int = typer.Option(3, '--cycles', min=1),
    output_root: str = typer.Option('.easy-agent/longrun', '--output-root'),
) -> None:
    report = run_longrun_suite(config, cycles=cycles, output_root=output_root)
    table = Table(title='easy-agent longrun')
    table.add_column('Mode', style='cyan')
    table.add_column('Runs', style='green')
    table.add_column('Successes', style='green')
    table.add_column('Failures', style='red')
    table.add_column('Avg Seconds', style='yellow')
    for mode, summary in report['summary'].items():
        table.add_row(
            mode,
            str(summary['runs']),
            str(summary['successes']),
            str(summary['failures']),
            str(summary['average_duration_seconds']),
        )
    console.print(table)
    output_path = Path(output_root) / 'longrun-report.json'
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    console.print(f'Report written to: {output_path}')
    failures = [record for record in report['records'] if not record['success']]
    if failures:
        raise typer.Exit(code=1)

