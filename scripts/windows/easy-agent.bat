@echo off
setlocal
set SCRIPT_DIR=%~dp0
for %%I in ("%SCRIPT_DIR%..\..") do set REPO_ROOT=%%~fI
if exist "%REPO_ROOT%\.venv\Scripts\easy-agent.exe" (
  "%REPO_ROOT%\.venv\Scripts\easy-agent.exe" %*
  exit /b %ERRORLEVEL%
)
uv run --directory "%REPO_ROOT%" easy-agent %*

