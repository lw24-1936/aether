"""Protocols module — MCP Client/Server and A2A Agent integration."""

from aether.protocols.mcp_client import MCPClient, MCPClientManager, MCPTool
from aether.protocols.mcp_server import MCPServer
from aether.protocols.a2a_client import A2AClient, AgentCard, TaskResult
from aether.protocols.tool_registry import ToolRegistry, RegisteredTool

__all__ = [
    "MCPClient",
    "MCPClientManager",
    "MCPTool",
    "MCPServer",
    "A2AClient",
    "AgentCard",
    "TaskResult",
    "ToolRegistry",
    "RegisteredTool",
]
