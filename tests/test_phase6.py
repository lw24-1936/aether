"""Phase 6 tests: Sandbox execution and audit logging (cross-platform)."""

import sys
import tempfile
from pathlib import Path

import pytest

from aether.core.sandbox import (
    Sandbox,
    ProcessSandbox,
    DockerSandbox,
    SandboxConfig,
    SandboxMode,
    SandboxResult,
)
from aether.core.audit import AuditLogger, AuditEvent


IS_WINDOWS = sys.platform == "win32"


class TestSandboxConfig:
    """Sandbox configuration tests."""

    def test_default_config(self):
        cfg = SandboxConfig()
        assert cfg.mode == SandboxMode.AUTO
        assert cfg.timeout_seconds == 300
        assert cfg.memory_limit_mb == 512

    def test_docker_mode(self):
        cfg = SandboxConfig(mode=SandboxMode.DOCKER)
        assert cfg.mode == SandboxMode.DOCKER

    def test_process_mode(self):
        cfg = SandboxConfig(mode=SandboxMode.PROCESS)
        assert cfg.mode == SandboxMode.PROCESS


class TestProcessSandbox:
    """Process sandbox tests."""

    @pytest.mark.asyncio
    async def test_simple_command(self):
        sandbox = ProcessSandbox(SandboxConfig(timeout_seconds=5))
        cmd = "echo hello" if not IS_WINDOWS else "echo hello"
        result = await sandbox.execute(cmd)
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_failing_command(self):
        sandbox = ProcessSandbox(SandboxConfig(timeout_seconds=5))
        cmd = "exit 42" if not IS_WINDOWS else "exit 42"
        result = await sandbox.execute(cmd)
        # Windows cmd.exe: exit code may differ
        if not IS_WINDOWS:
            assert result.exit_code == 42
        else:
            assert result.exit_code != 0 or result.stderr  # Some error expected

    @pytest.mark.asyncio
    async def test_invalid_command(self):
        sandbox = ProcessSandbox(SandboxConfig(timeout_seconds=5))
        result = await sandbox.execute("nonexistent_command_xyz_12345")
        assert result.exit_code != 0


class TestUnifiedSandbox:
    """Unified sandbox tests (auto-selection)."""

    def test_auto_mode_selection(self):
        sandbox = Sandbox(SandboxConfig(mode=SandboxMode.AUTO))
        assert sandbox.mode in (SandboxMode.DOCKER, SandboxMode.PROCESS)

    def test_force_process_mode(self):
        sandbox = Sandbox(SandboxConfig(mode=SandboxMode.PROCESS))
        assert sandbox.mode == SandboxMode.PROCESS

    @pytest.mark.asyncio
    async def test_execute_in_process_mode(self):
        sandbox = Sandbox(SandboxConfig(mode=SandboxMode.PROCESS, timeout_seconds=5))
        result = await sandbox.execute("echo hello")
        assert result.exit_code == 0
        assert "hello" in result.stdout


class TestAuditLogger:
    """Audit logger tests."""

    @pytest.fixture
    def audit_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def test_log_event(self, audit_dir):
        logger = AuditLogger(log_dir=audit_dir)
        event = AuditEvent(
            event_type="tool_execute",
            details={"tool": "test", "args": {"x": 1}},
            session_id="s1",
        )
        logger.log(event)

        entries = logger.query_recent()
        assert len(entries) >= 1
        assert entries[-1]["type"] == "tool_execute"
        assert entries[-1]["details"]["tool"] == "test"

    def test_convenience_methods(self, audit_dir):
        logger = AuditLogger(log_dir=audit_dir)
        logger.tool_executed("read_file", {"path": "/tmp"}, "content", 10.0, session_id="s1")
        logger.tool_denied("terminal", "blacklisted", session_id="s1")
        logger.permission_granted("write_file", 2, session_id="s1")

        entries = logger.query_recent()
        types = [e["type"] for e in entries]
        assert "tool_execute" in types
        assert "tool_denied" in types
        assert "permission_granted" in types

    def test_redact_sensitive(self, audit_dir):
        logger = AuditLogger(log_dir=audit_dir)
        event = AuditEvent(
            event_type="tool_execute",
            details={
                "tool": "api_call",
                "api_key": "secret-123",
                "password": "hunter2",
                "safe_field": "visible",
            },
        )
        logger.log(event)

        entries = logger.query_recent()
        details = entries[-1]["details"]
        assert details["api_key"] == "***REDACTED***"
        assert details["password"] == "***REDACTED***"
        assert details["safe_field"] == "visible"

    def test_query_by_type(self, audit_dir):
        logger = AuditLogger(log_dir=audit_dir)
        logger.tool_executed("t1", {}, "ok", 1.0)
        logger.tool_executed("t2", {}, "ok", 1.0)
        logger.tool_denied("t3", "reason")

        tools = logger.query_by_type("tool_execute")
        assert len(tools) == 2

        denied = logger.query_by_type("tool_denied")
        assert len(denied) == 1

        empty = logger.query_by_type("nonexistent")
        assert len(empty) == 0

    def test_rotation(self, audit_dir):
        logger = AuditLogger(log_dir=audit_dir, max_files=2)
        import json
        for day in range(5):
            path = Path(audit_dir) / f"audit-2026-05-{day+10:02d}.jsonl"
            path.write_text(json.dumps({"test": True}) + "\n")

        logger._rotate()
        remaining = list(Path(audit_dir).glob("audit-*.jsonl"))
        assert len(remaining) <= 2

    def test_error_logging(self, audit_dir):
        logger = AuditLogger(log_dir=audit_dir)
        logger.error("ConnectionError", "Failed to connect", session_id="s1")

        entries = logger.query_recent()
        assert entries[-1]["type"] == "error"
        assert not entries[-1]["success"]
