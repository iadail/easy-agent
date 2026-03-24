# easy-agent

`easy-agent` 是一个偏白板、业务无关、可工程化扩展的 Agent 开发底座。当前版本聚焦于稳定的多 Agent 运行时、可挂载的 skills / MCP / plugins、标准化 CLI，以及在 Windows 上可落地的真实长跑验证能力。

## 基线环境

- Python: `3.12.x`
- 虚拟环境: `uv venv --python 3.12`
- 安装依赖: `uv sync --dev`

## 快速开始

```powershell
uv venv --python 3.12
uv sync --dev
$env:DEEPSEEK_API_KEY = "your-key"
```

常用命令:

```powershell
uv run easy-agent doctor -c easy-agent.yml
uv run easy-agent skills list -c easy-agent.yml
uv run easy-agent plugins list -c easy-agent.yml
uv run easy-agent run "用工具返回一句话" -c easy-agent.yml
python scripts/benchmark_modes.py --config easy-agent.yml --repeat 2
```

Windows 快速入口:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/windows/easy-agent.ps1 --help
cmd /c scripts/windows/easy-agent.bat --help
```

## 设计要点

- `OpenAI`、`Anthropic`、`Gemini` 三类 tool-calling 协议统一映射到内部事件模型。
- 通过 `runtime.load(...)` 统一挂载本地路径插件、插件清单和 Python 包 entry point 插件。
- skill 同时支持 `Python Hook` 和本地命令，两者都以工具方式注册。
- MCP 支持 `stdio` 和 `HTTP/SSE` 两种传输；Windows 下的 `stdio` 传输带有本地生命周期管理和回退逻辑。
- 调度层支持 `single_agent`、`subAgent` 和 DAG `multi_agent_graph`。
- 安全层支持 `off`、`process`、`auto`、`windows_sandbox` 模式，默认对命令型 skill 和 `stdio MCP` 开启隔离。
- SQLite + JSONL 持久化用于运行轨迹、事件和真实长跑结果分析。

## 插件挂载

```python
from pathlib import Path

from agent_runtime.runtime import build_runtime

runtime = build_runtime("easy-agent.yml")
runtime.load(Path("skills/examples"))
runtime.load("third_party_plugin")
```

本地插件路径支持：

- 直接传入 skill 目录或 skill 根目录
- 传入 `easy-agent-plugin.yaml` / `plugin.yaml` 清单文件，内部可声明 `skills` 和 `mcp`

第三方包插件通过 `agent_runtime.plugins` entry point 暴露 `register(host)`。

## 真实长跑验证

真实长跑配置见 `configs/longrun.example.yml`，会联动：

- `skills/real/html_page_builder`
- filesystem MCP
- Redis MCP
- PostgreSQL MCP
- `single_agent`
- `sub_agent`
- `multi_agent_graph`

手动执行示例：

```powershell
$env:DEEPSEEK_API_KEY = "your-key"
$env:PG_HOST = "127.0.0.1"
$env:PG_PORT = "5432"
$env:PG_USER = "postgres"
$env:PG_PASSWORD = "your-password"
$env:PG_DATABASE = "postgres"
$env:REDIS_URL = "redis://127.0.0.1:6379/0"
uv run python -m pytest tests/integration -m real -q
```

如果直接运行 `easy-agent mcp list -c configs/longrun.example.yml`，请先确保 `.easy-agent/longrun/artifacts` 已存在。

## 目录

- `src/agent_cli`: CLI 入口与命令组
- `src/agent_common`: 通用模型与工具注册抽象
- `src/agent_config`: 配置模型与加载逻辑
- `src/agent_graph`: agent orchestration 与 DAG 调度
- `src/agent_integrations`: MCP、skills、plugins、sandbox、storage 集成
- `src/agent_protocols`: 协议适配与模型客户端
- `src/agent_runtime`: runtime 组装与真实长跑逻辑
- `skills/examples`: 示例 skills
- `skills/real`: 真实验证 skills
- `configs`: 长跑与集成示例配置
- `scripts/windows`: Windows CLI 启动脚本
- `scripts/benchmark_modes.py`: DeepSeek 模式基准脚本
- `tests/unit`: 单元测试
- `tests/integration`: 真实集成测试
- `easy-agent.yml`: 基础运行配置
