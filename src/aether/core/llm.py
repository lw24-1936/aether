"""Lightweight LLM client for Aether.

Uses httpx + OpenAI-compatible API. Supports function calling (tools).
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx

from aether.core.config import AetherConfig, ModelConfig


@dataclass
class LLMResponse:
    content: str
    model: str
    provider: str
    tokens_prompt: int = 0
    tokens_completion: int = 0
    tokens_total: int = 0
    latency_ms: float = 0.0
    finish_reason: str = "stop"
    tool_calls: list[dict] | None = None


@dataclass
class LLMStreamChunk:
    delta: str
    finish_reason: str | None = None
    is_done: bool = False
    tool_calls: list[dict] | None = None


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


class LLMClient:
    """Lightweight multi-provider LLM client with function calling."""

    def __init__(self, config: AetherConfig):
        self.config = config
        self.primary = config.model
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(120.0))
        self._call_count = 0
        self._error_count = 0

    async def close(self) -> None:
        """Safely close the HTTP client."""
        try:
            await self._http.aclose()
        except Exception:
            pass  # Ignore cleanup errors on Windows

    def _get_api_key(self, provider: str) -> str:
        import os
        env_map = {
            "deepseek": "DEEPSEEK_API_KEY",
            "openai": "OPENAI_API_KEY",
            "groq": "GROQ_API_KEY",
        }
        env_key = env_map.get(provider, f"{provider.upper()}_API_KEY")
        if os.environ.get(env_key):
            return os.environ[env_key]
        if self.primary.provider == provider:
            return self.primary.api_key
        return ""

    def _get_api_base(self, provider: str) -> str:
        if self.primary.provider == provider and self.primary.api_base:
            return self.primary.api_base
        bases = {
            "deepseek": "https://api.deepseek.com/v1",
            "groq": "https://api.groq.com/openai/v1",
            "openai": "https://api.openai.com/v1",
        }
        return bases.get(provider, "https://api.openai.com/v1")

    def _build_body(
        self, model: str, messages: list[dict], stream: bool,
        temperature: float, max_tokens: int, tools: list[dict] | None,
    ) -> dict:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    async def _call_api(
        self, provider: str, model: str, messages: list[dict],
        stream: bool, temperature: float, max_tokens: int,
        tools: list[dict] | None = None,
    ) -> httpx.Response:
        api_key = self._get_api_key(provider)
        api_base = self._get_api_base(provider)
        body = self._build_body(model, messages, stream, temperature, max_tokens, tools)
        self._call_count += 1
        return await self._http.post(
            f"{api_base}/chat/completions",
            json=body,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        )

    async def chat(
        self, messages: list[ChatMessage], system_prompt: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> LLMResponse:
        temp = temperature or self.primary.temperature
        mx_tokens = max_tokens or self.primary.max_tokens

        msg_dicts = []
        if system_prompt:
            msg_dicts.append({"role": "system", "content": system_prompt})
        msg_dicts.extend(m.to_dict() for m in messages)

        resp = await self._call_api(
            self.primary.provider, self.primary.model, msg_dicts,
            False, temp, mx_tokens, tools,
        )

        if resp.status_code != 200:
            raise RuntimeError(f"API error HTTP {resp.status_code}: {resp.text[:300]}")

        data = resp.json()
        choice = data["choices"][0]
        msg = choice["message"]
        usage = data.get("usage", {})

        return LLMResponse(
            content=msg.get("content") or "",
            model=data.get("model", self.primary.model),
            provider=self.primary.provider,
            tokens_prompt=usage.get("prompt_tokens", 0),
            tokens_completion=usage.get("completion_tokens", 0),
            tokens_total=usage.get("total_tokens", 0),
            latency_ms=0,
            finish_reason=choice.get("finish_reason", "stop"),
            tool_calls=msg.get("tool_calls"),
        )

    async def chat_stream(
        self, messages: list[ChatMessage], system_prompt: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
        tools: list[dict] | None = None,
    ) -> AsyncIterator[LLMStreamChunk]:
        temp = temperature or self.primary.temperature
        mx_tokens = max_tokens or self.primary.max_tokens

        msg_dicts = []
        if system_prompt:
            msg_dicts.append({"role": "system", "content": system_prompt})
        msg_dicts.extend(m.to_dict() for m in messages)

        resp = await self._call_api(
            self.primary.provider, self.primary.model, msg_dicts,
            True, temp, mx_tokens, tools,
        )

        if resp.status_code != 200:
            try:
                result = await self.chat(messages, system_prompt, temperature, max_tokens, tools)
                yield LLMStreamChunk(delta=result.content, finish_reason="stop", is_done=True)
                return
            except Exception:
                yield LLMStreamChunk(delta=f"Error: HTTP {resp.status_code}", is_done=True)
                return

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
                tc = delta.get("tool_calls")
                if content:
                    yield LLMStreamChunk(delta=content, finish_reason=finish, tool_calls=tc)
                if finish:
                    yield LLMStreamChunk(delta="", finish_reason=finish, is_done=True, tool_calls=tc)
                    break
            except (json.JSONDecodeError, KeyError):
                continue
