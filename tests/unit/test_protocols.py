from agent_common.models import ChatMessage, Protocol, ToolSpec
from agent_config.app import ModelConfig
from agent_protocols.client import AnthropicAdapter, GeminiAdapter, OpenAIAdapter, resolve_protocol


def test_auto_protocol_prefers_openai_for_deepseek() -> None:
    config = ModelConfig(provider="deepseek", protocol=Protocol.AUTO)

    assert resolve_protocol(config).protocol is Protocol.OPENAI


def test_anthropic_adapter_parses_tool_use() -> None:
    adapter = AnthropicAdapter()
    response = adapter.parse_response(
        {
            "content": [
                {"type": "text", "text": "working"},
                {"type": "tool_use", "id": "call_1", "name": "python_echo", "input": {"prompt": "hi"}},
            ]
        }
    )

    assert response.text == "working"
    assert response.tool_calls[0].name == "python_echo"


def test_gemini_builds_function_declarations() -> None:
    adapter = GeminiAdapter()
    payload = adapter.build_payload(
        ModelConfig(provider="gemini", protocol=Protocol.GEMINI),
        [ChatMessage(role="user", content="hello")],
        [ToolSpec(name="python_echo", description="Echo", input_schema={"type": "object"})],
    )

    assert payload["tools"][0]["functionDeclarations"][0]["name"] == "python_echo"


def test_openai_parses_tool_calls() -> None:
    adapter = OpenAIAdapter()
    response = adapter.parse_response(
        {
            "choices": [
                {
                    "message": {
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "function": {"name": "command_echo", "arguments": "{\"prompt\":\"hello\"}"},
                            }
                        ],
                    }
                }
            ]
        }
    )

    assert response.tool_calls[0].arguments["prompt"] == "hello"


