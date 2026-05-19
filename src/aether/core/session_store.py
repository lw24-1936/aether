"""Session store — persist conversation sessions for cross-session search."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from aether.platform import get_data_dir


class SessionStore:
    """SQLite-backed session persistence for cross-session search."""

    SCHEMA = """
    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT DEFAULT '',
        messages_json TEXT DEFAULT '[]',
        model TEXT DEFAULT '',
        provider TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
    """

    def __init__(self, db_path: str | Path | None = None):
        if db_path is None:
            db_path = get_data_dir() / "sessions.db"
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(self.SCHEMA)
            self._conn.commit()
        return self._conn

    def save_session(self, session_id: str, messages: list[dict], model: str = "", provider: str = "") -> None:
        now = datetime.now(timezone.utc).isoformat()
        title = ""
        for m in messages:
            if m.get("role") == "user":
                title = m.get("content", "")[:60]
                break

        self.conn.execute(
            """INSERT OR REPLACE INTO sessions (id, title, messages_json, model, provider, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM sessions WHERE id=?), ?), ?)""",
            (session_id, title, json.dumps(messages, ensure_ascii=False),
             model, provider, session_id, now, now),
        )
        self.conn.commit()

    def search(self, query: str, limit: int = 5) -> list[dict]:
        query_lower = query.lower()
        rows = self.conn.execute(
            "SELECT * FROM sessions WHERE title LIKE ? OR messages_json LIKE ? ORDER BY updated_at DESC LIMIT ?",
            (f"%{query_lower}%", f"%{query_lower}%", limit),
        ).fetchall()

        results = []
        for row in rows:
            messages = json.loads(row["messages_json"])
            preview = ""
            for m in messages[-3:]:
                c = m.get("content", "")[:100]
                if c:
                    preview += c + " | "
            results.append({
                "id": row["id"],
                "title": row["title"],
                "model": row["model"],
                "updated": row["updated_at"][:16],
                "preview": preview[:200],
            })
        return results

    def list_recent(self, limit: int = 10) -> list[dict]:
        rows = self.conn.execute(
            "SELECT id, title, model, updated_at FROM sessions ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [{"id": r["id"], "title": r["title"], "model": r["model"], "updated": r["updated_at"][:16]} for r in rows]

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
