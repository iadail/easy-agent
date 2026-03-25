# easy-agent

[English](./README.md) | [简体中文](./README.zh-CN.md)

`easy-agent` 是一个白板化、业务无关、可工程化扩展的 Python Agent 开发底座。它聚焦在运行时层，而不是某个具体业务域，因此可以把 teams、sub-agents、skills、MCP servers、plugins、session memory 以及后续协议演进挂载到同一个框架中，而不把仓库绑死在单一场景上。

## 技术栈

<table>
  <tr>
    <td valign="top" width="25%">
      <strong>运行时</strong><br>
      <img alt="Python" src="https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white"><br>
      <img alt="uv" src="https://img.shields.io/badge/uv-managed-4B5563"><br>
      <img alt="AnyIO" src="https://img.shields.io/badge/AnyIO-async-0F766E"><br>
      <img alt="Typer" src="https://img.shields.io/badge/Typer-CLI-059669">
    </td>
    <td valign="top" width="25%">
      <strong>Agent Core</strong><br>
      <img alt="Protocols" src="https://img.shields.io/badge/Protocols-OpenAI%20%7C%20Anthropic%20%7C%20Gemini-2563EB"><br>
      <img alt="Teams" src="https://img.shields.io/badge/Teams-round__robin%20%7C%20selector%20%7C%20swarm-7C3AED"><br>
      <img alt="Graph" src="https://img.shields.io/badge/Graph-DAG%20scheduler-9333EA"><br>
      <img alt="Tools" src="https://img.shields.io/badge/Tools-tool%20calling%202.0-1D4ED8">
    </td>
    <td valign="top" width="25%">
      <strong>集成层</strong><br>
      <img alt="Skills" src="https://img.shields.io/badge/Skills-Python%20hook%20%7C%20command-F59E0B"><br>
      <img alt="MCP" src="https://img.shields.io/badge/MCP-stdio%20%7C%20HTTP%2FSSE-DC2626"><br>
      <img alt="Plugins" src="https://img.shields.io/badge/Plugins-path%20%7C%20manifest%20%7C%20entry%20point-0891B2"><br>
      <img alt="Sandbox" src="https://img.shields.io/badge/Sandbox-process%20%7C%20windows__sandbox-374151">
    </td>
    <td valign="top" width="25%">
      <strong>状态与质量</strong><br>
      <img alt="Storage" src="https://img.shields.io/badge/Storage-SQLite%20%2B%20JSONL-0EA5E9"><br>
      <img alt="Session Memory" src="https://img.shields.io/badge/Memory-session__messages%20%2B%20shared__state-0284C7"><br>
      <img alt="Checkpoint" src="https://img.shields.io/badge/Recovery-resumable__checkpoints-16A34A"><br>
      <img alt="Static checks" src="https://img.shields.io/badge/Checks-Ruff%20%2B%20mypy-111827">
    </td>
  </tr>
</table>

## 项目目标

- 保持框架白板化、透明、易扩展。
- 把 Agent 工程问题和业务逻辑解耦。
- 用一个运行时承接 direct tools、skills、MCP、plugins、teams、session memory 和 graph workflows。
- 在协议、编排模式、工具边界继续演进时，尽量减少重构成本。

## Features

- 显式、白盒的运行时分层，核心由 scheduler、orchestrator、registry、storage 和 protocol adapter 组成。
- 面向 `OpenAI`、`Anthropic`、`Gemini` 风格载荷的统一协议适配模型调用。
- 面向 Tool Calling 2.0 的运行时，支持 direct tools、command skills、Python hook skills、MCP tools 和 plugin mounting。
- 支持 `single_agent`、`sub_agent`、`multi_agent_graph` 与 `Agent Teams` 协作模式。
- 支持带显式 `session_id` 的 session-oriented memory，持久化会话消息和 graph shared state。
- 为长流程 graph workflow 与顶层 team workflow 提供 resumable checkpoints。
- 对 command skills 与 `stdio` MCP 提供沙盒隔离，并支持 Windows 回退处理。
- 使用 SQLite + JSONL 持久化 trace、节点状态、运行事件、会话状态和恢复点。

## 当前架构

### 运行时拓扑

