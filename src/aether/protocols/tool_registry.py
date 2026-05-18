"""Unified tool registry — built-in + MCP + custom tools.

Single interface for all tools regardless of source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from aether.protocols.mcp_client import MCPTool


@dataclass
class RegisteredTool:
    """A tool registered in the unified registry."""
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable | None = None       # Built-in handler
    mcp_tool: MCPTool | None = None       # MCP tool reference
    permission_level: int = 3              # Default: EXECUTE
    source: str = "built-in"              # "built-in" | "mcp" | "custom"

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function calling format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    """Unified registry for all tools: built-in, MCP, and custom."""

    def __init__(self):
        self._tools: dict[str, RegisteredTool] = {}

    def register(
        self,
        name: str,
        description: str,
        parameters: dict[str, Any],
        handler: Callable | None = None,
        permission_level: int = 3,
        source: str = "built-in",
    ) -> RegisteredTool:
        """Register a tool."""
        tool = RegisteredTool(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            permission_level=permission_level,
            source=source,
        )
        self._tools[name] = tool
        return tool

    def register_mcp_tool(self, mcp_tool: MCPTool, mcp_client_call: Callable) -> RegisteredTool:
        """Register an MCP tool with a call wrapper."""
        async def wrapper(**kwargs):
            return await mcp_client_call(mcp_tool.name, kwargs)

        tool = RegisteredTool(
            name=mcp_tool.name,
            description=mcp_tool.description,
            parameters=mcp_tool.input_schema,
            handler=wrapper,
            mcp_tool=mcp_tool,
            permission_level=3,
            source="mcp",
        )
        self._tools[mcp_tool.name] = tool
        return tool

    def get(self, name: str) -> RegisteredTool | None:
        return self._tools.get(name)

    def list_all(self) -> list[RegisteredTool]:
        return list(self._tools.values())

    def list_by_source(self, source: str) -> list[RegisteredTool]:
        return [t for t in self._tools.values() if t.source == source]

    def to_openai_format(self) -> list[dict]:
        return [t.to_openai_format() for t in self._tools.values()]

    def remove(self, name: str) -> bool:
        if name in self._tools:
            del self._tools[name]
            return True
        return False

    def clear_source(self, source: str) -> int:
        """Remove all tools from a specific source. Returns count removed."""
        to_remove = [n for n, t in self._tools.items() if t.source == source]
        for name in to_remove:
            del self._tools[name]
        return len(to_remove)

    def __len__(self) -> int:
        return len(self._tools)

    def __contains__(self, name: str) -> bool:
        return name in self._tools
