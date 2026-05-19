"""Integration tests for Aether Phase 0 prototype."""
import asyncio
from pathlib import Path

import pytest

from aether.core.models import (
    Message, Session, Memory, Task, Skill, CronJob,
    TaskStatus, StreamEvent, PermissionRequest, PermissionLevel,
)
from aether.core.config import AetherConfig
from aether.core.llm import LLMClient, ChatMessage, LLMResponse
from aether.core.loop import AgentLoop, _build_tool_registry, _tools_to_openai_format
from aether.platform import PlatformInfo, ShellExecutor, get_platform
from aether.tools.terminal import TerminalTool
from aether.tools.file import FileTools


class TestModels:
    """Core data model tests."""

    def test_message_creation(self):
        msg = Message(id="1", session_id="s1", role="user", content="hello")
        assert msg.role.value == "user"
        assert msg.content == "hello"
        assert msg.session_id == "s1"

    def test_session_creation(self):
        s = Session(id="s1", title="Test", working_dir="/tmp")
        assert s.platform in ("windows", "linux", "darwin")
        assert len(s.messages) == 0

    def test_memory_model(self):
        m = Memory(id="m1", content="User prefers concise answers")
        assert m.importance == 0.5
        assert m.access_count == 0

    def test_task_status_flow(self):
        task = Task(id="t1", session_id="s1", goal="test")
        assert task.status == TaskStatus.PENDING
        task.status = TaskStatus.PLANNING
        assert task.status == TaskStatus.PLANNING
        task.status = TaskStatus.COMPLETED

    def test_permission_levels(self):
        assert PermissionLevel.READ_ONLY < PermissionLevel.EXECUTE
        assert PermissionLevel.EXTERNAL > PermissionLevel.SYSTEM

    def test_stream_event(self):
        evt = StreamEvent(
            event_id="e1",
            type="text_delta",
            data={"content": "hello"},
        )
        assert evt.type == "text_delta"
        assert evt.data["content"] == "hello"


class TestConfig:
    """Configuration system tests."""

    def test_default_config(self):
        cfg = AetherConfig()
        assert cfg.model.provider == "openai"
        assert cfg.model.model == "gpt-4o"
        assert cfg.memory.max_entries == 2000
        assert cfg.security.auto_approve_level == 1

    def test_config_serialization(self):
        cfg = AetherConfig()
        data = cfg.model_dump()
        assert "model" in data
        assert data["model"]["provider"] == "openai"

        # Re-parse
        cfg2 = AetherConfig(**data)
        assert cfg2.model.provider == "openai"


class TestPlatform:
    """Cross-platform tests."""

    def test_platform_detection(self):
        plat = get_platform()
        assert plat in ("windows", "linux", "darwin")

    def test_platform_info(self):
        info = PlatformInfo()
        assert info.os in ("windows", "linux", "darwin")
        assert info.shell in ("bash", "zsh", "cmd", "pwsh", "powershell")
        assert info.python_version

    def test_shell_executor_basic(self):
        executor = ShellExecutor()
        result = executor.execute_sync("echo hello", timeout=5)
        assert result.exit_code == 0
        assert "hello" in result.stdout


class TestLLMClient:
    """LLM client tests (offline)."""

    def test_client_creation(self):
        cfg = AetherConfig()
        client = LLMClient(cfg)
        assert client.primary.provider == "openai"
        assert client.primary.model == "gpt-4o"

    def test_chat_message(self):
        msg = ChatMessage(role="user", content="test")
        d = msg.to_dict()
        assert d["role"] == "user"
        assert d["content"] == "test"


class TestTools:
    """Built-in tools tests."""

    def test_terminal_tool(self):
        tool = TerminalTool()
        result = tool.execute_sync("echo hello", timeout=5)
        assert result.exit_code == 0
        assert "hello" in result.stdout.replace("\r\n", "\n").replace("\r", "\n")

    def test_file_read_write(self, tmp_path: Path):
        ft = FileTools(workdir=tmp_path)

        # Write
        result = ft.write("test.txt", "line 1\nline 2\nline 3\n")
        assert result.bytes_written > 0

        # Read
        result = ft.read("test.txt")
        assert result.total_lines == 3
        assert "1|line 1" in result.content

        # Search
        result = ft.search("line 2", path=".")
        assert result.total_matches == 1

        # Patch
        result = ft.patch("test.txt", "line 1", "LINE ONE")
        assert result.bytes_written > 0
        content = ft.read("test.txt")
        assert "LINE ONE" in content.content

    def test_file_read_offset(self, tmp_path: Path):
        ft = FileTools(workdir=tmp_path)
        ft.write("test.txt", "\n".join(f"line {i}" for i in range(1, 20)))

        result = ft.read("test.txt", offset=10, limit=3)
        assert result.offset == 10
        assert "10|line 10" in result.content
        assert "12|line 12" in result.content


class TestAgentLoop:
    """Agent loop structure tests."""

    def test_tool_registry(self):
        tools = _build_tool_registry(Path("/tmp"))
        assert "terminal" in tools
        assert "read_file" in tools
        assert "write_file" in tools
        assert "search_files" in tools
        assert "patch_file" in tools

    def test_tools_openai_format(self):
        tools = _build_tool_registry(Path("/tmp"))
        schemas = _tools_to_openai_format(tools)
        assert len(schemas) == 5
        for s in schemas:
            assert s["type"] == "function"
            assert "name" in s["function"]
            assert "parameters" in s["function"]

    def test_loop_creation(self):
        cfg = AetherConfig()
        loop = AgentLoop(cfg)
        assert len(loop.tools) == 5
        assert loop._step_count == 0
