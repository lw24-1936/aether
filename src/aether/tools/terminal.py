"""Built-in terminal tool — cross-platform shell execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from aether.platform import ShellExecutor, ShellResult


@dataclass
class TerminalTool:
    """Execute shell commands with cross-platform support."""

    name: str = "terminal"
    description: str = "Execute a shell command. Works on Windows (cmd/pwsh) and Linux (bash)."

    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()
        self._executor = ShellExecutor(workdir=self.workdir)
        self._session_approved: set[str] = set()

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The shell command to execute",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30,
                },
                "workdir": {
                    "type": "string",
                    "description": "Working directory (optional)",
                },
            },
            "required": ["command"],
        }

    async def execute(
        self,
        command: str,
        timeout: int = 30,
        workdir: str | None = None,
    ) -> ShellResult:
        """Execute a command and return the result."""
        if workdir:
            self._executor.workdir = Path(workdir)
        return await self._executor.execute(command, timeout=timeout)

    def execute_sync(self, command: str, timeout: int = 30) -> ShellResult:
        return self._executor.execute_sync(command, timeout=timeout)
