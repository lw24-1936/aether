"""Agent Loop v2 — with circuit breaker, retry/degrade/escalate, security.

Planner → [Security Check] → Executor → Observer → Reflector
  ↑                                                    │
  └──── retry ──── degrade ──── escalate ──────────────┘
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from aether.core.config import AetherConfig
from aether.core.llm import LLMClient, ChatMessage
from aether.core.models import Artifact, StreamEvent, Task, TaskStatus
from aether.core.circuit_breaker import BreakerRegistry
from aether.core.security import (
    ApprovalManager,
    PermissionLevel,
    is_command_blacklisted,
)
from aether.memory.manager import MemoryManager
from aether.skills.manager import SkillManager
from aether.core.sandbox import Sandbox, SandboxConfig as SandboxCfg
from aether.core.audit import AuditLogger
from aether.tools.terminal import TerminalTool
from aether.tools.file import FileTools


# ═══════════════════════════════════════════════════════════
# Enhanced step result
# ═══════════════════════════════════════════════════════════

class StepOutcome(str, Enum):
    SUCCESS = "success"
    RETRY = "retry"
    DEGRADE = "degrade"
    ESCALATE = "escalate"
    FATAL = "fatal"


@dataclass
class ToolDef:
    name: str
    description: str
    parameters: dict
    handler: Any
    permission_level: PermissionLevel


def _build_tool_registry(workdir: Path) -> dict[str, ToolDef]:
    terminal = TerminalTool(workdir=workdir)
    file_tools = FileTools(workdir=workdir)

    return {
        "terminal": ToolDef(
            name="terminal",
            description="Execute shell commands. Returns {output, exit_code}.",
            parameters={
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer", "default": 30},
                },
                "required": ["command"],
            },
            handler=terminal,
            permission_level=PermissionLevel.EXECUTE,
        ),
        "read_file": ToolDef(
            name="read_file",
            description="Read a file with line numbers.",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer", "default": 1},
                    "limit": {"type": "integer", "default": 500},
                },
                "required": ["path"],
            },
            handler=file_tools,
            permission_level=PermissionLevel.READ_ONLY,
        ),
        "write_file": ToolDef(
            name="write_file",
            description="Write content to a file (overwrites).",
            parameters={
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
            handler=file_tools,
            permission_level=PermissionLevel.WRITE,
        ),
        "search_files": ToolDef(
            name="search_files",
            description="Search file contents with regex.",
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
            permission_level=PermissionLevel.READ_ONLY,
        ),
        "patch_file": ToolDef(
            name="patch_file",
            description="Find-and-replace text in a file.",
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
            permission_level=PermissionLevel.WRITE,
        ),
    }


def _tools_to_openai_format(tools: dict[str, ToolDef]) -> list[dict]:
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
# Agent Loop v2
# ═══════════════════════════════════════════════════════════

class AgentLoop:
    """Enhanced agent loop with circuit breaker, security, and recovery."""

    MAX_RETRIES = 3
    MAX_STEPS = 20

    def __init__(self, config: AetherConfig, workdir: Path | None = None):
        self.config = config
        self.workdir = workdir or Path.cwd()
        self.llm = LLMClient(config)
        self.tools = _build_tool_registry(self.workdir)
        self.breakers = BreakerRegistry()
        self.approval = ApprovalManager(config.security)
        self.memory = MemoryManager(max_entries=config.memory.max_entries)
        self.skills = SkillManager()
        self.skills.discover()
        self.sandbox = Sandbox(SandboxCfg())
        self.audit = AuditLogger()
        self._step_count = 0
        self._pending_approvals: dict[str, Any] = {}

    async def close(self) -> None:
        await self.llm.close()
        self.memory.close()

    def get_pending_approvals(self) -> list[dict]:
        """Get pending approval requests (for CLI polling)."""
        return [
            {
                "id": req.id,
                "tool": req.tool_name,
                "level": req.level.name,
                "args": req.args_preview,
                "risk": req.risk_description,
            }
            for req in self.approval._pending.values()
        ]

    def handle_approval(self, request_id: str, decision: str) -> str:
        """Handle user's approval decision. Returns 'approved'/'denied'/'timed_out'."""
        if decision == "approve":
            self.approval.approve(request_id, session_wide=False)
        elif decision == "deny":
            self.approval.deny(request_id)
        elif decision == "approve_session":
            self.approval.approve(request_id, session_wide=True)
        return decision

    async def run(
        self,
        user_message: str,
        history: list[ChatMessage] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Run the agent loop with full recovery support."""
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

        # ── Inject relevant memories into system prompt ──
        relevant_memories = self.memory.recall(user_message, limit=5)
        memory_context = ""
        if relevant_memories:
            memory_lines = [f"- {m.content}" for m in relevant_memories]
            memory_context = (
                "\n\nRelevant memories:\n"
                + "\n".join(memory_lines)
                + "\n\nUse these to personalize your response."
            )

        # ── Match and inject relevant skills ──
        matched_skills = self.skills.match_triggers(user_message)
        skill_context = ""
        if matched_skills:
            skill_lines = []
            for s in matched_skills[:3]:  # Top 3
                body_preview = s.body[:800]  # Limit skill content
                skill_lines.append(
                    f"### {s.meta.name}: {s.meta.description}\n{body_preview}"
                )
            skill_context = (
                "\n\nRelevant skills loaded:\n"
                + "\n---\n".join(skill_lines)
                + "\n\nFollow these skill instructions when applicable."
            )
        tool_list = "\n".join(
            f"- {name}: {tool.description}" for name, tool in self.tools.items()
        )
        full_system = (
            f"{sys_prompt}{memory_context}{skill_context}\n\n"
            f"Available tools:\n{tool_list}\n\n"
            "When you need a tool, respond with a JSON function call.\n"
            "When done, respond normally."
        )

        tool_schemas = _tools_to_openai_format(self.tools)
        self._step_count = 0
        consecutive_failures = 0

        while self._step_count < self.MAX_STEPS:
            self._step_count += 1

            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="thinking",
                data={"step": self._step_count, "status": "planning"},
            )

            # ── Check timeouts ──
            timed_out = self.approval.check_timeouts()
            for req in timed_out:
                yield StreamEvent(
                    event_id=uuid.uuid4().hex,
                    type="permission_resolved",
                    data={"id": req.id, "status": "timed_out"},
                )

            # ── Call LLM ──
            try:
                breaker = self.breakers.get("llm")
                if not breaker.before_call():
                    yield StreamEvent(
                        event_id=uuid.uuid4().hex,
                        type="error",
                        data={"message": "LLM circuit breaker open — cooling down"},
                    )
                    # Degrade: return simple error message
                    messages.append(ChatMessage(
                        role="assistant",
                        content="I'm temporarily unable to process your request. Please try again in a moment.",
                    ))
                    task.status = TaskStatus.DEGRADING
                    break

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
                    tools=tool_schemas,
                )
            except Exception as e:
                breaker.on_failure(str(e))
                consecutive_failures += 1
                yield StreamEvent(
                    event_id=uuid.uuid4().hex,
                    type="error",
                    data={"message": f"LLM call failed: {e}"},
                )
                outcome = self._decide_outcome(consecutive_failures)
                if outcome == StepOutcome.RETRY and self._step_count < self.MAX_STEPS:
                    task.status = TaskStatus.RETRYING
                    continue
                elif outcome == StepOutcome.DEGRADE:
                    task.status = TaskStatus.DEGRADING
                    messages.append(ChatMessage(
                        role="assistant",
                        content="I encountered an error. Please try again or simplify your request.",
                    ))
                    break
                else:
                    task.status = TaskStatus.FAILED
                    break

            if response.status_code != 200:
                breaker.on_failure(f"HTTP {response.status_code}")
                consecutive_failures += 1
                outcome = self._decide_outcome(consecutive_failures)
                if outcome == StepOutcome.RETRY:
                    task.status = TaskStatus.RETRYING
                    continue
                yield StreamEvent(
                    event_id=uuid.uuid4().hex,
                    type="error",
                    data={"message": f"API error: HTTP {response.status_code}"},
                )
                task.status = TaskStatus.FAILED
                break

            breaker.on_success()
            consecutive_failures = 0  # Reset on success

            data = response.json()
            choice = data["choices"][0]
            msg = choice["message"]
            tool_calls = msg.get("tool_calls")

            if tool_calls:
                # ── Process tool calls ──
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
                        yield StreamEvent(
                            event_id=uuid.uuid4().hex,
                            type="tool_result",
                            data={"name": tool_name, "result": result},
                        )
                        messages.append(ChatMessage(
                            role="assistant", content="",
                            tool_calls=[{"id": tc["id"], "type": "function",
                                         "function": {"name": tool_name, "arguments": func["arguments"]}}],
                        ))
                        messages.append(ChatMessage(
                            role="tool", content=json.dumps(result, ensure_ascii=False),
                            tool_call_id=tc["id"],
                        ))
                        continue

                    # ── Security check ──
                    cmd = tool_args.get("command", "")
                    if tool_name == "terminal" and is_command_blacklisted(cmd):
                        result = {"error": f"Command blocked by security policy: {cmd[:80]}"}
                        yield StreamEvent(
                            event_id=uuid.uuid4().hex,
                            type="tool_result",
                            data={"name": tool_name, "result": result},
                        )
                        messages.append(ChatMessage(
                            role="assistant", content="",
                            tool_calls=[{"id": tc["id"], "type": "function",
                                         "function": {"name": tool_name, "arguments": func["arguments"]}}],
                        ))
                        messages.append(ChatMessage(
                            role="tool", content=json.dumps(result, ensure_ascii=False),
                            tool_call_id=tc["id"],
                        ))
                        continue

                    # ── Approval check ──
                    if self.approval.needs_approval(tool_name, tool.permission_level):
                        req = self.approval.create_request(
                            tool_name, tool.permission_level,
                            str(tool_args)[:200],
                        )
                        yield StreamEvent(
                            event_id=uuid.uuid4().hex,
                            type="permission_request",
                            data={
                                "id": req.id,
                                "tool": tool_name,
                                "level": req.level.name,
                                "args": req.args_preview,
                                "risk": req.risk_description,
                            },
                        )
                        # Store for async approval handling
                        self._pending_approvals[req.id] = (tc, tool, tool_args, messages)
                        # Return control to CLI for user input
                        yield StreamEvent(
                            event_id=uuid.uuid4().hex,
                            type="status_change",
                            data={"status": "awaiting_approval", "request_id": req.id},
                        )
                        return  # Wait for approval callback

                    # ── Execute tool ──
                    tool_breaker = self.breakers.get(tool_name)
                    if not tool_breaker.before_call():
                        result = {"error": f"Tool '{tool_name}' circuit breaker open"}
                    else:
                        result = await self._execute_tool(tool_name, tool, tool_args, tool_breaker)

                    yield StreamEvent(
                        event_id=uuid.uuid4().hex,
                        type="tool_result",
                        data={"name": tool_name, "result": result},
                    )

                    messages.append(ChatMessage(
                        role="assistant", content="",
                        tool_calls=[{"id": tc["id"], "type": "function",
                                     "function": {"name": tool_name, "arguments": func["arguments"]}}],
                    ))
                    messages.append(ChatMessage(
                        role="tool", content=json.dumps(result, ensure_ascii=False),
                        tool_call_id=tc["id"],
                    ))

                continue  # More tool processing

            # ── Final text response ──
            content = msg.get("content", "")
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
            break

        else:
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="error",
                data={"message": f"Max steps ({self.MAX_STEPS}) reached"},
            )
            task.status = TaskStatus.FAILED

        yield StreamEvent(
            event_id=uuid.uuid4().hex,
            type="done",
            data={"task_id": task.id, "status": task.status.value, "steps": self._step_count},
        )

    async def resume_after_approval(
        self,
        request_id: str,
        decision: str,
        messages: list[ChatMessage],
    ) -> AsyncIterator[StreamEvent]:
        """Resume execution after user handles an approval request."""
        if request_id not in self._pending_approvals:
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="error",
                data={"message": f"Unknown approval request: {request_id}"},
            )
            return

        tc, tool, tool_args, msg_list = self._pending_approvals.pop(request_id)

        if decision == "deny" or decision == "timed_out":
            result = {"error": f"Tool '{tool.name}' was denied by user"}
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="tool_result",
                data={"name": tool.name, "result": result},
            )
            msg_list.append(ChatMessage(
                role="assistant", content="",
                tool_calls=[{"id": tc["id"], "type": "function",
                             "function": {"name": tool.name, "arguments": tc["function"]["arguments"]}}],
            ))
            msg_list.append(ChatMessage(
                role="tool", content=json.dumps(result, ensure_ascii=False),
                tool_call_id=tc["id"],
            ))
        else:
            # Approved — execute
            tool_breaker = self.breakers.get(tool.name)
            if not tool_breaker.before_call():
                result = {"error": f"Tool '{tool.name}' circuit breaker open"}
            else:
                result = await self._execute_tool(tool.name, tool, tool_args, tool_breaker)
            yield StreamEvent(
                event_id=uuid.uuid4().hex,
                type="tool_result",
                data={"name": tool.name, "result": result},
            )
            msg_list.append(ChatMessage(
                role="assistant", content="",
                tool_calls=[{"id": tc["id"], "type": "function",
                             "function": {"name": tool.name, "arguments": tc["function"]["arguments"]}}],
            ))
            msg_list.append(ChatMessage(
                role="tool", content=json.dumps(result, ensure_ascii=False),
                tool_call_id=tc["id"],
            ))

        # Continue the loop with updated messages
        async for event in self.run(
            user_message="",  # continuation
            history=msg_list,
        ):
            yield event

    async def _execute_tool(
        self, tool_name: str, tool: ToolDef, args: dict, breaker
    ) -> dict:
        """Execute a tool with error handling."""
        try:
            if tool_name == "terminal":
                res = await tool.handler.execute(**args)
                result = {"output": res.stdout, "exit_code": res.exit_code}
                if res.stderr:
                    result["stderr"] = res.stderr[:500]
            elif tool_name == "read_file":
                res = tool.handler.read(**args)
                result = {"content": res.content, "total_lines": res.total_lines}
            elif tool_name == "write_file":
                res = tool.handler.write(**args)
                result = {"path": res.path, "bytes_written": res.bytes_written}
            elif tool_name == "search_files":
                res = tool.handler.search(**args)
                result = {"matches": res.matches, "total_matches": res.total_matches}
            elif tool_name == "patch_file":
                res = tool.handler.patch(**args)
                result = {"path": res.path, "bytes_written": res.bytes_written}
            else:
                result = {"error": f"No handler: {tool_name}"}
            breaker.on_success()
            return result
        except Exception as e:
            breaker.on_failure(str(e))
            return {"error": str(e)}

    def _decide_outcome(self, consecutive_failures: int) -> StepOutcome:
        """Decide: retry, degrade, or escalate."""
        if consecutive_failures < self.MAX_RETRIES:
            return StepOutcome.RETRY
        elif consecutive_failures < self.MAX_RETRIES + 2:
            return StepOutcome.DEGRADE
        else:
            return StepOutcome.ESCALATE