```mermaid
flowchart LR
    User[User or Script] --> CLI[Typer CLI]
    CLI --> Runtime[EasyAgentRuntime]
    Runtime --> Scheduler[GraphScheduler]
    Scheduler --> Orchestrator[AgentOrchestrator]
    Orchestrator --> Registry[ToolRegistry]
    Orchestrator --> Model[HttpModelClient]
    Model --> Adapter[ProtocolAdapter]
    Adapter --> Provider[Provider API]
    Runtime --> Plugins[Plugin Host]
    Plugins --> Registry
    Runtime --> MCP[McpClientManager]
    MCP --> Stdio[stdio transport]
    MCP --> Remote[HTTP/SSE transport]
    Registry --> Skills[Skills and Local Tools]
    Scheduler --> Store[SQLiteRunStore and JSONL traces]
    Orchestrator --> Store
    Sandbox[SandboxManager] -. isolates .-> Skills
    Sandbox -. isolates .-> Stdio
```

### 通信流程

```mermaid
sequenceDiagram
    participant CLI as CLI
    participant Scheduler as GraphScheduler
    participant Orchestrator as AgentOrchestrator
    participant Model as HttpModelClient
    participant Provider as Provider API
    participant Tool as Tool or Skill or MCP
    CLI->>Scheduler: run(input, session_id)
    Scheduler->>Orchestrator: dispatch entrypoint
    Orchestrator->>Model: complete(messages, tools)
    Model->>Provider: HTTP request
    Provider-->>Model: assistant text and tool calls
    Model-->>Orchestrator: normalized response
    Orchestrator->>Tool: invoke tool call
    Tool-->>Orchestrator: tool result
    Orchestrator->>Model: continue with tool output
    Orchestrator-->>Scheduler: final result
    Scheduler-->>CLI: result or resumable failure state
```

### 当前通信模型

- 模型调用统一经过 `HttpModelClient`，再由协议适配层转换为 `OpenAI`、`Anthropic` 或 `Gemini` 风格的请求载荷。
- Skills 通过 Python hook 或本地命令包装后注册为运行时工具。
- 当前代码中 MCP 远程通信实现是 `stdio` 和 `HTTP/SSE`。
- 运行轨迹、session messages、shared session state 与 checkpoints 会落到 SQLite 与 JSONL trace 文件。
- 命令型 skill 和 `stdio` MCP 可以被沙盒隔离层包裹。

## 状态、记忆与恢复

- 直接 agent 和顶层 team 运行可以通过 `--session-id` 复用历史对话上下文。
- Graph 运行会把 `shared_state` 绑定到同一个 `session_id`，后续运行可以继续使用持久化的图状态。
- Graph checkpoints 会在图启动时创建，并在每个节点成功后继续落盘。
- 顶层 team checkpoints 会在 team 启动时创建，并在每次完整 turn 结束后继续落盘。
- `easy-agent resume <run_id>` 会从最近一次 checkpoint 恢复可恢复的 graph 与顶层 team 运行。

## 项目结构

```text
src/
  agent_cli/           CLI entrypoints and commands
  agent_common/        shared models and tool abstractions
  agent_config/        typed config models and validation
  agent_graph/         orchestration, graph scheduling, team runtime
  agent_integrations/  skills, MCP, plugins, sandbox, storage
  agent_protocols/     protocol adapters and model client
  agent_runtime/       runtime assembly, benchmarks, long-run flows
skills/
  examples/            local demo skills
  real/                real validation skills
configs/
  longrun.example.yml  real MCP + skill validation
  teams.example.yml    Agent Teams examples
scripts/
  benchmark_modes.py   live benchmark for six execution modes
  windows/             easy-agent.ps1 / easy-agent.bat
tests/
  unit/                fast isolated unit tests
  integration/         live-service integration tests
```

## 协作模式

- `single_agent`：单 Agent 直接调用工具。
- `sub_agent`：协调者通过 `subagent__*` 工具把任务委托给子 Agent。
- `multi_agent_graph`：用 graph nodes 调度多个 Agent 并聚合结果。
- `Agent Teams`：
  - `round_robin`
  - `selector`
  - `swarm`

## Plugins、Skills 与 MCP

```python
from pathlib import Path

from agent_runtime.runtime import build_runtime

runtime = build_runtime('easy-agent.yml')
runtime.load(Path('skills/examples'))
runtime.load('third_party_plugin')
```

支持的挂载方式：

