"""Built-in terminal tool — cross-platform shell execution."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from aether.platform import ShellExecutor, ShellResult


@dataclass
class TerminalTool:
    """Execute shell commands with cross-platform support."""

    name: str = "terminal"
    description: str = (
        "Execute a shell command. On Windows use cmd.exe commands. "
        "For web requests use: curl.exe -s URL (not bare curl). "
        "For listing files use: dir (not ls)."
    )

    def __init__(self, workdir: Path | None = None):
        self.workdir = workdir or Path.cwd()
        self._executor = ShellExecutor(workdir=self.workdir)

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": (
                        "Shell command. Windows: use curl.exe for HTTP, dir for listing, "
                        "echo for output, type for reading files. "
                        "Linux: use curl, ls, cat, echo."
                    ),
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds (default: 30)",
                    "default": 30,
                },
            },
            "required": ["command"],
        }

    def _adapt_command(self, command: str) -> str:
        """Adapt command for the current platform."""
        if sys.platform != "win32":
            return command

        adapted = command
        # Fix: pwsh aliases 'curl' to 'Invoke-WebRequest' which prompts for URI
        # Replace bare 'curl' with 'curl.exe' (real curl on Windows)
        import re
        adapted = re.sub(r'\bcurl\b(?!\.exe)', 'curl.exe', adapted)
        # Fix: 'ls' is aliased in pwsh, use 'dir' for reliability
        adapted = re.sub(r'\bls\b', 'dir', adapted)
        return adapted

    async def execute(
        self,
        command: str,
        timeout: int = 30,
    ) -> ShellResult:
        # Force UTF-8 on Windows
        if sys.platform == "win32":
            command = f"chcp 65001 >nul 2>&1 && {command}"
        command = self._adapt_command(command)
        return await self._executor.execute(command, timeout=timeout)

    def execute_sync(self, command: str, timeout: int = 30) -> ShellResult:
        if sys.platform == "win32":
            command = f"chcp 65001 >nul 2>&1 && {command}"
        command = self._adapt_command(command)
        return self._executor.execute_sync(command, timeout=timeout)
