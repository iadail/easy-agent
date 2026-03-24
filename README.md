# easy-agent

`easy-agent` 是一个偏白板、业务无关、可工程化扩展的 Agent 开发底座。当前版本在既有运行时之上补齐了统一插件挂载、模式基准测试和执行沙盒，让外部技能、MCP 与后续扩展都可以通过统一的 `runtime.load(...)` 接口接入。

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

## 设计要点

- `OpenAI`、`Anthropic`、`Gemini` 三类 tool-calling 协议统一映射到内部事件模型。
- 通过 `runtime.load(...)` 统一挂载本地路径插件、插件清单和 Python 包 entry point 插件。
- skill 同时支持 `Python Hook` 和本地命令，两者都以工具方式注册。
- MCP 支持 `stdio` 和 `HTTP/SSE` 两种传输；`stdio` 默认走沙盒进程运行。
- 调度层支持 single-agent、subAgent 和 DAG multi-agent graph。
- 安全层支持 `off`、`process`、`auto`、`windows_sandbox` 模式，默认对命令型 skill 和 `stdio MCP` 开启隔离。
- SQLite + JSONL 持久化用于运行轨迹、事件和 benchmark 结果分析。

## 插件挂载

```python
from pathlib import Path

from easy_agent.runtime import build_runtime

runtime = build_runtime("easy-agent.yml")
runtime.load(Path("examples/skills"))
runtime.load("third_party_plugin")
```

本地插件路径支持：

- 直接传入 skill 目录或 skill 根目录
- 传入 `easy-agent-plugin.yaml` / `plugin.yaml` 清单文件，内部可声明 `skills` 和 `mcp`

第三方包插件通过 `easy_agent.plugins` entry point 暴露 `register(host)`。

## 目录

- `src/easy_agent`: 核心运行时、插件宿主、沙盒、基准与 CLI
- `examples/skills`: 示例 skills
- `scripts/benchmark_modes.py`: DeepSeek 模式基准脚本
- `tests`: 单元与集成测试
- `easy-agent.yml`: 声明式运行配置
