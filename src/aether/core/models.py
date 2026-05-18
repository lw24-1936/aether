"""Core data models for Aether Agent Framework.

All Pydantic models follow the design spec (DESIGN-v2.md section 4.9).
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Literal

from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════
# Role & Message
# ═══════════════════════════════════════════════════════════

class Role(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ContentBlock(BaseModel):
    type: Literal["text", "image", "tool_use", "tool_result"]
    text: str | None = None
    image_url: str | None = None
    tool_use: dict[str, Any] | None = None


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


class Message(BaseModel):
    id: str
    session_id: str
    role: Role
    content: str | list[ContentBlock]
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# Session
# ═══════════════════════════════════════════════════════════

class Session(BaseModel):
    id: str
    user_id: str = "default"
    title: str = "New Session"
    messages: list[Message] = Field(default_factory=list)
    working_dir: str = "."
    model: str = "gpt-4o"
    provider: str = "openai"
    platform: Literal["windows", "linux", "darwin"] = "linux"
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# Memory
# ═══════════════════════════════════════════════════════════

class MemoryTarget(str, Enum):
    USER = "user"
    PROJECT = "project"
    ENVIRONMENT = "environment"


class Memory(BaseModel):
    id: str
    user_id: str = "default"
    target: MemoryTarget = MemoryTarget.USER
    content: str
    tags: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    access_count: int = 0
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    expires_at: datetime | None = None


class MemoryConflictType(str, Enum):
    CONTRADICTION = "contradiction"
    OVERLAP = "overlap"
    OUTDATED = "outdated"


class MemoryResolution(str, Enum):
    KEEP_BOTH = "keep_both"
    KEEP_NEWER = "keep_newer"
    ASK_USER = "ask_user"
    MERGE = "merge"


class MemoryConflict(BaseModel):
    memory_a_id: str
    memory_b_id: str
    conflict_type: MemoryConflictType
    resolution: MemoryResolution = MemoryResolution.ASK_USER


# ═══════════════════════════════════════════════════════════
# Skill
# ═══════════════════════════════════════════════════════════

class SkillMeta(BaseModel):
    name: str
    description: str = ""
    triggers: list[str] = Field(default_factory=list)
    tools_required: list[str] = Field(default_factory=list)
    version: str = "0.1.0"
    category: str = "general"
    author: str | None = None


class Skill(BaseModel):
    meta: SkillMeta
    path: str
    content: str = ""
    linked_files: dict[str, str] = Field(default_factory=dict)


# ═══════════════════════════════════════════════════════════
# Task & Artifact
# ═══════════════════════════════════════════════════════════

class TaskStatus(str, Enum):
    PENDING = "pending"
    PLANNING = "planning"
    EXECUTING = "executing"
    OBSERVING = "observing"
    REFLECTING = "reflecting"
    RETRYING = "retrying"
    DEGRADING = "degrading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class Step(BaseModel):
    id: str
    content: str
    status: TaskStatus = TaskStatus.PENDING
    depends_on: list[str] = Field(default_factory=list)


class Artifact(BaseModel):
    type: Literal["file", "code", "url", "text", "image"]
    path: str | None = None
    content: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Task(BaseModel):
    id: str
    session_id: str
    parent_task_id: str | None = None
    goal: str
    plan: list[Step] | None = None
    status: TaskStatus = TaskStatus.PENDING
    retry_count: int = 0
    max_retries: int = 3
    artifacts: list[Artifact] = Field(default_factory=list)
    error: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None


# ═══════════════════════════════════════════════════════════
# SubAgent
# ═══════════════════════════════════════════════════════════

class SubAgent(BaseModel):
    id: str
    parent_task_id: str
    role: Literal["leaf", "orchestrator"] = "leaf"
    goal: str
    context: str = ""
    toolsets: list[str] = Field(default_factory=list)
    status: TaskStatus = TaskStatus.PENDING
    result_summary: str | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════
# CronJob
# ═══════════════════════════════════════════════════════════

class CronJob(BaseModel):
    id: str
    name: str
    schedule: str
    type: Literal["agent", "script", "chain"] = "agent"
    prompt: str | None = None
    script_path: str | None = None
    upstream_jobs: list[str] = Field(default_factory=list)
    enabled: bool = True
    deliver_to: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_run: datetime | None = None


# ═══════════════════════════════════════════════════════════
# Permission
# ═══════════════════════════════════════════════════════════

class PermissionLevel(IntEnum):
    READ_ONLY = 0
    NETWORK = 1
    WRITE = 2
    EXECUTE = 3
    SYSTEM = 4
    EXTERNAL = 5


class PermissionRequest(BaseModel):
    id: str
    tool_name: str
    level: PermissionLevel
    args_preview: str
    status: Literal["pending", "approved", "denied", "timed_out"] = "pending"
    approved_for_session: bool = False
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    resolved_at: datetime | None = None


# ═══════════════════════════════════════════════════════════
# Stream Events
# ═══════════════════════════════════════════════════════════

class StreamEvent(BaseModel):
    event_id: str
    type: Literal[
        "thinking",
        "tool_call",
        "tool_result",
        "text_delta",
        "text_done",
        "artifact",
        "permission_request",
        "permission_resolved",
        "status_change",
        "error",
        "done",
    ]
    data: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ═══════════════════════════════════════════════════════════
# Attachment
# ═══════════════════════════════════════════════════════════

class Attachment(BaseModel):
    path: str
    mime_type: str = "application/octet-stream"
    description: str = ""
