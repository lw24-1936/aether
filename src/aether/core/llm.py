"""Lightweight LLM client for Aether.

Uses httpx + OpenAI-compatible API (works with OpenAI, DeepSeek, Groq, etc.)
No heavy dependencies like litellm.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from aether.core.config import AetherConfig, ModelConfig


# ═══════════════════════════════════════════════════════════
# Data types
# ═══════════════════════════════════════════════════════════

@dataclass
class LLMResponse:
    """Non-streaming LLM response."""
    content: str
    model: str
    provider: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
    latency_ms: float = 0.0
    finish_reason: str = "stop"


@dataclass
class LLMStreamChunk:
    """Streaming LLM response chunk."""
    delta: str
    finish_reason: str | None = None
    is_done: bool = False


# ═══════════════════════════════════════════════════════════
# Message format
# ═══════════════════════════════════════════════════════════

@dataclass
class ChatMessage:
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None = None
    tool_calls: list[dict] | None = None

    def to_dict(self) -> dict[str, Any]:
        msg: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_call_id:
            msg["tool_call_id"] = self.tool_call_id
        if self.tool_calls:
            msg["tool_calls"] = self.tool_calls
        return msg


# ═══════════════════════════════════════════════════════════
# Fallback chain
# ═══════════════════════════════════════════════════════════

FALLBACK_CHAIN: list[tuple[str, str, str | None]] = [
    # (provider, model, api_base)
    ("openai", "gpt-4o", None),
    ("openai", "gpt-4o-mini", None),
    ("deepseek", "deepseek-chat", "https://api.deepseek.com/v1"),
]


# ═══════════════════════════════════════════════════════════
# LLM Client
# ═══════════════════════════════════════════════════════════

class LLMClient:
    """Lightweight, multi-provider LLM client with fallback.

    Usage:
        client = LLMClient(config)
        async for chunk in client.chat_stream(messages):
            print(chunk.delta, end="")
    """

    def __init__(self, config: AetherConfig):
        self.config = config
        self.primary = config.model
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self._call_count = 0
        self._error_count = 0

    async def close(self) -> None:
        await self._http.aclose()

    def _get_api_key(self, provider: str) -> str:
        """Get API key for provider. Checks config and env vars."""
        import os

        # Try environment variable first
        env_key = f"{provider.upper()}_API_KEY"
        if os.environ.get(env_key):
            return os.environ[env_key]

        # Try config
        if self.primary.provider == provider:
            return self.primary.api_key

        return ""

    def _get_api_base(self, provider: str) -> str:
        """Get API base URL for provider."""
        if provider == "deepseek":
            return "https://api.deepseek.com/v1"
        if provider == "groq":
            return "https://api.groq.com/openai/v1"
        if self.primary.provider == provider and self.primary.api_base:
            return self.primary.api_base
        return "https://api.openai.com/v1"

    def _get_model_name(self, provider: str, model: str) -> str:
        """Get full model name."""
        if provider == self.primary.provider and self.primary.model == model:
            return self.primary.model
        return model

    async def _call_api(
        self,
        provider: str,
        model: str,
        messages: list[dict],
        stream: bool,
        temperature: float,
        max_tokens: int,
    ) -> httpx.Response:
        """Make raw API call."""
        api_key = self._get_api_key(provider)
        api_base = self._get_api_base(provider)

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

        self._call_count += 1
        return await self._http.post(
            f"{api_base}/chat/completions",
            json=body,
            headers=headers,
        )

    async def chat(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        """Non-streaming chat completion with fallback."""
        temp = temperature if temperature is not None else self.primary.temperature
        mx_tokens = max_tokens if max_tokens is not None else self.primary.max_tokens

        msg_dicts = []
        if system_prompt:
            msg_dicts.append({"role": "system", "content": system_prompt})
        msg_dicts.extend(m.to_dict() for m in messages)

        if tools:
            # Inject tools into the request body
            pass  # Will handle in execute_api directly

        last_error = None
        for fb_provider, fb_model, fb_base in FALLBACK_CHAIN:
            # Skip if not the primary and not in fallback
            if fb_provider != self.primary.provider and fb_model != self.primary.model:
                # Only try primary + explicit fallbacks
                if fb_provider not in ("openai",):
                    continue

            try:
                start = time.monotonic()
                resp = await self._call_api(
                    fb_provider, fb_model, msg_dicts, False, temp, mx_tokens
                )
                elapsed = (time.monotonic() - start) * 1000

                if resp.status_code == 200:
                    data = resp.json()
                    choice = data["choices"][0]
                    usage = data.get("usage", {})

                    return LLMResponse(
                        content=choice["message"]["content"],
                        model=data.get("model", fb_model),
                        provider=fb_provider,
                        tokens_prompt=usage.get("prompt_tokens", 0),
                        tokens_completion=usage.get("completion_tokens", 0),
                        tokens_total=usage.get("total_tokens", 0),
                        latency_ms=elapsed,
                        finish_reason=choice.get("finish_reason", "stop"),
                    )
                else:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    self._error_count += 1
                    continue
            except Exception as e:
                last_error = str(e)
                self._error_count += 1
                continue

        raise RuntimeError(f"All providers failed. Last error: {last_error}")

    async def chat_stream(
        self,
        messages: list[ChatMessage],
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        """Streaming chat completion."""
        temp = temperature if temperature is not None else self.primary.temperature
        mx_tokens = max_tokens if max_tokens is not None else self.primary.max_tokens

        msg_dicts = []
        if system_prompt:
            msg_dicts.append({"role": "system", "content": system_prompt})
        msg_dicts.extend(m.to_dict() for m in messages)

        resp = await self._call_api(
            self.primary.provider, self.primary.model, msg_dicts, True, temp, mx_tokens
        )

        if resp.status_code != 200:
            # Try non-streaming fallback
            try:
                result = await self.chat(messages, system_prompt, temperature, max_tokens)
                yield LLMStreamChunk(delta=result.content, finish_reason="stop", is_done=True)
                return
            except Exception:
                yield LLMStreamChunk(
                    delta=f"Error: HTTP {resp.status_code}: {resp.text[:300]}",
                    is_done=True,
                )
                return

        buffer = ""
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                yield LLMStreamChunk(delta="", finish_reason="stop", is_done=True)
                break

            try:
                data = json.loads(data_str)
                choice = data["choices"][0]
                delta = choice.get("delta", {})
                content = delta.get("content", "")
                finish = choice.get("finish_reason")

                if content:
                    buffer += content
                    yield LLMStreamChunk(delta=content, finish_reason=finish)

                if finish:
                    yield LLMStreamChunk(delta="", finish_reason=finish, is_done=True)
                    break
            except (json.JSONDecodeError, KeyError):
                continue


# ═══════════════════════════════════════════════════════════
# Convenience function
# ═══════════════════════════════════════════════════════════

def create_client(config: AetherConfig | None = None) -> LLMClient:
    """Create an LLM client from config."""
    if config is None:
        from aether.core.config import AetherConfig
        config = AetherConfig()
    return LLMClient(config)
