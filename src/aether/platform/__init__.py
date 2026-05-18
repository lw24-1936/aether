"""Platform abstraction layer.

Handles cross-platform differences between Windows and Linux.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from platformdirs import user_cache_dir, user_config_dir, user_data_dir


# ═══════════════════════════════════════════════════════════
# Platform detection
# ═══════════════════════════════════════════════════════════

def get_platform() -> str:
    """Detect the current platform."""
    if sys.platform == "win32":
        return "windows"
    elif sys.platform == "darwin":
        return "darwin"
    return "linux"


def get_config_dir() -> Path:
    """Get the platform-appropriate config directory."""
    return Path(user_config_dir("aether", ensure_exists=True))


def get_data_dir() -> Path:
    """Get the platform-appropriate data directory."""
    return Path(user_data_dir("aether", ensure_exists=True))


def get_cache_dir() -> Path:
    """Get the platform-appropriate cache directory."""
    return Path(user_cache_dir("aether", ensure_exists=True))


# ═══════════════════════════════════════════════════════════
# Shell result
# ═══════════════════════════════════════════════════════════

@dataclass
class ShellResult:
    """Result of a shell command execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    execution_time_ms: float = 0.0


# ═══════════════════════════════════════════════════════════
# Shell executor
# ═══════════════════════════════════════════════════════════

class ShellExecutor:
    """Cross-platform shell command executor.

    Auto-detects shell: pwsh > bash > cmd.
    Handles platform-specific command adaptations.
    """

    def __init__(self, workdir: Path | None = None):
        self.platform = get_platform()
        self.workdir = workdir or Path.cwd()
        self.shell = self._detect_shell()

    def _detect_shell(self) -> str:
        """Detect the best available shell."""
        if self.platform == "windows":
            for candidate in ("pwsh", "powershell"):
                if shutil.which(candidate):
                    return candidate
            return "cmd"
        return "bash"

    def _build_command(self, shell_cmd: str) -> tuple[str, list[str]]:
        """Build the platform-appropriate command invocation."""
        if self.shell == "cmd":
            return "cmd.exe", ["/c", shell_cmd]
        elif self.shell == "pwsh":
            return "pwsh", ["-NoProfile", "-Command", shell_cmd]
        elif self.shell == "powershell":
            return "powershell", ["-NoProfile", "-Command", shell_cmd]
        else:
            return "bash", ["-c", shell_cmd]

    def _adapt_command(self, command: str) -> str:
        """Adapt command for the detected shell."""
        if self.shell == "cmd":
            # cmd.exe: replace common bash-isms
            command = command.replace("&&", "&&")
        return command

    async def execute(
        self,
        command: str,
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Execute a shell command and return the result."""
        import time

        adapted = self._adapt_command(command)
        exe, args = self._build_command(adapted)
        full_args = [*args]

        merged_env = os.environ.copy()
        if env:
            merged_env.update(env)

        start = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                exe,
                *full_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.workdir),
                env=merged_env,
            )
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            elapsed = (time.monotonic() - start) * 1000

            return ShellResult(
                stdout=stdout_bytes.decode("utf-8", errors="replace"),
                stderr=stderr_bytes.decode("utf-8", errors="replace"),
                exit_code=proc.returncode or 0,
                execution_time_ms=elapsed,
            )
        except asyncio.TimeoutError:
            elapsed = (time.monotonic() - start) * 1000
            if proc:
                proc.kill()
            return ShellResult(
                timed_out=True,
                execution_time_ms=elapsed,
                stderr=f"Command timed out after {timeout}s",
            )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return ShellResult(
                exit_code=-1,
                stderr=str(e),
                execution_time_ms=elapsed,
            )

    def execute_sync(
        self,
        command: str,
        timeout: int = 30,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Synchronous wrapper for execute()."""
        return asyncio.run(self.execute(command, timeout, env))


# ═══════════════════════════════════════════════════════════
# Platform info
# ═══════════════════════════════════════════════════════════

@dataclass
class PlatformInfo:
    """Information about the current platform."""
    os: str = field(default_factory=get_platform)
    shell: str = ""
    python_version: str = ""
    config_dir: Path = field(default_factory=get_config_dir)
    data_dir: Path = field(default_factory=get_data_dir)
    cache_dir: Path = field(default_factory=get_cache_dir)
    session_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    def __post_init__(self):
        if not self.shell:
            executor = ShellExecutor()
            self.shell = executor.shell
        if not self.python_version:
            self.python_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
