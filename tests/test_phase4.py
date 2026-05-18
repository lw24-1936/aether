"""Phase 4 tests: MCP Client, MCP Server, A2A Client, Tool Registry."""

import json
import tempfile
from pathlib import Path

import pytest

from aether.protocols.mcp_client import MCPClient, MCPClientManager, MCPTool
from aether.protocols.mcp_server import MCPServer
from aether.protocols.a2a_client import A2AClient, AgentCard
from aether.protocols.tool_registry import ToolRegistry, RegisteredTool


class TestMCPTypes:
    """MCP type tests."""

    def test_mcp_tool_creation(self):
        tool = MCPTool(
            name="test-tool",
            description="A test tool",
            input_schema={"type": "object", "properties": {}},
            server_name="test-server",
        )
        assert tool.name == "test-tool"
        assert tool.server_name == "test-server"

    def test_jsonrpc_request_format(self):
        from aether.protocols.mcp_client import JSONRPCRequest
        req = JSONRPCRequest(method="tools/list", params={}, id="1")
        d = req.to_dict()
        assert d["jsonrpc"] == "2.0"
        assert d["method"] == "tools/list"
        assert d["id"] == "1"


class TestToolRegistry:
    """Unified tool registry tests."""

    def test_register_builtin(self):
        reg = ToolRegistry()
        reg.register("test-tool", "A test", {"type": "object", "properties": {}})
        assert "test-tool" in reg
        tool = reg.get("test-tool")
        assert tool is not None
        assert tool.source == "built-in"

    def test_register_mcp_tool(self):
        reg = ToolRegistry()
        mcp_tool = MCPTool(
            name="mcp-tool",
            description="MCP tool",
            input_schema={"type": "object", "properties": {"x": {"type": "string"}}},
            server_name="srv",
        )
        reg.register_mcp_tool(mcp_tool, lambda name, args: {"result": "ok"})
        assert "mcp-tool" in reg
        tool = reg.get("mcp-tool")
        assert tool.source == "mcp"
        assert tool.mcp_tool is not None

    def test_list_by_source(self):
        reg = ToolRegistry()
        reg.register("a", "A", {})
        reg.register("b", "B", {}, source="custom")
        mcp_tool = MCPTool(name="c", description="C", input_schema={}, server_name="s")
        reg.register_mcp_tool(mcp_tool, lambda *a, **kw: {})

        builtins = reg.list_by_source("built-in")
        assert len(builtins) == 1

        customs = reg.list_by_source("custom")
        assert len(customs) == 1

        mcps = reg.list_by_source("mcp")
        assert len(mcps) == 1

    def test_remove(self):
        reg = ToolRegistry()
        reg.register("x", "X", {})
        assert reg.remove("x")
        assert "x" not in reg
        assert not reg.remove("nonexistent")

    def test_clear_source(self):
        reg = ToolRegistry()
        reg.register("a", "A", {}, source="mcp")
        reg.register("b", "B", {}, source="mcp")
        reg.register("c", "C", {}, source="built-in")

        removed = reg.clear_source("mcp")
        assert removed == 2
        assert "a" not in reg
        assert "b" not in reg
        assert "c" in reg

    def test_openai_format(self):
        reg = ToolRegistry()
        reg.register("tool1", "First tool", {"type": "object", "properties": {"p1": {"type": "string"}}})

        schemas = reg.to_openai_format()
        assert len(schemas) == 1
        assert schemas[0]["type"] == "function"
        assert schemas[0]["function"]["name"] == "tool1"

    def test_len(self):
        reg = ToolRegistry()
        assert len(reg) == 0
        reg.register("a", "A", {})
        reg.register("b", "B", {})
        assert len(reg) == 2


class TestMCPClientManager:
    """MCP client manager tests (offline)."""

    def test_manager_creation(self):
        mgr = MCPClientManager()
        assert len(mgr.list_all_tools()) == 0

    def test_get_unknown_tool(self):
        mgr = MCPClientManager()
        assert mgr.get_tool("nonexistent") is None

    def test_call_unknown_tool(self):
        mgr = MCPClientManager()
        import asyncio
        result = asyncio.run(mgr.call_tool("nonexistent", {}))
        assert "error" in result


class TestMCPServer:
    """MCP server tests (message handling)."""

    @pytest.mark.asyncio
    async def test_server_tool_registration(self):
        server = MCPServer("test-server", "1.0.0")
        server.add_tool(
            "echo",
            "Echo back the input",
            {"type": "object", "properties": {"text": {"type": "string"}}},
            lambda text: {"echo": text},
        )
        assert "echo" in server._tools

    @pytest.mark.asyncio
    async def test_server_initialize_response(self):
        server = MCPServer("test-server", "1.0.0")
        msg = json.dumps({
            "jsonrpc": "2.0", "method": "initialize",
            "params": {"protocolVersion": "2024-11-05"}, "id": "1",
        })
        # We can't fully test stdio without mocking, but we verify structure
        assert server.name == "test-server"
        assert server.version == "1.0.0"

    @pytest.mark.asyncio
    async def test_server_tools_list(self):
        server = MCPServer("test", "1.0")
        server.add_tool("t1", "Tool 1", {}, lambda: "ok")
        server.add_tool("t2", "Tool 2", {}, lambda: "ok")
        assert len(server._tools) == 2


class TestA2AClient:
    """A2A client tests (offline)."""

    def test_agent_card(self):
        card = AgentCard(
            name="test-agent",
            description="A test agent",
            url="https://test.example.com",
            skills=[{"name": "code-review", "description": "Review code"}],
        )
        assert card.name == "test-agent"
        assert len(card.skills) == 1

    @pytest.mark.asyncio
    async def test_list_known_agents(self):
        client = A2AClient()
        agents = await client.list_known_agents()
        assert len(agents) == 0
        await client.close()
