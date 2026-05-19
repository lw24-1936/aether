"""Memory Manager — orchestrates SQLite store, vector search, sessions.

Three memory layers:
  1. Short-term (session) — message history (in AgentLoop)
  2. Long-term (persistent) — durable facts (SQLite)
  3. Project — repo-specific context (future: AGENTS.md parsing)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aether.memory.store import SQLiteMemoryStore, MemoryRecord, MemoryConflict, MemoryTarget


class MemoryManager:
    """Central memory orchestrator.

    Usage:
        mgr = MemoryManager()
        mgr.remember("User prefers concise replies", target="user")
        results = mgr.recall("user preferences")
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        max_entries: int = 2000,
        user_id: str = "default",
    ):
        self.store = SQLiteMemoryStore(db_path)
        self.max_entries = max_entries
        self.user_id = user_id
        self._capacity_checked = False

    def close(self) -> None:
        self.store.close()

    # ═══════════════════════════════════════════════════
    # Remember / Recall API
    # ═══════════════════════════════════════════════════

    def remember(
        self,
        content: str,
        target: MemoryTarget = "user",
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MemoryRecord:
        """Save a durable fact to memory.

        Automatically checks capacity and evicts if needed.
        Detects conflicts with existing memories.
        """
        self._ensure_capacity()

        # Check for conflicts
        conflicts = self.store.find_conflicts(content, self.user_id)
        if conflicts:
            # Auto-resolve simple cases
            for c in conflicts:
                if c.type == "outdated":
                    # Replace outdated memory
                    self.store.delete(c.memory_b.id)

        record = self.store.add(
            content=content,
            target=target,
            tags=tags,
            importance=importance,
            user_id=self.user_id,
        )
        return record

    def recall(
        self,
        query: str,
        target: MemoryTarget | None = None,
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """Search memories by keyword (FTS5)."""
        if not query or not query.strip():
            return self.recall_recent(limit)

        results = self.store.search_keyword(query, self.user_id, limit)

        # If no keyword results, try recent
        if not results:
            results = self.store.search_recent(self.user_id, limit)

        if target:
            results = [r for r in results if r.target == target]

        return results[:limit]

    def recall_by_tag(self, tag: str, limit: int = 20) -> list[MemoryRecord]:
        """Find memories by tag."""
        return self.store.search_by_tag(tag, self.user_id, limit)

    def recall_recent(self, limit: int = 10) -> list[MemoryRecord]:
        """Get most recently updated memories."""
        return self.store.search_recent(self.user_id, limit)

    def forget(self, memory_id: str) -> bool:
        """Delete a specific memory."""
        return self.store.delete(memory_id)

    def update_memory(self, memory_id: str, content: str) -> MemoryRecord | None:
        """Update an existing memory."""
        return self.store.update(memory_id, content)

    # ═══════════════════════════════════════════════════
    # Capacity & stats
    # ═══════════════════════════════════════════════════

    def stats(self) -> dict[str, Any]:
        """Get memory statistics."""
        total = self.store.count(self.user_id)
        by_target = self.store.count_by_target(self.user_id)
        return {
            "total_entries": total,
            "max_entries": self.max_entries,
            "usage_percent": round(total / self.max_entries * 100, 1) if self.max_entries else 0,
            "by_target": by_target,
        }

    def _ensure_capacity(self) -> None:
        """Check and enforce capacity limit strictly.
        Evicts down to max_entries-1 to leave room for the new entry.
        """
        count = self.store.count(self.user_id)
        if count >= self.max_entries:
            self.store.evict(max_entries=self.max_entries - 1, user_id=self.user_id)
        self._capacity_checked = True

    # ═══════════════════════════════════════════════════
    # Session search
    # ═══════════════════════════════════════════════════

    def search_sessions(
        self,
        query: str,
        sessions: list[dict[str, Any]] | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search across conversation sessions.

        Args:
            query: Search keywords
            sessions: List of session dicts with id/title/messages
            limit: Max results

        Returns ranked session summaries.
        """
        if not sessions:
            return []

        results = []
        query_lower = query.lower()
        query_words = set(query_lower.split())

        for session in sessions:
            title = (session.get("title") or "").lower()
            messages = session.get("messages", [])
            msg_text = " ".join(
                (m.get("content") or "") for m in messages[-20:]  # Last 20 messages
            ).lower()

            # Score: title match > content match
            score = 0
            if query_lower in title:
                score += 10
            for word in query_words:
                if word in title:
                    score += 3
                if word in msg_text:
                    score += 1

            if score > 0:
                results.append({
                    "session_id": session.get("id", ""),
                    "title": session.get("title", ""),
                    "score": score,
                    "preview": msg_text[:200],
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    # ═══════════════════════════════════════════════════
    # Export / Import
    # ═══════════════════════════════════════════════════

    def export_all(self) -> list[dict[str, Any]]:
        """Export all memories as dicts (for backup/transfer)."""
        records = self.store.list_all(user_id=self.user_id, limit=10000)
        return [r.to_dict() for r in records]

    def import_memories(self, records: list[dict[str, Any]]) -> int:
        """Import memories from exported dicts."""
        count = 0
        for r in records:
            try:
                self.store.add(
                    content=r["content"],
                    target=r.get("target", "user"),
                    tags=r.get("tags", []),
                    importance=r.get("importance", 0.5),
                    user_id=self.user_id,
                )
                count += 1
            except Exception:
                continue
        return count
