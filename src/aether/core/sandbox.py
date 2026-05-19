"""Sandbox execution — Docker container + process isolation fallback.

Provides isolated command execution with resource limits.
Auto-detects Docker availability, falls back to process isolation.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class SandboxMode(str, Enum):
    DOCKER = "docker"
    PROCESS = "process"
    AUTO = "auto"


@dataclass
class SandboxResult:
    """Result of a sandboxed execution."""
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False
    execution_time_ms: float = 0.0
    mode: SandboxMode = SandboxMode.PROCESS
    container_id: str = ""


@dataclass
class SandboxConfig:
    """Sandbox configuration."""
    mode: SandboxMode = SandboxMode.AUTO
    docker_image: str = "python:3.12-slim"
    timeout_seconds: int = 300
    memory_limit_mb: int = 512
    cpu_limit: float = 1.0
    network_enabled: bool = False
    read_only_root: bool = True
    allowed_directories: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════
# Docker check
# ═══════════════════════════════════════════════════════════

def _is_docker_available() -> bool:
    """Check if Docker is installed and accessible."""
    return shutil.which("docker") is not None


# ═══════════════════════════════════════════════════════════
# Process sandbox (fallback)
# ═══════════════════════════════════════════════════════════

class ProcessSandbox:
    """Lightweight process isolation when Docker is unavailable.

    Limits: working directory restriction, env isolation, timeout enforcement.
    """

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()

    async def execute(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
    ) -> SandboxResult:
        """Execute a command with basic process isolation."""
        start = time.monotonic()

        # Build restricted environment
        restricted_env = {
            "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
            "HOME": workdir or tempfile.gettempdir(),
            "TMPDIR": tempfile.gettempdir(),
            "LANG": "C.UTF-8",
        }
        if env:
            restricted_env.update(env)

        # Build shell command (cross-platform)
        if sys.platform == "win32":
            # Windows: prefer pwsh, fall back to cmd
            if shutil.which("pwsh"):
                shell_cmd = ["pwsh", "-NoProfile", "-Command", command]
            else:
                shell_cmd = ["cmd.exe", "/c", command]
        else:
            shell = "bash" if shutil.which("bash") else "sh"
            shell_cmd = [shell, "-c", command]

        try:
            proc = await asyncio.create_subprocess_exec(
                *shell_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=workdir or os.getcwd(),
                env=restricted_env,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.timeout_seconds,
                )
                elapsed = (time.monotonic() - start) * 1000
                return SandboxResult(
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    execution_time_ms=elapsed,
                    mode=SandboxMode.PROCESS,
                )
            except asyncio.TimeoutError:
                proc.kill()
                elapsed = (time.monotonic() - start) * 1000
                return SandboxResult(
                    timed_out=True,
                    execution_time_ms=elapsed,
                    mode=SandboxMode.PROCESS,
                    stderr=f"Command timed out after {self.config.timeout_seconds}s",
                )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return SandboxResult(
                exit_code=-1,
                stderr=str(e),
                execution_time_ms=elapsed,
                mode=SandboxMode.PROCESS,
            )


# ═══════════════════════════════════════════════════════════
# Docker sandbox
# ═══════════════════════════════════════════════════════════

class DockerSandbox:
    """Execute commands in isolated Docker containers."""

    def __init__(self, config: SandboxConfig | None = None):
        self.config = config or SandboxConfig()
        self._available = _is_docker_available()

    @property
    def available(self) -> bool:
        return self._available

    async def execute(
        self,
        command: str,
        workdir: str | None = None,
        env: dict[str, str] | None = None,
        mount_dirs: list[str] | None = None,
    ) -> SandboxResult:
        """Execute a command in a Docker container."""
        if not self._available:
            return SandboxResult(
                stderr="Docker not available",
                exit_code=-1,
                mode=SandboxMode.PROCESS,
            )

        start = time.monotonic()
        container_name = f"aether-sandbox-{int(time.time())}"

        docker_args = [
            "docker", "run", "--rm",
            "--name", container_name,
            f"--memory={self.config.memory_limit_mb}m",
            f"--cpus={self.config.cpu_limit}",
            "--network", "none" if not self.config.network_enabled else "bridge",
        ]

        if self.config.read_only_root:
            docker_args.append("--read-only")
            docker_args.extend(["--tmpfs", "/tmp:exec"])

        # Mount working directory
        wd = workdir or os.getcwd()
        docker_args.extend(["-v", f"{wd}:{wd}:rw"])
        docker_args.extend(["-w", wd])

        # Mount additional directories
        for d in (mount_dirs or []):
            if os.path.exists(d):
                docker_args.extend(["-v", f"{d}:{d}:ro"])

        # Environment variables
        if env:
            for k, v in env.items():
                docker_args.extend(["-e", f"{k}={v}"])

        docker_args.append(self.config.docker_image)
        docker_args.extend(["sh", "-c", command])

        try:
            proc = await asyncio.create_subprocess_exec(
                *docker_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(),
                    timeout=self.config.timeout_seconds + 30,  # Extra for Docker overhead
                )
                elapsed = (time.monotonic() - start) * 1000
                return SandboxResult(
                    stdout=stdout_bytes.decode("utf-8", errors="replace"),
                    stderr=stderr_bytes.decode("utf-8", errors="replace"),
                    exit_code=proc.returncode or 0,
                    execution_time_ms=elapsed,
                    mode=SandboxMode.DOCKER,
                    container_id=container_name,
                )
            except asyncio.TimeoutError:
                # Kill container
                kill_proc = await asyncio.create_subprocess_exec(
                    "docker", "kill", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await kill_proc.wait()
                elapsed = (time.monotonic() - start) * 1000
                return SandboxResult(
                    timed_out=True,
                    execution_time_ms=elapsed,
                    mode=SandboxMode.DOCKER,
                    container_id=container_name,
                    stderr=f"Docker command timed out after {self.config.timeout_seconds}s",
                )
        except Exception as e:
            elapsed = (time.monotonic() - start) * 1000
            return SandboxResult(
                exit_code=-1,
                stderr=str(e),
                execution_time_ms=elapsed,
                mode=SandboxMode.DOCKER,
            )


# ═══════════════════════════════════════════════════════════
# Unified sandbox (auto-detection)
# ═══════════════════════════════════════════════════════════

class Sandbox:
    """Unified sandbox that auto-selects Docker or process isolation."""

    def __init__(self, config: SandboxConfig | None = None, workdir: str | None = None):
        self.config = config or SandboxConfig()
        self.workdir = workdir or os.getcwd()
        self.docker = DockerSandbox(self.config)
        self.process = ProcessSandbox(self.config)

        # Determine mode
        if self.config.mode == SandboxMode.DOCKER:
            self._use_docker = True
        elif self.config.mode == SandboxMode.PROCESS:
            self._use_docker = False
        else:  # AUTO
            self._use_docker = self.docker.available

    @property
    def mode(self) -> SandboxMode:
        return SandboxMode.DOCKER if self._use_docker else SandboxMode.PROCESS

    @property
    def docker_available(self) -> bool:
        return self.docker.available

    async def execute(
        self,
        command: str,
        env: dict[str, str] | None = None,
        mount_dirs: list[str] | None = None,
    ) -> SandboxResult:
        """Execute command in the best available sandbox."""
        if self._use_docker:
            result = await self.docker.execute(
                command,
                workdir=self.workdir,
                env=env,
                mount_dirs=mount_dirs,
            )
            if result.exit_code == -1 and "Docker not available" in result.stderr:
                # Docker was expected but unavailable — fallback with warning
                result = await self.process.execute(command, workdir=self.workdir, env=env)
                result.stderr = "[WARNING: Docker unavailable, using process isolation]\n" + result.stderr
            return result
        else:
            return await self.process.execute(command, workdir=self.workdir, env=env)
