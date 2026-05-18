"""Configuration system for Aether Agent.

Reads from YAML config files and environment variables.
Uses platformdirs for cross-platform config paths.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Literal

import yaml
from platformdirs import user_config_dir, user_data_dir
from pydantic import BaseModel, Field


# ═══════════════════════════════════════════════════════════
# Config paths (cross-platform)
# ═══════════════════════════════════════════════════════════

CONFIG_DIR = Path(user_config_dir("aether", ensure_exists=True))
DATA_DIR = Path(user_data_dir("aether", ensure_exists=True))
CONFIG_FILE = CONFIG_DIR / "config.yaml"
DEFAULT_CONFIG = CONFIG_DIR / "config.default.yaml"


# ═══════════════════════════════════════════════════════════
# Config models
# ═══════════════════════════════════════════════════════════

class ModelConfig(BaseModel):
    """Default model configuration."""
    provider: str = "openai"
    model: str = "gpt-4o"
    api_key: str = ""
    api_base: str | None = None
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = 4096


class MemoryConfig(BaseModel):
    """Memory system configuration."""
    max_entries: int = 2000
    embedding_model: str = "text-embedding-3-small"
    vector_db_path: str = ""  # auto-set from DATA_DIR
    session_search_limit: int = 50


class SecurityConfig(BaseModel):
    """Security configuration."""
    auto_approve_level: int = 1  # Level 0-1 auto-approved
    approval_timeout_seconds: int = 30
    command_blacklist: list[str] = Field(default_factory=lambda: [
        "rm -rf /", "dd if=/dev/zero", "mkfs.", ":(){ :|:& };:",
        "curl | sh", "wget -O - | sh",
    ])
    allowed_directories: list[str] = Field(default_factory=list)


class SandboxConfig(BaseModel):
    """Sandbox execution configuration."""
    mode: Literal["docker", "process", "auto"] = "auto"
    docker_image: str = "python:3.12-slim"
    timeout_seconds: int = 300
    memory_limit_mb: int = 512


class AetherConfig(BaseModel):
    """Root configuration."""
    model: ModelConfig = Field(default_factory=ModelConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    debug: bool = False


# ═══════════════════════════════════════════════════════════
# Config loading
# ═══════════════════════════════════════════════════════════

def _merge_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    """Merge environment variable overrides into config dict."""
    env_map = {
        "AETHER_MODEL_PROVIDER": ("model", "provider"),
        "AETHER_MODEL_NAME": ("model", "model"),
        "AETHER_MODEL_API_KEY": ("model", "api_key"),
        "AETHER_MODEL_API_BASE": ("model", "api_base"),
        "AETHER_DEBUG": ("debug", lambda v: v.lower() in ("1", "true", "yes")),
    }
    for env_var, path in env_map.items():
        value = os.environ.get(env_var)
        if value is not None:
            if callable(path[1]):
                config[path[0]] = path[1](value)
            else:
                config.setdefault(path[0], {})[path[1]] = value
    return config


def load_config(config_path: str | Path | None = None) -> AetherConfig:
    """Load configuration from YAML file with env overrides.

    Resolution order:
    1. Default values (from AetherConfig defaults)
    2. config.yaml (at config_path or CONFIG_DIR)
    3. Environment variables (AETHER_*)
    """
    config_dict: dict[str, Any] = {}

    # Try to load YAML config
    path = Path(config_path) if config_path else CONFIG_FILE
    if path.exists():
        with open(path) as f:
            config_dict = yaml.safe_load(f) or {}

    # Merge env overrides
    config_dict = _merge_env_overrides(config_dict)

    return AetherConfig(**config_dict)


def save_config(config: AetherConfig, config_path: str | Path | None = None) -> None:
    """Save configuration to YAML file."""
    path = Path(config_path) if config_path else CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.safe_dump(config.model_dump(), f, default_flow_style=False, indent=2)


def get_default_config() -> AetherConfig:
    """Get the default configuration."""
    return AetherConfig()


# Rebuild models with forward references (required by from __future__ import annotations)
ModelConfig.model_rebuild()
MemoryConfig.model_rebuild()
SecurityConfig.model_rebuild()
SandboxConfig.model_rebuild()
AetherConfig.model_rebuild()
