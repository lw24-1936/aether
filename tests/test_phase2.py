"""Phase 2 tests: Memory system (SQLite, FTS5, Manager, eviction, conflicts)."""

import tempfile
from pathlib import Path

import pytest

from aether.memory.store import SQLiteMemoryStore, MemoryRecord, MemoryConflict
from aether.memory.manager import MemoryManager


@pytest.fixture
def store():
    """Create a temporary SQLite memory store."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    s = SQLiteMemoryStore(path)
    yield s
    s.close()
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def manager():
    """Create a temporary MemoryManager."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    mgr = MemoryManager(db_path=path, max_entries=100)
    yield mgr
    mgr.close()
    Path(path).unlink(missing_ok=True)


class TestSQLiteStore:
    """SQLite memory store tests."""

    def test_add_and_get(self, store):
        r = store.add("User prefers concise answers", target="user")
        assert r.id
        assert r.content == "User prefers concise answers"
        assert r.target == "user"

        got = store.get(r.id)
        assert got is not None
        assert got.content == r.content

    def test_add_multiple(self, store):
        store.add("Fact 1", target="user")
        store.add("Fact 2", target="project")
        store.add("Fact 3", target="environment")

        all_mem = store.list_all()
        assert len(all_mem) == 3

        by_user = store.list_all(target="user")
        assert len(by_user) == 1
        assert by_user[0].content == "Fact 1"

    def test_update(self, store):
        r = store.add("original content")
        updated = store.update(r.id, "updated content")
        assert updated is not None
        assert updated.content == "updated content"
        assert updated.updated_at >= r.updated_at

    def test_delete(self, store):
        r = store.add("to be deleted")
        assert store.delete(r.id)
        assert store.get(r.id) is None
        assert not store.delete("nonexistent")

    def test_count(self, store):
        assert store.count() == 0
        store.add("a")
        store.add("b")
        assert store.count() == 2
        assert store.count_by_target() == {"user": 2}

    def test_keyword_search(self, store):
        store.add("Python is my preferred language")
        store.add("I like Rust for systems programming")
        store.add("TypeScript for frontend work")

        results = store.search_keyword("Python")
        assert len(results) >= 1
        assert "Python" in results[0].content

        results = store.search_keyword("programming")
        assert len(results) >= 1

        results = store.search_keyword("zzz_nonexistent_zzz")
        assert len(results) == 0

    def test_tag_search(self, store):
        store.add("Project uses PostgreSQL", tags=["db", "postgres"])
        store.add("Project uses Redis", tags=["db", "redis", "cache"])

        results = store.search_by_tag("postgres")
        assert len(results) == 1

        results = store.search_by_tag("db")
        assert len(results) == 2

    def test_recent(self, store):
        store.add("oldest")
        store.add("middle")
        store.add("newest")

        results = store.search_recent(limit=2)
        assert len(results) == 2
        assert results[0].content == "newest"

    def test_eviction(self, store):
        # Add 10 memories
        for i in range(10):
            store.add(f"memory {i}", importance=0.1)

        assert store.count() == 10
        removed = store.evict(max_entries=5)
        assert removed == 5
        assert store.count() == 5

    def test_eviction_exact(self, store):
        for i in range(5):
            store.add(f"memory {i}")
        removed = store.evict(max_entries=10)
        assert removed == 0  # Already under limit

    def test_conflicts_detection(self, store):
        store.add("User prefers Python for backend development")
        store.add("User uses VS Code as editor")

        conflicts = store.find_conflicts("User prefers Python for backend coding")
        assert len(conflicts) >= 1
        assert conflicts[0].type in ("overlap", "contradiction", "outdated")

    def test_no_conflicts_for_different(self, store):
        store.add("User prefers Python")
        conflicts = store.find_conflicts("The weather is nice today")
        assert len(conflicts) == 0


class TestMemoryManager:
    """Memory manager orchestration tests."""

    def test_remember_and_recall(self, manager):
        manager.remember("User is working on an AI agent framework called Aether", target="user")
        manager.remember("Project uses Python 3.12 and httpx", target="project")

        results = manager.recall("Aether")
        assert len(results) >= 1
        assert "Aether" in results[0].content

        results = manager.recall("Python")
        assert len(results) >= 1

    def test_remember_with_tags(self, manager):
        manager.remember("Uses PostgreSQL", tags=["db", "postgres"])
        results = manager.recall_by_tag("postgres")
        assert len(results) == 1

    def test_recall_recent(self, manager):
        manager.remember("first")
        manager.remember("second")
        manager.remember("third")

        results = manager.recall_recent(2)
        assert len(results) == 2
        assert results[0].content == "third"

    def test_forget(self, manager):
        r = manager.remember("temporary fact")
        assert manager.forget(r.id)
        results = manager.recall("temporary")
        assert len(results) == 0

    def test_update_memory(self, manager):
        r = manager.remember("old fact")
        updated = manager.update_memory(r.id, "new fact")
        assert updated is not None
        assert updated.content == "new fact"

    def test_stats(self, manager):
        manager.remember("user fact", target="user")
        manager.remember("project fact", target="project")
        manager.remember("env fact", target="environment")

        stats = manager.stats()
        assert stats["total_entries"] == 3
        assert stats["usage_percent"] == 3.0
        assert stats["by_target"]["user"] == 1
        assert stats["by_target"]["project"] == 1
        assert stats["by_target"]["environment"] == 1

    def test_auto_eviction(self, manager):
        manager.max_entries = 10
        for i in range(20):
            manager.remember(f"memory {i}", importance=0.1)

        # Should have evicted down to ~10
        stats = manager.stats()
        assert stats["total_entries"] <= 10

    def test_export_import(self, manager):
        manager.remember("fact a", target="user", tags=["tag1"])
        manager.remember("fact b", target="project", tags=["tag2"])

        exported = manager.export_all()
        assert len(exported) == 2

        # Clear and re-import
        for r in list(manager.store.list_all()):
            manager.forget(r.id)

        count = manager.import_memories(exported)
        assert count == 2

        results = manager.recall("fact a")
        assert len(results) == 1

    def test_session_search(self, manager):
        sessions = [
            {
                "id": "s1",
                "title": "Python debugging session",
                "messages": [
                    {"role": "user", "content": "Help me debug a Python error"},
                    {"role": "assistant", "content": "Let's look at the traceback"},
                ],
            },
            {
                "id": "s2",
                "title": "Docker setup help",
                "messages": [
                    {"role": "user", "content": "How to set up Docker?"},
                ],
            },
        ]

        results = manager.search_sessions("Python", sessions)
        assert len(results) >= 1
        assert results[0]["session_id"] == "s1"

        results = manager.search_sessions("Docker", sessions)
        assert len(results) >= 1

    def test_conflict_auto_resolve(self, manager):
        # Add initial memory
        manager.remember("User prefers dark mode", target="user")

        # Add similar memory — should detect conflict and replace outdated
        manager.remember("User prefers dark mode for all apps", target="user")

        # Only the latest should exist
        results = manager.recall("dark mode")
        assert len(results) >= 1
        # Newer content should be present
        assert any("all apps" in r.content for r in results)
