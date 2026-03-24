param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Args
)
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot = (Resolve-Path (Join-Path $ScriptDir '..\..')).Path
$ExePath = Join-Path $RepoRoot '.venv\Scripts\easy-agent.exe'
if (Test-Path $ExePath) {
    & $ExePath @Args
    exit $LASTEXITCODE
}
uv run --directory $RepoRoot easy-agent @Args
exit $LASTEXITCODE