- 本地 skill 目录
- `plugin.yaml` 或 `easy-agent-plugin.yaml` 这类 plugin manifest
- `agent_runtime.plugins` 中暴露的 Python package entry point
- 在 YAML 配置中声明的 MCP server

## 快速开始

### 环境准备

```powershell
uv venv --python 3.12
uv sync --dev
```

### 本地凭据

运行时支持本地 `.env.local` 文件。可以把机器专属凭据放进去，避免每次手动导出环境变量。

示例键：

```dotenv
DEEPSEEK_API_KEY=your-key
PG_HOST=127.0.0.1
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=your-password
PG_DATABASE=postgres
REDIS_URL=redis://127.0.0.1:6379/0
```

### 常用命令

```powershell
uv run easy-agent doctor -c easy-agent.yml
uv run easy-agent skills list -c easy-agent.yml
uv run easy-agent plugins list -c easy-agent.yml
uv run easy-agent teams list -c configs/teams.example.yml
uv run easy-agent run "summarize the repository" --session-id demo-session -c easy-agent.yml
uv run easy-agent resume <run_id> -c configs/teams.example.yml
uv run python scripts/benchmark_modes.py --config easy-agent.yml --repeat 1
```

### Windows 快捷入口

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/easy-agent.ps1 --help
cmd /c scripts/windows/easy-agent.bat --help
```

## 真实使用效果

当前真实基准来自 `.easy-agent/benchmark-report.json`，底座模型是通过 OpenAI-compatible 路径访问的 DeepSeek。

| 模式 | 成功率 | 平均耗时 | 平均工具调用 | 平均子 Agent 调用 |
| --- | --- | ---: | ---: | ---: |
| `single_agent` | 1/1 | 6.1493 | 1 | 0 |
| `sub_agent` | 1/1 | 20.6691 | 1 | 1 |
| `multi_agent_graph` | 1/1 | 14.4803 | 2 | 0 |
| `team_round_robin` | 1/1 | 11.2187 | 1 | 0 |
| `team_selector` | 1/1 | 15.1416 | 1 | 0 |
| `team_swarm` | 1/1 | 11.0792 | 2 | 0 |

## 后续仍值得继续补强的工程点

当前实现已经包含 durable session memory 和 resumable checkpoints，下一步更值得补强的是：

- 在工具调用前和最终输出前增加显式 guardrail hooks。
- 增强 tracing 与 event streaming，覆盖 agent、team、tool、MCP 边界。
- 继续保持 team orchestration 的显式建模，而不是把多种模式折叠成一个模糊循环。
- 在未来版本中升级远程 MCP transport，但当前文档仍然只描述仓库已经实现的通信方式。

参考资料：

- OpenAI Agents SDK Sessions: <https://openai.github.io/openai-agents-python/sessions/>
- OpenAI Agents SDK Handoffs: <https://openai.github.io/openai-agents-python/handoffs/>
- OpenAI Agents SDK Guardrails: <https://openai.github.io/openai-agents-python/guardrails/>
- OpenAI Agents SDK Tracing: <https://openai.github.io/openai-agents-python/tracing/>
- AutoGen Teams: <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/tutorial/teams.html>
- AutoGen Selector Group Chat: <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/selector-group-chat.html>
- AutoGen Swarm: <https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/swarm.html>
- LangGraph Durable Execution: <https://docs.langchain.com/oss/python/langgraph/durable-execution>
- LangGraph Memory: <https://docs.langchain.com/oss/python/langgraph/memory>
- MCP Transports: <https://modelcontextprotocol.io/docs/concepts/transports>

## 测试

```powershell
uv run ruff check src tests scripts
uv run mypy src tests scripts
uv run python -m pytest tests/unit -q
uv run python -m pytest tests/integration -m real -q
```

如果要执行完整 live suite，本地 `.env.local` 或环境变量中还需要提供 PostgreSQL 与 Redis 的真实凭据。

## 致谢

- <a href="https://linux.do/"><img alt="Linux.do" src="https://linux.do/logo-128.svg" width="20"></a> [Linux.do](https://linux.do/) 提供了开放的社区讨论与知识分享环境。
- [![DeepSeek](https://img.shields.io/badge/DeepSeek-deepseek--chat-2563EB?style=flat-square)](https://www.deepseek.com/) 为本仓库真实验证流程提供了模型端点基线。

## License

MIT
