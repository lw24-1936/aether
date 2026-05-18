"""A2A (Agent-to-Agent) client — discover and delegate to external agents.

Google's A2A protocol: agent card discovery, streaming task delegation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx


# ═══════════════════════════════════════════════════════════
# A2A types
# ═══════════════════════════════════════════════════════════

@dataclass
class AgentCard:
    """An A2A agent's capability card."""
    name: str
    description: str = ""
    url: str = ""
    version: str = "1.0.0"
    capabilities: dict[str, Any] = field(default_factory=dict)
    skills: list[dict] = field(default_factory=list)


@dataclass
class TaskResult:
    """Result from an A2A task delegation."""
    task_id: str
    status: Literal["completed", "failed", "cancelled", "working"] = "completed"
    artifacts: list[dict] = field(default_factory=list)
    error: str | None = None


# ═══════════════════════════════════════════════════════════
# A2A Client
# ═══════════════════════════════════════════════════════════

class A2AClient:
    """Client for Google's Agent-to-Agent (A2A) protocol.

    Usage:
        client = A2AClient()
        card = await client.discover("https://agent.example.com")
        result = await client.send_task(
            card, "Analyze this data", artifacts=[{"type": "text", "content": "..."}]
        )
    """

    def __init__(self, timeout: float = 120.0):
        self._http = httpx.AsyncClient(timeout=httpx.Timeout(timeout))
        self._known_agents: dict[str, AgentCard] = {}

    async def close(self) -> None:
        await self._http.aclose()

    async def discover(self, url: str) -> AgentCard:
        """Discover an agent's capabilities via its card URL.

        The agent's /.well-known/agent.json is fetched (A2A convention).
        """
        card_url = url.rstrip("/") + "/.well-known/agent.json"

        try:
            resp = await self._http.get(card_url)
            if resp.status_code == 200:
                data = resp.json()
                card = AgentCard(
                    name=data.get("name", url),
                    description=data.get("description", ""),
                    url=data.get("url", url),
                    version=data.get("version", "1.0"),
                    capabilities=data.get("capabilities", {}),
                    skills=data.get("skills", []),
                )
                self._known_agents[card.name] = card
                return card
        except Exception:
            pass

        # Fallback: create a basic card
        card = AgentCard(
            name=url.split("://")[-1].split("/")[0],
            url=url,
            description="Agent discovered at " + url,
        )
        self._known_agents[card.name] = card
        return card

    async def send_task(
        self,
        card: AgentCard,
        message: str,
        artifacts: list[dict] | None = None,
        stream: bool = False,
    ) -> TaskResult:
        """Send a task to an A2A agent.

        Args:
            card: The agent's card (from discover)
            message: The task description/message
            artifacts: Optional artifacts (files, data)
            stream: Whether to stream the response
        """
        task_id = uuid.uuid4().hex[:12]
        task_url = card.url.rstrip("/") + "/tasks"

        body = {
            "id": task_id,
            "message": {
                "role": "user",
                "parts": [{"type": "text", "text": message}],
            },
        }

        if artifacts:
            for a in artifacts:
                body["message"]["parts"].append(a)

        try:
            resp = await self._http.post(
                task_url,
                json=body,
                headers={"Content-Type": "application/json"},
            )

            if resp.status_code in (200, 201):
                data = resp.json()
                return TaskResult(
                    task_id=task_id,
                    status=data.get("status", "completed"),
                    artifacts=data.get("artifacts", []),
                )
            else:
                return TaskResult(
                    task_id=task_id,
                    status="failed",
                    error=f"HTTP {resp.status_code}: {resp.text[:300]}",
                )
        except Exception as e:
            return TaskResult(task_id=task_id, status="failed", error=str(e))

    async def list_known_agents(self) -> list[AgentCard]:
        """Get all known agents."""
        return list(self._known_agents.values())

    async def get_task_status(self, card: AgentCard, task_id: str) -> TaskResult:
        """Poll the status of a previously submitted task."""
        task_url = f"{card.url.rstrip('/')}/tasks/{task_id}"
        try:
            resp = await self._http.get(task_url)
            if resp.status_code == 200:
                data = resp.json()
                return TaskResult(
                    task_id=task_id,
                    status=data.get("status", "completed"),
                    artifacts=data.get("artifacts", []),
                )
        except Exception:
            pass
        return TaskResult(task_id=task_id, status="failed", error="Could not fetch status")
