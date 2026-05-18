"""SQLite-backed memory store with FTS5 full-text search.

Stores durable facts about users, projects, and environments.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from aether.platform import get_data_dir


MemoryTarget = Literal["user", "project", "environment"]


@dataclass
class MemoryRecord:
    """A single memory entry."""
    id: str
    user_id: str
    target: MemoryTarget
    content: str
    tags: list[str]
    importance: float
    access_count: int
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "target": self.target,
            "content": self.content,
            "tags": self.tags,
            "importance": self.importance,
            "access_count": self.access_count,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }


@dataclass
class MemoryConflict:
    """Detected conflict between two memories."""
    type: Literal["contradiction", "overlap", "outdated"]
    memory_a: MemoryRecord
    memory_b: MemoryRecord
    resolution: Literal["keep_both", "keep_newer", "ask_user", "merge"] = "ask_user"


class SQLiteMemoryStore:
    """SQLite-backed persistent memory store with FTS5 search.

    Schema:
      memories(id, user_id, target, content, tags_json, importance,
               access_count, created_at, updated_at, expires_at)
      memories_fts(content)  -- FTS5 virtual table
    """

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL DEFAULT 'default',
        target TEXT NOT NULL DEFAULT 'user',
        content TEXT NOT NULL,
        tags_json TEXT DEFAULT '[]',
        importance REAL DEFAULT 0.5,
        access_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT
    );

    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content,
        content='memories',
        content_rowid='rowid'
    );

    -- Triggers to keep FTS in sync
    CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
        INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
    END;

    CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
    END;

    CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
        INSERT INTO memories_fts(memories_fts, rowid, content) VALUES ('delete', old.rowid, old.content);
        INSERT INTO memories_fts(rowid, content) VALUES (new.rowid, new.content);
    END;

    CREATE INDEX IF NOT EXISTS idx_memories_user ON memories(user_id);
    CREATE INDEX IF NOT EXISTS idx_memories_target ON memories(target);
    CREATE INDEX IF NOT EXISTS idx_memories_importance ON memories(importance DESC);
    CREATE INDEX IF NOT EXISTS idx_memories_updated ON memories(updated_at DESC);
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = get_data_dir() / "memory.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.executescript(self.SCHEMA)
            self._conn.commit()
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ═══════════════════════════════════════════════════
    # CRUD
    # ═══════════════════════════════════════════════════

    def add(
        self,
        content: str,
        target: MemoryTarget = "user",
        tags: list[str] | None = None,
        importance: float = 0.5,
        user_id: str = "default",
    ) -> MemoryRecord:
        """Add a memory entry."""
        now = datetime.now(timezone.utc).isoformat()
        record = MemoryRecord(
            id=uuid.uuid4().hex[:16],
            user_id=user_id,
            target=target,
            content=content,
            tags=tags or [],
            importance=importance,
            access_count=0,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self.conn.execute(
            """INSERT INTO memories (id, user_id, target, content, tags_json,
               importance, access_count, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record.id, record.user_id, record.target, record.content,
                json.dumps(record.tags), record.importance, record.access_count,
                record.created_at.isoformat(), record.updated_at.isoformat(),
            ),
        )
        self.conn.commit()
        return record

    def get(self, memory_id: str) -> MemoryRecord | None:
        """Get a memory by ID."""
        row = self.conn.execute(
            "SELECT * FROM memories WHERE id = ?", (memory_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_record(row)

    def update(self, memory_id: str, content: str) -> MemoryRecord | None:
        """Update memory content."""
        now = datetime.now(timezone.utc).isoformat()
        self.conn.execute(
            "UPDATE memories SET content = ?, updated_at = ? WHERE id = ?",
            (content, now, memory_id),
        )
        self.conn.commit()
        return self.get(memory_id)

    def delete(self, memory_id: str) -> bool:
        """Delete a memory."""
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def list_all(
        self,
        target: MemoryTarget | None = None,
        user_id: str = "default",
        limit: int = 100,
    ) -> list[MemoryRecord]:
        """List all memories, optionally filtered."""
        if target:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND target = ? "
                "ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (user_id, target, limit),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM memories WHERE user_id = ? "
                "ORDER BY importance DESC, updated_at DESC LIMIT ?",
                (user_id, limit),
            ).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ═══════════════════════════════════════════════════
    # Search
    # ═══════════════════════════════════════════════════

    def search_keyword(
        self,
        query: str,
        user_id: str = "default",
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """Full-text keyword search via FTS5."""
        rows = self.conn.execute(
            """SELECT m.* FROM memories m
               JOIN memories_fts fts ON m.rowid = fts.rowid
               WHERE memories_fts MATCH ? AND m.user_id = ?
               ORDER BY rank
               LIMIT ?""",
            (query, user_id, limit),
        ).fetchall()
        results = [self._row_to_record(r) for r in rows]

        # Update access count
        for r in results:
            self.conn.execute(
                "UPDATE memories SET access_count = access_count + 1 WHERE id = ?",
                (r.id,),
            )
        self.conn.commit()
        return results

    def search_by_tag(
        self,
        tag: str,
        user_id: str = "default",
        limit: int = 20,
    ) -> list[MemoryRecord]:
        """Search by tag."""
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE user_id = ? AND tags_json LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, f"%{tag}%", limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def search_recent(
        self,
        user_id: str = "default",
        limit: int = 10,
    ) -> list[MemoryRecord]:
        """Get most recently updated memories."""
        rows = self.conn.execute(
            "SELECT * FROM memories WHERE user_id = ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    # ═══════════════════════════════════════════════════
    # Capacity & eviction
    # ═══════════════════════════════════════════════════

    def count(self, user_id: str = "default") -> int:
        """Count total memories."""
        row = self.conn.execute(
            "SELECT COUNT(*) as cnt FROM memories WHERE user_id = ?",
            (user_id,),
        ).fetchone()
        return row["cnt"] if row else 0

    def count_by_target(self, user_id: str = "default") -> dict[str, int]:
        """Count memories by target type."""
        rows = self.conn.execute(
            "SELECT target, COUNT(*) as cnt FROM memories WHERE user_id = ? GROUP BY target",
            (user_id,),
        ).fetchall()
        return {r["target"]: r["cnt"] for r in rows}

    def evict(
        self,
        max_entries: int,
        user_id: str = "default",
    ) -> int:
        """Evict least important / least accessed memories to stay under max_entries."""
        current = self.count(user_id)
        if current <= max_entries:
            return 0

        to_remove = current - max_entries
        total_removed = 0

        # First, remove expired
        now = datetime.now(timezone.utc).isoformat()
        cur = self.conn.execute(
            "DELETE FROM memories WHERE user_id = ? AND expires_at IS NOT NULL AND expires_at < ?",
            (user_id, now),
        )
        total_removed += cur.rowcount

        # Re-count
        current = self.count(user_id)
        remaining_to_remove = current - max_entries
        if remaining_to_remove <= 0:
            self.conn.commit()
            return total_removed

        # Remove by (importance * 0.7 - age * 0.3) ascending
        cur = self.conn.execute(
            """DELETE FROM memories WHERE id IN (
               SELECT id FROM memories WHERE user_id = ?
               ORDER BY (importance * 0.7 - (julianday('now') - julianday(updated_at)) * 0.01) ASC
               LIMIT ?
            )""",
            (user_id, remaining_to_remove),
        )
        total_removed += cur.rowcount
        self.conn.commit()
        return total_removed

    # ═══════════════════════════════════════════════════
    # Conflict detection
    # ═══════════════════════════════════════════════════

    def find_conflicts(self, content: str, user_id: str = "default") -> list[MemoryConflict]:
        """Find potentially conflicting memories for a new/updated entry."""
        # Find similar memories by keyword overlap
        words = set(content.lower().split())
        if len(words) < 3:
            return []

        all_memories = self.list_all(user_id=user_id, limit=200)
        conflicts = []

        for mem in all_memories:
            mem_words = set(mem.content.lower().split())
            if not mem_words:
                continue

            # Jaccard similarity
            intersection = words & mem_words
            union = words | mem_words
            similarity = len(intersection) / len(union) if union else 0

            if similarity > 0.4:
                conflict_type = "overlap"
                if similarity > 0.7:
                    conflict_type = "contradiction"
                # Check if one is strictly newer
                if mem.updated_at < datetime.now(timezone.utc):
                    conflict_type = "outdated"

                conflicts.append(MemoryConflict(
                    type=conflict_type,
                    memory_a=MemoryRecord(
                        id="new", user_id=user_id, target="user",
                        content=content, tags=[], importance=0.5,
                        access_count=0,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                    ),
                    memory_b=mem,
                ))

        return conflicts[:5]  # Top 5 conflicts

    # ═══════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════

    def _row_to_record(self, row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            user_id=row["user_id"],
            target=row["target"],
            content=row["content"],
            tags=json.loads(row["tags_json"]),
            importance=row["importance"],
            access_count=row["access_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        )
