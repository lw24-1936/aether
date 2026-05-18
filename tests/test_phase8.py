"""Phase 8 tests: Web API (FastAPI REST + WebSocket)."""

import pytest
from fastapi.testclient import TestClient

from aether.api import create_app
from aether.core.config import AetherConfig


@pytest.fixture
def client():
    config = AetherConfig()
    app = create_app(config, workdir="/tmp")
    return TestClient(app)


class TestHealthEndpoint:
    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data


class TestSessions:
    def test_list_sessions(self, client):
        resp = client.get("/sessions")
        assert resp.status_code == 200
        assert "sessions" in resp.json()

    def test_delete_session(self, client):
        resp = client.delete("/sessions/nonexistent-123")
        assert resp.status_code in (200, 404)


class TestMemoryAPI:
    def test_memory_stats(self, client):
        resp = client.get("/memory/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert "total_entries" in data
        assert "max_entries" in data

    def test_search_memory(self, client):
        resp = client.get("/memory?q=test&limit=5")
        assert resp.status_code == 200
        assert "results" in resp.json()


class TestSkillsAPI:
    def test_list_skills(self, client):
        resp = client.get("/skills")
        assert resp.status_code == 200
        assert "skills" in resp.json()

    def test_get_skill_not_found(self, client):
        resp = client.get("/skills/nonexistent-skill-xyz")
        assert resp.status_code == 404

    def test_create_skill(self, client):
        resp = client.post("/skills", json={
            "name": "api-test-skill",
            "description": "Test skill from API",
            "body": "## Steps\n1. Do it",
            "triggers": ["api-test"],
            "category": "testing",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "api-test-skill"


class TestCronAPI:
    def test_list_cron(self, client):
        resp = client.get("/cron")
        assert resp.status_code == 200
        assert "jobs" in resp.json()


class TestBreakersAPI:
    def test_breaker_status(self, client):
        resp = client.get("/breakers")
        assert resp.status_code == 200
        assert "breakers" in resp.json()
