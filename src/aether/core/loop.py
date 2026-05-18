"""Agent Loop — the core execution cycle.

Planner → Executor → Observer → Reflector
with retry, degradation, and user escalation.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from aether.core.config import AetherConfig
from aether.core.llm import LLMClient, ChatMessage
from aether.core.models import Artifact, StreamEvent, Task, TaskStatus
from aether.tools.terminal import TerminalTool
from aether.tools.file import FileTools


# ═══════════════════════════════════════════════════════════
# Tool registry
# ═══════════════════════════════════════════════════════════

@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Any  # callable


def _build_tool_registry(workdir: Path) -> dict[str, ToolDef]:
    terminal = TerminalTool(workdir=workdir)
    file_tools = FileTools(workdir=workdir)

    return {
        "terminal": ToolDef(
            name="terminal",
            description="Execute shell commands. Returns {output, exit_code}.",
            parameters=terminal.parameters,
            handler=terminal,
        ),
        "read_file": ToolDef(
            name="read_file",
            description="Read a file with line numbers. Args: path, offset (default 1), limit (default 500).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "File path"},
                    "offset": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["path"],
            },
            handler=file_tools,
        ),
        "write_file": ToolDef(
            name="write_file",
            description="Write content to a file (overwrites). Args: path, content.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=file_tools,
        ),
        "search_files": ToolDef(
            name="search_files",
            description="Search file contents with regex. Args: pattern, path, file_glob, limit.",
            parameters={
                "type": "object",
                "properties": {
                    "pattern": {"type": "string"},
                    "path": {"type": "string", "default": "."},
                    "file_glob": {"type": "string"},
                    "limit": {"type": "integer", "default": 50},
                },
                "required": ["pattern"],
            },
            handler=file_tools,
        ),
        "patch_file": ToolDef(
            name="patch_file",
            description="Replace text in a file. Args: path, old_string, new_string, replace_all (default false).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "old_string": {"type": "string"},
                    "new_string": {"type": "string"},
                    "replace_all": {"type": "boolean", "default": False},
                },
                "required": ["path", "old_string", "new_string"],
            },
            handler=file_tools,
        ),
    }


def _tools_to_openai_format(tools: dict[str, ToolDef]) -> list[dict]:
    """Convert tool registry to OpenAI function format."""
    result = []
    for name, tool in tools.items():
        result.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        })
    return result


# ═══════════════════════════════════════════════════════════
# Agent Loop
# ═══════════════════════════════════════════════════════════

class AgentLoop:
    """Core agent execution loop.

    Think → Act → Observe → Reflect → (retry/degrade/escalate)
    """

    MAX_RETRIES = 3
    MAX_STEPS = 20

    def __init__(self, config: AetherConfig, workdir: Path | None = None):
        self.config = config
        self.workdir = workdir or Path.cwd()
        self.llm = LLMClient(config)
        self.tools = _build_tool_registry(self.workdir)
        self._step_count = 0

    async def close(self) -> None:
        await self.llm.close()

    async def run(
        self,
        user_message: str,
        history: list[ChatMessage] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run the agent loop for a user message.

        Yields StreamEvent for each step: thinking, tool_call, tool_result, text_delta, done.
        """
        session_id = uuid.uuid4().hex[:12]
        task = Task(
            id=uuid.uuid4().hex[:12],
            session_id=session_id,
            goal=user_message[:200],
            status=TaskStatus.PLANNING,
        )

        messages: list[ChatMessage] = list(history) if history else []
        messages.append(ChatMessage(role="user", content=user_message))

        sys_prompt = system_prompt or "You are Aether, a helpful AI assistant with access to tools."

        # Build system prompt with tools
        tool_list = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.tools.items()
        )
        full_system = (
            f"{sys_prompt}\n\n"
            f"Available tools:\n{tool_list}\n\n"
            "When you need to use a tool, respond with a JSON function call.\n"
            "When you are done, respond normally without a function call."
        )

        tool_schemas = _tools_to_openai_format(self.tools)
        self._step_count = 0

        while self._step_count < self.MAX_STEPS:
            self._step_count += 1

            # ── ① PLAN: Call LLM with tool definitions ──
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="thinking",
                data={"step": self._step_count, "status": "planning"},
            )

            try:
                response = await self.llm._call_api(
                    self.llm.primary.provider,
                    self.llm.primary.model,
                    messages=[
                        {"role": "system", "content": full_system},
                        *[m.to_dict() for m in messages],
                    ],
                    stream=False,
                    temperature=self.llm.primary.temperature,
                    max_tokens=self.llm.primary.max_tokens,
                )
            except Exception as e:
                yield StreamEvent(
                    event_id=uuid.uuid4().hex,
                    type="error",
                    data={"message": f"LLM call failed: {e}"},
                )
                task.status = TaskStatus.FAILED
                task.error = str(e)
                break

            if response.status_code != 200:
                yield StreamEvent(
                    event_id=uuid.uuid4().hex,
                    type="error",
                    data={"message": f"API error: HTTP {response.status_code}"},
                )
                task.status = TaskStatus.FAILED
                break

            data = response.json()
            choice = data["choices"][0]
            msg = choice["message"]

            # ── Check for tool call ──
            tool_calls = msg.get("tool_calls")
            if tool_calls:
                # ── ③ EXECUTE: Run the tool ──
                for tc in tool_calls:
                    func = tc["function"]
                    tool_name = func["name"]
                    try:
                        tool_args = json.loads(func["arguments"])
                    except json.JSONDecodeError:
                        tool_args = {}

                    yield StreamEvent(
                        event_id=uuid.uuid4().hex,
                        type="tool_call",
                        data={"name": tool_name, "arguments": tool_args},
                    )

                    tool = self.tools.get(tool_name)
                    if not tool:
                        result = {"error": f"Unknown tool: {tool_name}"}
                    else:
                        try:
                            handler = tool.handler
                            # Map tool_name to handler method
                            if tool_name == "terminal":
                                res = await handler.execute(**tool_args)
                                result = {"output": res.stdout, "exit_code": res.exit_code}
                                if res.stderr:
                                    result["stderr"] = res.stderr
                            elif tool_name == "read_file":
                                res = handler.read(**tool_args)
                                result = {"content": res.content, "total_lines": res.total_lines}
                            elif tool_name == "write_file":
                                res = handler.write(**tool_args)
                                result = {"path": res.path, "bytes_written": res.bytes_written}
                            elif tool_name == "search_files":
                                res = handler.search(**tool_args)
                                result = {"matches": res.matches, "total_matches": res.total_matches}
                            elif tool_name == "patch_file":
                                res = handler.patch(**tool_args)
                                result = {"path": res.path, "bytes_written": res.bytes_written}
                            else:
                                result = {"error": f"No handler for: {tool_name}"}
                        except Exception as e:
                            result = {"error": str(e)}

                    # ── ④ OBSERVE: Record result ──
                    yield StreamEvent(
                        event_id=uuid.uuid4().hex,
                        type="tool_result",
                        data={"name": tool_name, "result": result},
                    )

                    # Add tool call + result to messages
                    messages.append(ChatMessage(
                        role="assistant",
                        content="",
                        tool_calls=[{
                            "id": tc["id"],
                            "type": "function",
                            "function": {"name": tool_name, "arguments": func["arguments"]},
                        }],
                    ))
                    messages.append(ChatMessage(
                        role="tool",
                        content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=tc["id"],
                    ))

                # Continue loop to let LLM process tool results
                continue

            # ── No tool call: final text response ──
            content = msg.get("content", "")

            # Emit text as stream event
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="text_delta",
                data={"content": content},
            )
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="text_done",
                data={"content": content},
            )

            messages.append(ChatMessage(role="assistant", content=content))
            task.status = TaskStatus.COMPLETED
            task.artifacts.append(Artifact(type="text", content=content))
            break

        else:
            # Max steps reached
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="error",
                data={"message": f"Max steps ({self.MAX_STEPS}) reached"},
            )
            task.status = TaskStatus.FAILED

        # ── Done ──
        yield StreamEvent(
            event_id=uuid.uuid4().hex,
            type="done",
            data={
                "task_id": task.id,
                "status": task.status.value,
                "steps": self._step_count,
            },
        )
