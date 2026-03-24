from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from easy_agent.config import McpServerConfig
from easy_agent.mcp import McpClientManager
from easy_agent.sandbox import SandboxManager, SandboxMode, SandboxTarget

STDIO_SERVER = r"""
import asyncio
import json
import sys

async def read_message():
    headers = b""
    while b"\r\n\r\n" not in headers:
        headers += await asyncio.get_event_loop().run_in_executor(None, sys.stdin.buffer.read, 1)
    raw_headers, _, rest = headers.partition(b"\r\n\r\n")
    length = 0
    for line in raw_headers.decode().split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    body = rest
    while len(body) < length:
        body += await asyncio.get_event_loop().run_in_executor(None, sys.stdin.buffer.read, length - len(body))
    return json.loads(body.decode())

def write_message(payload):
    body = json.dumps(payload).encode()
    sys.stdout.buffer.write(b"Content-Length: " + str(len(body)).encode() + b"\r\n\r\n" + body)
    sys.stdout.buffer.flush()

async def main():
    while True:
        request = await read_message()
        if request["method"] == "tools/list":
            write_message({"jsonrpc": "2.0", "id": request["id"], "result": {"tools": [{"name": "echo", "description": "Echo", "inputSchema": {"type": "object"}}]}})
        elif request["method"] == "tools/call":
            write_message({"jsonrpc": "2.0", "id": request["id"], "result": {"content": request["params"]["arguments"]}})

asyncio.run(main())
"""


class McpHttpHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/sse":
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        self.wfile.write(b"data: {\"status\":\"ready\"}\n\n")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/rpc":
            self.send_response(404)
            self.end_headers()
            return
        content_length = int(self.headers["Content-Length"])
        payload = json.loads(self.rfile.read(content_length))
        if payload["method"] == "tools/list":
            result = {"tools": [{"name": "remote", "description": "Remote tool", "inputSchema": {"type": "object"}}]}
        else:
            result = {"content": payload["params"]["arguments"]}
        body = json.dumps({"jsonrpc": "2.0", "id": payload["id"], "result": result}).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        del format, args
        return


@pytest.mark.asyncio
async def test_mcp_manager_supports_stdio_and_http_sse() -> None:
    server = HTTPServer(("127.0.0.1", 0), McpHttpHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    rpc_port = server.server_address[1]
    sandbox_manager = SandboxManager(
        mode=SandboxMode.PROCESS,
        targets=[SandboxTarget.STDIO_MCP],
        env_allowlist=["PATH", "SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP"],
    )
    manager = McpClientManager(
        [
            McpServerConfig(
                name="stdio",
                transport="stdio",
                command=[sys.executable, "-c", STDIO_SERVER],
            ),
            McpServerConfig(
                name="remote",
                transport="http_sse",
                rpc_url=f"http://127.0.0.1:{rpc_port}/rpc",
                sse_url=f"http://127.0.0.1:{rpc_port}/sse",
            ),
        ],
        sandbox_manager,
    )

    await manager.start()
    try:
        servers = await manager.list_servers()
        echo_result = await manager.call_tool("stdio", "echo", {"prompt": "hello"})
        remote_result = await manager.call_tool("remote", "remote", {"prompt": "hi"})
    finally:
        await manager.aclose()
        server.shutdown()
        thread.join()

    assert servers["stdio"][0].name == "echo"
    assert servers["remote"][0].name == "remote"
    assert echo_result["prompt"] == "hello"
    assert remote_result["prompt"] == "hi"
