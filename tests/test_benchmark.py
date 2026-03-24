from easy_agent.benchmark import build_default_cases, summarize_trace
from easy_agent.config import AppConfig, ModelConfig


def build_config() -> AppConfig:
    return AppConfig.model_validate(
        {
            "model": ModelConfig().model_dump(),
            "graph": {
                "entrypoint": "coordinator",
                "agents": [{"name": "coordinator", "tools": ["python_echo"], "sub_agents": []}],
                "nodes": [],
            },
            "skills": [{"path": "examples/skills"}],
            "mcp": [],
            "storage": {"path": ".easy-agent", "database": "state.db"},
            "security": {"allowed_commands": [["cmd", "/c", "echo"]]},
        }
    )


def test_build_default_cases_contains_all_modes() -> None:
    cases = build_default_cases(build_config())

    assert [case.mode for case in cases] == ["single_agent", "sub_agent", "multi_agent_graph"]


def test_summarize_trace_counts_tool_and_subagent_calls() -> None:
    trace = {
        "events": [
            {
                "kind": "agent_response",
                "payload": {
                    "tool_calls": [
                        {"name": "python_echo"},
                        {"name": "subagent__analyst"},
                    ]
                },
            }
        ]
    }
    output = {"result": {"status": "ok"}, "nodes": {"a": 1, "b": 2}}

    record = summarize_trace(trace, "openai", output, 1.2345, "sub_agent", 1)

    assert record.protocol == "openai"
    assert record.tool_call_count == 2
    assert record.subagent_call_count == 1
    assert record.graph_node_count == 2
    assert record.success is True
