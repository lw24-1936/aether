"""Security approval system for tool execution.

Permission levels 0-5 with auto-approve, whitelist, session-remember,
and timeout-based default-deny.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Literal

from aether.core.config import SecurityConfig


class PermissionLevel(IntEnum):
    READ_ONLY = 0    # Read files, search
    NETWORK = 1      # HTTP requests
    WRITE = 2        # Write files
    EXECUTE = 3      # Run commands
    SYSTEM = 4       # Install packages, modify config
    EXTERNAL = 5     # Access external agents/services


# Risky command patterns (blacklist) — checked with substring matching
COMMAND_BLACKLIST = [
    "rm -rf /",
    "rm -rf ~",
    "dd if=/dev/zero",
    "mkfs.",
    ":(){ :|:& };:",
    "| sh",
    "| bash",
    "chmod 777 /",
    "> /dev/sda",
]


def is_command_blacklisted(command: str) -> bool:
    """Check if a command matches any blacklist pattern."""
    cmd_lower = command.lower().strip()
    for pattern in COMMAND_BLACKLIST:
        if pattern.lower() in cmd_lower:
            return True
    # Extra check: curl/wget piped to shell
    if ("curl" in cmd_lower or "wget" in cmd_lower) and ("| sh" in cmd_lower or "| bash" in cmd_lower):
        return True
    return False


@dataclass
class ApprovalRequest:
    """A pending approval request."""
    id: str
    tool_name: str
    level: PermissionLevel
    args_preview: str
    risk_description: str
    status: Literal["pending", "approved", "denied", "timed_out"] = "pending"
    approved_for_session: bool = False
    requested_at: float = field(default_factory=time.monotonic)
    resolved_at: float | None = None


class ApprovalManager:
    """Manages tool execution approvals.

    Flow:
      1. Check permission level → auto-approve Level 0-1
      2. Check whitelist → auto-approve
      3. Check session-approved → auto-approve
      4. Show approval prompt → user decides (30s timeout = deny)
    """

    def __init__(self, config: SecurityConfig):
        self.config = config
        self.auto_approve_level = config.auto_approve_level
        self.timeout_seconds = config.approval_timeout_seconds
        self.whitelist: set[str] = set()  # tool_name → auto-approve
        self.session_approved: set[str] = set()  # tool_name approved this session
        self._pending: dict[str, ApprovalRequest] = {}
        self._history: list[ApprovalRequest] = []

    def needs_approval(self, tool_name: str, level: PermissionLevel) -> bool:
        """Check if a tool call needs user approval."""
        if level.value < self.auto_approve_level:
            return False
        if tool_name in self.whitelist:
            return False
        if tool_name in self.session_approved:
            return False
        return True

    def create_request(
        self, tool_name: str, level: PermissionLevel, args_preview: str
    ) -> ApprovalRequest:
        """Create a new approval request."""
        risk = self._describe_risk(tool_name, level)
        req = ApprovalRequest(
            id=uuid.uuid4().hex[:8],
            tool_name=tool_name,
            level=level,
            args_preview=args_preview,
            risk_description=risk,
        )
        self._pending[req.id] = req
        return req

    def approve(self, request_id: str, session_wide: bool = False) -> ApprovalRequest:
        """Approve a pending request."""
        req = self._pending.pop(request_id)
        req.status = "approved"
        req.resolved_at = time.monotonic()
        req.approved_for_session = session_wide
        if session_wide:
            self.session_approved.add(req.tool_name)
        self._history.append(req)
        return req

    def deny(self, request_id: str) -> ApprovalRequest:
        """Deny a pending request."""
        req = self._pending.pop(request_id)
        req.status = "denied"
        req.resolved_at = time.monotonic()
        self._history.append(req)
        return req

    def check_timeouts(self) -> list[ApprovalRequest]:
        """Check for timed-out requests. Returns list of timed-out requests."""
        now = time.monotonic()
        timed_out = []
        for req in list(self._pending.values()):
            if now - req.requested_at > self.timeout_seconds:
                req.status = "timed_out"
                req.resolved_at = now
                self._history.append(req)
                timed_out.append(req)
                del self._pending[req.id]
        return timed_out

    def add_whitelist(self, *tool_names: str) -> None:
        """Add tools to the whitelist."""
        self.whitelist.update(tool_names)

    def _describe_risk(self, tool_name: str, level: PermissionLevel) -> str:
        """Generate a human-readable risk description."""
        descriptions = {
            PermissionLevel.READ_ONLY: "Reading data (no risk)",
            PermissionLevel.NETWORK: "Making network requests",
            PermissionLevel.WRITE: "Writing to filesystem",
            PermissionLevel.EXECUTE: "Executing commands",
            PermissionLevel.SYSTEM: "Modifying system configuration",
            PermissionLevel.EXTERNAL: "Interacting with external services",
        }
        base = descriptions.get(level, "Unknown risk level")

        if tool_name == "terminal" and level == PermissionLevel.EXECUTE:
            base += " — will run shell commands"
        elif tool_name == "write_file":
            base += " — will modify or create files"
        elif tool_name == "patch_file":
            base += " — will modify existing files"

        return base

    def _get_level(self, tool_name: str) -> PermissionLevel:
        """Map tool name to permission level."""
        level_map = {
            "read_file": PermissionLevel.READ_ONLY,
            "search_files": PermissionLevel.READ_ONLY,
            "write_file": PermissionLevel.WRITE,
            "patch_file": PermissionLevel.WRITE,
            "terminal": PermissionLevel.EXECUTE,
        }
        return level_map.get(tool_name, PermissionLevel.EXECUTE)
