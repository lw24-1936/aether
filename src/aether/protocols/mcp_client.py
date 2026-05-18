"""MCP (Model Context Protocol) client — JSON-RPC 2.0 over stdio.

Connect to MCP-compatible servers, discover tools, and call them.

Spec: https://spec.modelcontextprotocol.io/
"""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════
# JSON-RPC 2.0 types
# ═══════════════════════════════════════════════════════════

@dataclass
class JSONRPCRequest:
    jsonrpc: str = "2.0"
    method: str = ""
    params: dict[str, Any] = field(default_factory=dict)
    id: str = ""

    def to_dict(self) -> dict:
        return {
            "jsonrpc": self.jsonrpc,
            "method": self.method,
            "params": self.params,
            "id": self.id,
        }


@dataclass
class JSONRPCResponse:
    jsonrpc: str = "2.0"
    result: Any = None
    error: dict | None = None
    id: str = ""

    @property
    def is_error(self) -> bool:
        return self.error is not None


@dataclass
class MCPTool:
    """An MCP tool discovered from a server."""
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    server_name: str = ""


@dataclass
class MCPResource:
    """An MCP resource exposed by a server."""
    uri: str
    name: str
    description: str = ""
    mime_type: str = "text/plain"


# ═══════════════════════════════════════════════════════════
# MCP Client
# ═══════════════════════════════════════════════════════════

class MCPClient:
    """JSON-RPC 2.0 client for MCP servers over stdio.

    Usage:
        client = MCPClient("my-server", ["python", "server.py"])
        await client.connect()
        tools = await client.list_tools()
        result = await client.call_tool("tool_name", {"arg": "value"})
        await client.disconnect()
    """

    def __init__(self, name: str, command: list[str], env: dict[str, str] | None = None):
        self.name = name
        self.command = command
        self.env = env
        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[str, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._buf = ""
        self._connected = False
        self._server_info: dict = {}

    async def connect(self, timeout: float = 30.0) -> None:
        """Start the MCP server subprocess and initialize."""
        self._process = await asyncio.create_subprocess_exec(
            *self.command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=self.env,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        self._connected = True

        # Initialize
        result = await self._call("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aether-mcp-client", "version": "0.1.0"},
        }, timeout=timeout)
        self._server_info = result.get("result", {})

    async def disconnect(self) -> None:
        """Stop the MCP server and clean up."""
        self._connected = False
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._process:
            try:
                self._process.stdin.close()
            except Exception:
                pass
            try:
                self._process.kill()
                await self._process.wait()
            except Exception:
                pass

    async def list_tools(self) -> list[MCPTool]:
        """Discover tools from the MCP server."""
        result = await self._call("tools/list", {})
        tools_data = result.get("result", {}).get("tools", [])
        return [
            MCPTool(
                name=t.get("name", ""),
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
                server_name=self.name,
            )
            for t in tools_data
        ]

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server."""
        result = await self._call("tools/call", {
            "name": tool_name,
            "arguments": arguments,
        })
        return result.get("result", {})

    async def list_resources(self) -> list[MCPResource]:
        """List resources from the MCP server."""
        result = await self._call("resources/list", {})
        resources = result.get("result", {}).get("resources", [])
        return [
            MCPResource(
                uri=r.get("uri", ""),
                name=r.get("name", ""),
                description=r.get("description", ""),
                mime_type=r.get("mimeType", "text/plain"),
            )
            for r in resources
        ]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        """Read a resource from the MCP server."""
        result = await self._call("resources/read", {"uri": uri})
        return result.get("result", {})

    async def list_prompts(self) -> list[dict]:
        """List prompts from the MCP server."""
        result = await self._call("prompts/list", {})
        return result.get("result", {}).get("prompts", [])

    # ═══════════════════════════════════════════════
    # Internal
    # ═══════════════════════════════════════════════

    async def _call(self, method: str, params: dict, timeout: float = 30.0) -> dict:
        """Send a JSON-RPC request and wait for the response."""
        req_id = uuid.uuid4().hex[:8]
        req = JSONRPCRequest(method=method, params=params, id=req_id)

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[req_id] = future

        await self._send(req.to_dict())

        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError:
            self._pending.pop(req_id, None)
            raise TimeoutError(f"MCP call '{method}' timed out after {timeout}s")

    async def _send(self, data: dict) -> None:
        """Send a JSON-RPC message to the server."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("MCP client not connected")
        msg = json.dumps(data) + "\n"
        self._process.stdin.write(msg.encode())
        await self._process.stdin.drain()

    async def _read_loop(self) -> None:
        """Read JSON-RPC responses from stdout."""
        if not self._process or not self._process.stdout:
            return
        try:
            while self._connected:
                line = await self._process.stdout.readline()
                if not line:
                    break
                try:
                    data = json.loads(line.decode())
                except json.JSONDecodeError:
                    continue

                resp_id = data.get("id", "")
                if resp_id and resp_id in self._pending:
                    future = self._pending.pop(resp_id)
                    if not future.done():
                        future.set_result(data)
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════
# MCP Client Manager
# ═══════════════════════════════════════════════════════════

class MCPClientManager:
    """Manages multiple MCP client connections."""

    def __init__(self):
        self._clients: dict[str, MCPClient] = {}
        self._tools: dict[str, MCPTool] = {}  # tool_name → MCPTool (with server info)

    async def connect_server(
        self, name: str, command: list[str], env: dict[str, str] | None = None
    ) -> MCPClient:
        """Connect to an MCP server and discover its tools."""
        if name in self._clients:
            await self._clients[name].disconnect()

        client = MCPClient(name, command, env)
        await client.connect()
        self._clients[name] = client

        # Discover tools
        tools = await client.list_tools()
        for tool in tools:
            self._tools[tool.name] = tool

        return client

    async def disconnect_all(self) -> None:
        for client in self._clients.values():
            await client.disconnect()
        self._clients.clear()
        self._tools.clear()

    def get_tool(self, name: str) -> MCPTool | None:
        return self._tools.get(name)

    def list_all_tools(self) -> list[MCPTool]:
        return list(self._tools.values())

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call an MCP tool (routes to correct server)."""
        tool = self._tools.get(tool_name)
        if not tool:
            return {"error": f"Unknown MCP tool: {tool_name}"}

        client = self._clients.get(tool.server_name)
        if not client:
            return {"error": f"MCP server not connected: {tool.server_name}"}

        return await client.call_tool(tool_name, arguments)
