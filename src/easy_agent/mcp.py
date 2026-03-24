from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import IO, Any, cast

import httpx
from httpx_sse import aconnect_sse

from easy_agent.config import McpServerConfig
from easy_agent.models import ToolSpec
from easy_agent.sandbox import ProcessHandle, SandboxManager, SandboxRequest, SandboxTarget


def _frame_message(payload: dict[str, Any]) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n\r\n" + body



def _read_framed_sync(stream: IO[bytes]) -> dict[str, Any]:
    headers = b""
    while b"\r\n\r\n" not in headers:
        chunk = stream.read(1)
        if not chunk:
            raise EOFError("MCP stream closed")
        headers += chunk
    raw_headers, _, remainder = headers.partition(b"\r\n\r\n")
    content_length = 0
    for line in raw_headers.decode("utf-8").split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
            break
    body = remainder
    while len(body) < content_length:
        chunk = stream.read(content_length - len(body))
        if not chunk:
            raise EOFError("MCP stream closed before message body completed")
        body += chunk
    return cast(dict[str, Any], json.loads(body.decode("utf-8")))


class BaseMcpClient(ABC):
    def __init__(self, config: McpServerConfig) -> None:
        self.config = config

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def list_tools(self) -> list[ToolSpec]: ...

    @abstractmethod
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any: ...

    @abstractmethod
    async def aclose(self) -> None: ...


class StdioMcpClient(BaseMcpClient):
    def __init__(self, config: McpServerConfig, sandbox_manager: SandboxManager) -> None:
        super().__init__(config)
        self._process: ProcessHandle | None = None
        self._request_id = 0
        self._sandbox_manager = sandbox_manager

    async def start(self) -> None:
        if not self.config.command:
            raise ValueError("stdio MCP transport requires a command")
        self._process = self._sandbox_manager.start(
            SandboxRequest(
                command=self.config.command,
                cwd=Path.cwd(),
                env=self.config.env,
                timeout_seconds=self.config.timeout_seconds,
                target=SandboxTarget.STDIO_MCP,
            )
        )

    async def _request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if self._process is None or self._process.stdin is None or self._process.stdout is None:
            raise RuntimeError("MCP stdio process is not running")
        self._request_id += 1
        payload = {"jsonrpc": "2.0", "id": self._request_id, "method": method, "params": params}
        framed = _frame_message(payload)

        def _write() -> None:
            assert self._process is not None and self._process.stdin is not None
            self._process.stdin.write(framed)
            self._process.stdin.flush()

        await asyncio.to_thread(_write)
        stream = self._process.stdout
        response = await asyncio.to_thread(_read_framed_sync, stream)
        return cast(dict[str, Any], response["result"])

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._request("tools/list", {})
        return [
            ToolSpec(
                name=item["name"],
                description=item["description"],
                input_schema=item["inputSchema"],
            )
            for item in result["tools"]
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await self._request("tools/call", {"name": name, "arguments": arguments})
        return result["content"]

    async def aclose(self) -> None:
        if self._process is not None:
            self._process.terminate()
            await asyncio.to_thread(self._process.wait)


class HttpSseMcpClient(BaseMcpClient):
    def __init__(self, config: McpServerConfig) -> None:
        super().__init__(config)
        self._client = httpx.AsyncClient(timeout=config.timeout_seconds)
        self.notifications: list[dict[str, Any]] = []
        self._sse_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self.config.sse_url:
            self._sse_task = asyncio.create_task(self._consume_sse(self.config.sse_url))

    async def _consume_sse(self, url: str) -> None:
        async with aconnect_sse(self._client, "GET", url) as event_source:
            async for event in event_source.aiter_sse():
                if event.data:
                    self.notifications.append(cast(dict[str, Any], json.loads(event.data)))
                    return

    async def _rpc(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.config.rpc_url:
            raise ValueError("http_sse transport requires rpc_url")
        response = await self._client.post(
            self.config.rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
        )
        response.raise_for_status()
        payload = cast(dict[str, Any], response.json())
        return cast(dict[str, Any], payload["result"])

    async def list_tools(self) -> list[ToolSpec]:
        result = await self._rpc("tools/list", {})
        return [
            ToolSpec(
                name=item["name"],
                description=item["description"],
                input_schema=item["inputSchema"],
            )
            for item in result["tools"]
        ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        result = await self._rpc("tools/call", {"name": name, "arguments": arguments})
        return result["content"]

    async def aclose(self) -> None:
        if self._sse_task is not None:
            self._sse_task.cancel()
        await self._client.aclose()


class McpClientManager:
    def __init__(self, configs: list[McpServerConfig], sandbox_manager: SandboxManager) -> None:
        self._sandbox_manager = sandbox_manager
        self._clients: dict[str, BaseMcpClient] = {}
        self._started = False
        for config in configs:
            self.add_server(config)

    def add_server(self, config: McpServerConfig) -> None:
        client = self._build_client(config)
        self._clients[config.name] = client
        if self._started:
            asyncio.run(client.start())

    def _build_client(self, config: McpServerConfig) -> BaseMcpClient:
        if config.transport == "stdio":
            return StdioMcpClient(config, self._sandbox_manager)
        if config.transport == "http_sse":
            return HttpSseMcpClient(config)
        raise ValueError(f"Unsupported MCP transport: {config.transport}")

    async def start(self) -> None:
        for client in self._clients.values():
            await client.start()
        self._started = True

    async def list_servers(self) -> dict[str, list[ToolSpec]]:
        result: dict[str, list[ToolSpec]] = {}
        for name, client in self._clients.items():
            result[name] = await client.list_tools()
        return result

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, Any]) -> Any:
        return await self._clients[server_name].call_tool(tool_name, arguments)

    async def aclose(self) -> None:
        for client in self._clients.values():
            await client.aclose()
        self._started = False


