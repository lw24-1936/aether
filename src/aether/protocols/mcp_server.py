"""MCP Server — exposes Aether's capabilities as MCP tools/resources.

Other agents can connect to Aether as an MCP server and use its tools.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


# ═══════════════════════════════════════════════════════════
# MCP Server
# ═══════════════════════════════════════════════════════════

@dataclass
class MCPServerTool:
    """A tool exposed by the MCP server."""
    name: str
    description: str
    input_schema: dict[str, Any]
    handler: Callable  # async (params) → result


class MCPServer:
    """Minimal MCP server over stdio (JSON-RPC 2.0).

    Usage:
        server = MCPServer("aether-mcp", "0.1.0")
        server.add_tool(...)
        await server.run()
    """

    def __init__(self, name: str, version: str):
        self.name = name
        self.version = version
        self._tools: dict[str, MCPServerTool] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None

    def add_tool(
        self,
        name: str,
        description: str,
        input_schema: dict[str, Any],
        handler: Callable,
    ) -> None:
        """Register a tool."""
        self._tools[name] = MCPServerTool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=handler,
        )

    async def run(self) -> None:
        """Run the MCP server on stdio."""
        loop = asyncio.get_event_loop()
        self._reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(self._reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)
        w_transport, w_protocol = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        self._writer = asyncio.StreamWriter(w_transport, w_protocol, self._reader, loop)

        buf = b""
        while True:
            try:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    if line.strip():
                        await self._handle_message(line.decode())
            except (asyncio.CancelledError, KeyboardInterrupt):
                break
            except Exception:
                break

    def run_sync(self) -> None:
        """Synchronous wrapper."""
        asyncio.run(self.run())

    async def _handle_message(self, raw: str) -> None:
        """Process an incoming JSON-RPC message."""
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await self._send_error(None, -32700, "Parse error")
            return

        method = msg.get("method", "")
        msg_id = msg.get("id")
        params = msg.get("params", {})

        if method == "initialize":
            await self._send_result(msg_id, {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": self.name, "version": self.version},
                "capabilities": {"tools": {}},
            })
        elif method == "tools/list":
            tools = [
                {
                    "name": t.name,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                }
                for t in self._tools.values()
            ]
            await self._send_result(msg_id, {"tools": tools})
        elif method == "tools/call":
            tool_name = params.get("name", "")
            tool_args = params.get("arguments", {})
            tool = self._tools.get(tool_name)
            if not tool:
                await self._send_error(msg_id, -32601, f"Unknown tool: {tool_name}")
                return

            try:
                if asyncio.iscoroutinefunction(tool.handler):
                    result = await tool.handler(**tool_args)
                else:
                    result = tool.handler(**tool_args)
                await self._send_result(msg_id, {"content": [{"type": "text", "text": json.dumps(result)}]})
            except Exception as e:
                await self._send_result(msg_id, {
                    "content": [{"type": "text", "text": json.dumps({"error": str(e)})}],
                    "isError": True,
                })
        elif method == "resources/list":
            await self._send_result(msg_id, {"resources": []})
        elif method == "prompts/list":
            await self._send_result(msg_id, {"prompts": []})
        else:
            await self._send_error(msg_id, -32601, f"Method not found: {method}")

    async def _send_result(self, msg_id: Any, result: Any) -> None:
        await self._send({"jsonrpc": "2.0", "id": msg_id, "result": result})

    async def _send_error(self, msg_id: Any, code: int, message: str) -> None:
        await self._send({
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {"code": code, "message": message},
        })

    async def _send(self, data: dict) -> None:
        if not self._writer:
            return
        msg = json.dumps(data) + "\n"
        self._writer.write(msg.encode())
        await self._writer.drain()
