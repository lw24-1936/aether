"""Audit logger — structured JSON audit trail for all tool executions.

Records: who, what, when, result, and security context.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import structlog

from aether.platform import get_data_dir


# ═══════════════════════════════════════════════════════════
# Audit event types
# ═══════════════════════════════════════════════════════════

@dataclass
class AuditEvent:
    """A single audit log entry."""
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: Literal[
        "tool_execute",
        "tool_denied",
        "permission_granted",
        "permission_denied",
        "sandbox_execute",
        "memory_write",
        "memory_read",
        "skill_loaded",
        "session_start",
        "session_end",
        "error",
    ] = "tool_execute"
    user_id: str = "default"
    session_id: str = ""
    details: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    duration_ms: float = 0.0


# ═══════════════════════════════════════════════════════════
# Audit logger
# ═══════════════════════════════════════════════════════════

class AuditLogger:
    """Structured audit logger for security and compliance.

    Writes JSON-lines to a log file, with optional console output.
    PII/secret values are automatically redacted.
    """

    SENSITIVE_KEYS = {
        "api_key", "password", "secret", "token", "auth",
        "credential", "private_key",
    }

    def __init__(
        self,
        log_dir: str | Path | None = None,
        console: bool = False,
        max_files: int = 7,
    ):
        if log_dir is None:
            log_dir = get_data_dir() / "audit"
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.console = console
        self.max_files = max_files
        self._logger = structlog.get_logger("aether.audit")
        self._current_file: str | None = None

    def _get_log_path(self) -> Path:
        """Get today's log file path, rotating if needed."""
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = self.log_dir / f"audit-{today}.jsonl"
        self._current_file = str(path)
        self._rotate()
        return path

    def _rotate(self) -> None:
        """Remove old log files beyond max_files."""
        files = sorted(self.log_dir.glob("audit-*.jsonl"))
        while len(files) > self.max_files:
            files[0].unlink()
            files = files[1:]

    def _redact(self, data: dict[str, Any]) -> dict[str, Any]:
        """Redact sensitive values from a dict."""
        redacted = {}
        for k, v in data.items():
            if any(s in k.lower() for s in self.SENSITIVE_KEYS):
                redacted[k] = "***REDACTED***"
            elif isinstance(v, dict):
                redacted[k] = self._redact(v)
            elif isinstance(v, str) and len(v) > 100:
                redacted[k] = v[:100] + "..."
            else:
                redacted[k] = v
        return redacted

    def log(self, event: AuditEvent) -> None:
        """Write an audit event to the log."""
        entry = {
            "id": event.id,
            "ts": event.timestamp,
            "type": event.event_type,
            "user": event.user_id,
            "session": event.session_id,
            "success": event.success,
            "duration_ms": round(event.duration_ms, 2),
            "details": self._redact(event.details),
        }

        # Write to file
        path = self._get_log_path()
        with open(path, "a") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

        # Console output (debug)
        if self.console:
            self._logger.info(event.event_type, **entry)

    # ═══════════════════════════════════════════════════════
    # Convenience methods
    # ═══════════════════════════════════════════════════════

    def tool_executed(
        self,
        tool_name: str,
        args: dict[str, Any],
        result: Any,
        duration_ms: float,
        session_id: str = "",
        success: bool = True,
    ) -> None:
        self.log(AuditEvent(
            event_type="tool_execute",
            session_id=session_id,
            details={"tool": tool_name, "args": args, "result_preview": str(result)[:200]},
            success=success,
            duration_ms=duration_ms,
        ))

    def tool_denied(
        self,
        tool_name: str,
        reason: str,
        session_id: str = "",
    ) -> None:
        self.log(AuditEvent(
            event_type="tool_denied",
            session_id=session_id,
            details={"tool": tool_name, "reason": reason},
            success=False,
        ))

    def permission_granted(
        self,
        tool_name: str,
        level: int,
        session_id: str = "",
    ) -> None:
        self.log(AuditEvent(
            event_type="permission_granted",
            session_id=session_id,
            details={"tool": tool_name, "level": level},
        ))

    def permission_denied(
        self,
        tool_name: str,
        level: int,
        reason: str,
        session_id: str = "",
    ) -> None:
        self.log(AuditEvent(
            event_type="permission_denied",
            session_id=session_id,
            details={"tool": tool_name, "level": level, "reason": reason},
            success=False,
        ))

    def sandbox_executed(
        self,
        mode: str,
        command: str,
        exit_code: int,
        duration_ms: float,
        session_id: str = "",
    ) -> None:
        self.log(AuditEvent(
            event_type="sandbox_execute",
            session_id=session_id,
            details={"mode": mode, "command": command[:200], "exit_code": exit_code},
            success=exit_code == 0,
            duration_ms=duration_ms,
        ))

    def memory_written(
        self,
        target: str,
        content_preview: str,
        session_id: str = "",
    ) -> None:
        self.log(AuditEvent(
            event_type="memory_write",
            session_id=session_id,
            details={"target": target, "content": content_preview[:200]},
        ))

    def error(
        self,
        error_type: str,
        message: str,
        session_id: str = "",
    ) -> None:
        self.log(AuditEvent(
            event_type="error",
            session_id=session_id,
            details={"error_type": error_type, "message": message[:500]},
            success=False,
        ))

    # ═══════════════════════════════════════════════════════
    # Query
    # ═══════════════════════════════════════════════════════

    def query_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Read recent audit entries."""
        path = self._get_log_path()
        if not path.exists():
            return []
        entries = []
        with open(path) as f:
            for line in f:
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        return entries[-limit:]

    def query_by_type(self, event_type: str, limit: int = 50) -> list[dict[str, Any]]:
        """Query audit entries by event type."""
        entries = self.query_recent(limit * 2)
        return [e for e in entries if e.get("type") == event_type][-limit:]
