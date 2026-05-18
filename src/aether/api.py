"""Aether Web API — FastAPI REST + WebSocket streaming.

Provides HTTP access to the full Aether agent: chat, memory, skills, cron.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from aether.core.config import load_config, AetherConfig
from aether.core.loop import AgentLoop
from aether.core.llm import ChatMessage
from aether.core.models import StreamEvent


# ═══════════════════════════════════════════════════════════
# Request/Response models
# ═══════════════════════════════════════════════════════════

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    stream: bool = True


class ChatResponse(BaseModel):
    session_id: str
    response: str
    tool_calls: list[dict[str, Any]] = []
    steps: int = 0


class MemoryRequest(BaseModel):
    content: str
    target: str = "user"
    tags: list[str] = []


class SkillCreateRequest(BaseModel):
    name: str
    description: str
    body: str
    triggers: list[str] = []
    category: str = "general"


class CronCreateRequest(BaseModel):
    name: str
    schedule: str
    prompt: str | None = None
    script_path: str | None = None


# ═══════════════════════════════════════════════════════════
# App factory
# ═══════════════════════════════════════════════════════════

def create_app(config: AetherConfig | None = None, workdir: str | None = None) -> FastAPI:
    """Create a FastAPI app with Aether endpoints."""

    if config is None:
        config = load_config()

    app = FastAPI(
        title="Aether Agent API",
        version="0.1.0",
        description="Universal AI Agent Framework — REST + WebSocket API",
    )

    wd = Path(workdir) if workdir else Path.cwd()

    # ── Session store ──
    sessions: dict[str, AgentLoop] = {}

    def get_or_create_session(session_id: str | None) -> tuple[str, AgentLoop]:
        if session_id and session_id in sessions:
            return session_id, sessions[session_id]
        sid = session_id or uuid.uuid4().hex[:12]
        loop = AgentLoop(config, wd)
        sessions[sid] = loop
        return sid, loop

    # ═══════════════════════════════════════════════════════
    # REST endpoints
    # ═══════════════════════════════════════════════════════

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "version": "0.1.0",
            "sessions": len(sessions),
        }

    @app.post("/chat")
    async def chat(req: ChatRequest) -> ChatResponse:
        """Non-streaming chat endpoint."""
        sid, loop = get_or_create_session(req.session_id)

        full_response = ""
        tool_calls = []
        steps = 0

        async for event in loop.run(user_message=req.message):
            if event.type == "text_delta":
                full_response += event.data.get("content", "")
            elif event.type == "tool_call":
                tool_calls.append({
                    "name": event.data.get("name"),
                    "args": event.data.get("arguments"),
                })
            elif event.type == "done":
                steps = event.data.get("steps", 0)

        return ChatResponse(
            session_id=sid,
            response=full_response,
            tool_calls=tool_calls,
            steps=steps,
        )

    @app.get("/sessions")
    async def list_sessions():
        return {"sessions": list(sessions.keys()), "count": len(sessions)}

    @app.delete("/sessions/{session_id}")
    async def delete_session(session_id: str):
        if session_id in sessions:
            loop = sessions.pop(session_id)
            await loop.close()
            return {"status": "deleted"}
        raise HTTPException(404, "Session not found")

    # ── Memory endpoints ──
    @app.post("/memory")
    async def add_memory(req: MemoryRequest):
        sid, loop = get_or_create_session(None)
        record = loop.memory.remember(req.content, target=req.target, tags=req.tags)
        return {"id": record.id, "content": record.content}

    @app.get("/memory")
    async def search_memory(q: str = "", limit: int = 10):
        sid, loop = get_or_create_session(None)
        results = loop.memory.recall(q, limit=limit)
        return {"results": [r.to_dict() for r in results]}

    @app.get("/memory/stats")
    async def memory_stats():
        sid, loop = get_or_create_session(None)
        return loop.memory.stats()

    # ── Skills endpoints ──
    @app.get("/skills")
    async def list_skills():
        sid, loop = get_or_create_session(None)
        skills = loop.skills.list_all()
        return {"skills": [{"name": s.name, "description": s.description, "category": s.category} for s in skills]}

    @app.post("/skills")
    async def create_skill(req: SkillCreateRequest):
        sid, loop = get_or_create_session(None)
        skill = loop.skills.create(
            name=req.name, description=req.description,
            body=req.body, triggers=req.triggers, category=req.category,
        )
        return {"name": skill.meta.name, "status": "created"}

    @app.get("/skills/{name}")
    async def get_skill(name: str):
        sid, loop = get_or_create_session(None)
        skill = loop.skills.load(name)
        if not skill:
            raise HTTPException(404, "Skill not found")
        return {"name": skill.meta.name, "body": skill.body, "triggers": skill.meta.triggers}

    # ── Cron endpoints ──
    @app.get("/cron")
    async def list_cron_jobs():
        from aether.core.cron import CronEngine
        engine = CronEngine(data_dir=Path.cwd() / ".aether" / "cron")
        engine.load()
        return {"jobs": [{"id": j.id, "name": j.name, "schedule": j.schedule, "status": j.status.value} for j in engine.list_all()]}

    @app.post("/cron")
    async def create_cron_job(req: CronCreateRequest):
        from aether.core.cron import CronEngine
        engine = CronEngine(data_dir=Path.cwd() / ".aether" / "cron")
        job = engine.create(req.name, req.schedule, prompt=req.prompt, script_path=req.script_path)
        return {"id": job.id, "name": job.name, "status": "created"}

    # ── Circuit breaker status ──
    @app.get("/breakers")
    async def breaker_status():
        sid, loop = get_or_create_session(None)
        return {"breakers": loop.breakers.status_all()}

    # ═══════════════════════════════════════════════════════
    # WebSocket streaming
    # ═══════════════════════════════════════════════════════

    @app.websocket("/ws/{session_id}")
    async def websocket_chat(websocket: WebSocket, session_id: str):
        await websocket.accept()
        sid, loop = get_or_create_session(session_id)

        try:
            # Send session info
            await websocket.send_json({"type": "connected", "session_id": sid})

            while True:
                data = await websocket.receive_text()

                # Parse message
                import json
                try:
                    msg = json.loads(data)
                    text = msg.get("message", data)
                except json.JSONDecodeError:
                    text = data

                # Handle commands
                if text.startswith("/") and text.strip() in ("/quit", "/exit"):
                    await websocket.send_json({"type": "done", "status": "closed"})
                    break

                # Stream agent response
                async for event in loop.run(user_message=text):
                    await websocket.send_json({
                        "type": event.type,
                        "data": event.data,
                        "event_id": event.event_id,
                    })

                await websocket.send_json({"type": "done"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            await websocket.send_json({"type": "error", "data": {"message": str(e)}})

    return app


def main():
    """Run the Aether web server."""
    import uvicorn
    app = create_app()
    uvicorn.run(app, host="0.0.0.0", port=8420, log_level="info")


if __name__ == "__main__":
    main()
