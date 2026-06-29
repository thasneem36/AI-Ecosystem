"""Persistent conversation memory backed by SQLite (built-in sqlite3).

Each conversation turn is one row.  A session_id groups turns that belong to
the same interactive session.  user_id ties every row to the account that
created it so users never see each other's data.

Schema
------
conversations
    id            TEXT  PRIMARY KEY   — UUID per turn
    session_id    TEXT  NOT NULL      — groups turns into a session
    user_id       INTEGER             — FK to accounts.id (NULL = bootstrap)
    timestamp     TEXT  NOT NULL      — ISO-8601
    model         TEXT                — backend used ("groq", "ollama", …)
    user_message  TEXT                — the raw user input for this turn
    preview       TEXT                — first 80 chars, for list views
    route         TEXT                — "chat" | "task" | "code" | "learn"
    messages_json TEXT                — JSON-encoded list of agent message dicts
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from config.settings import settings


class MemoryManager:
    def __init__(self, db_file: Optional[Path] = None) -> None:
        self.db_file: Path = Path(db_file) if db_file else settings.DB_FILE
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()
        self._migrate_json()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        # Same WAL/timeout settings as auth_manager._conn() — both managers write
        # to the same koottam.db file with separate Python locks, so WAL mode is
        # required to prevent SQLite file-level write-lock contention between them.
        conn = sqlite3.connect(str(self.db_file), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=9000")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id            TEXT PRIMARY KEY,
                    session_id    TEXT NOT NULL,
                    user_id       INTEGER,
                    timestamp     TEXT NOT NULL,
                    model         TEXT,
                    user_message  TEXT,
                    preview       TEXT,
                    route         TEXT,
                    messages_json TEXT
                )
            """)
            # Migrate existing databases that don't have the user_id column yet.
            try:
                conn.execute("ALTER TABLE conversations ADD COLUMN user_id INTEGER")
            except Exception:
                pass  # Column already exists — ignore
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session ON conversations (session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ts ON conversations (timestamp)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_user ON conversations (user_id)"
            )

    def _migrate_json(self) -> None:
        """Import records from legacy memory.json into SQLite exactly once."""
        json_file = settings.MEMORY_FILE
        if not json_file.exists():
            return
        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                return
            with self._lock, self._conn() as conn:
                existing = conn.execute(
                    "SELECT COUNT(*) FROM conversations"
                ).fetchone()[0]
                if existing > 0:
                    return  # already migrated
                for rec in data:
                    conn.execute(
                        "INSERT OR IGNORE INTO conversations "
                        "(id, session_id, user_id, timestamp, model, user_message, preview, route, messages_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (
                            rec.get("id") or str(uuid.uuid4()),
                            "migrated",
                            None,
                            rec.get("timestamp") or datetime.now().isoformat(),
                            rec.get("model", ""),
                            rec.get("user_message", ""),
                            rec.get("preview", rec.get("user_message", ""))[:80],
                            rec.get("route", ""),
                            json.dumps(rec.get("messages", []), ensure_ascii=False),
                        ),
                    )
        except Exception:
            pass

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["messages"] = json.loads(d.pop("messages_json") or "[]")
        return d

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def save_conversation(
        self,
        user_message: str,
        messages: List[Dict[str, Any]],
        model: str = "ollama",
        session_id: Optional[str] = None,
        route: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Persist one conversation turn and return the record dict."""
        record_id = str(uuid.uuid4())
        ts = datetime.now().isoformat()
        sid = session_id or str(uuid.uuid4())
        record: Dict[str, Any] = {
            "id": record_id,
            "session_id": sid,
            "user_id": user_id,
            "timestamp": ts,
            "model": model,
            "user_message": user_message,
            "preview": user_message[:80],
            "route": route or "",
            "messages": messages,
        }
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations "
                "(id, session_id, user_id, timestamp, model, user_message, preview, route, messages_json) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    sid,
                    user_id,
                    ts,
                    model,
                    user_message,
                    user_message[:80],
                    route or "",
                    json.dumps(messages, ensure_ascii=False),
                ),
            )
        return record

    def get_history(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return conversations newest-first.

        Pass session_id to limit to one session.
        Pass user_id to restrict to one user's data (omit for admin/all).
        """
        where_parts: List[str] = []
        params: List[Any] = []
        if session_id:
            where_parts.append("session_id = ?")
            params.append(session_id)
        if user_id is not None:
            where_parts.append("user_id = ?")
            params.append(user_id)
        where = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""
        with self._conn() as conn:
            rows = conn.execute(
                f"SELECT * FROM conversations {where} ORDER BY timestamp DESC",
                params,
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Return a single turn by its UUID, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def get_sessions(
        self,
        user_id: Optional[int] = None,
        limit: int = 60,
    ) -> List[Dict[str, Any]]:
        """Return one entry per session for the sidebar history list.

        Each entry: {session_id, title, last_ts, turn_count}.
        Pass user_id to scope to one user; omit for admin/all.
        """
        where = "WHERE session_id != 'migrated'"
        params: List[Any] = []
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(user_id)

        with self._conn() as conn:
            rows = conn.execute(
                f"""
                SELECT session_id, MAX(timestamp) AS last_ts, COUNT(*) AS turn_count
                FROM   conversations
                {where}
                GROUP  BY session_id
                ORDER  BY last_ts DESC
                LIMIT  ?
                """,
                params + [limit],
            ).fetchall()

            result: List[Dict[str, Any]] = []
            for row in rows:
                first = conn.execute(
                    "SELECT preview, user_message FROM conversations "
                    "WHERE session_id = ? ORDER BY timestamp ASC LIMIT 1",
                    (row["session_id"],),
                ).fetchone()
                if first:
                    title = (first["preview"] or first["user_message"] or "").strip()[:60]
                else:
                    title = ""
                result.append({
                    "session_id": row["session_id"],
                    "title": title or "Conversation",
                    "last_ts": row["last_ts"],
                    "turn_count": row["turn_count"],
                })
        return result

    def count(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> int:
        """Total stored turns (optionally scoped to one session or user)."""
        with self._conn() as conn:
            if session_id:
                return conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
            if user_id is not None:
                return conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE user_id=?",
                    (user_id,),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]

    def clear(
        self,
        session_id: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> None:
        """Delete conversations.

        session_id → delete only that session's turns.
        user_id    → delete all of that user's turns.
        neither    → delete everything (admin).
        """
        with self._lock, self._conn() as conn:
            if session_id:
                conn.execute(
                    "DELETE FROM conversations WHERE session_id=?", (session_id,)
                )
            elif user_id is not None:
                conn.execute(
                    "DELETE FROM conversations WHERE user_id=?", (user_id,)
                )
            else:
                conn.execute("DELETE FROM conversations")

    def get_recent_sessions(
        self,
        limit: int = 8,
        exclude_session: Optional[str] = None,
        user_id: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return the most recent past sessions for topic-bridge matching.

        Each item: {session_id, first_message, user_messages: [str, ...]}
        Scoped to user_id when provided so bridges never cross user boundaries.
        """
        exclude = exclude_session or "\x00"
        where = "WHERE session_id != ? AND session_id != 'migrated'"
        params: List[Any] = [exclude]
        if user_id is not None:
            where += " AND user_id = ?"
            params.append(user_id)

        with self._conn() as conn:
            session_ids: List[str] = [
                row["session_id"]
                for row in conn.execute(
                    f"""
                    SELECT session_id
                    FROM   conversations
                    {where}
                    GROUP  BY session_id
                    ORDER  BY MAX(timestamp) DESC
                    LIMIT  ?
                    """,
                    params + [limit],
                ).fetchall()
            ]
            if not session_ids:
                return []

            placeholders = ",".join("?" * len(session_ids))
            rows = conn.execute(
                f"""
                SELECT session_id, user_message
                FROM   conversations
                WHERE  session_id IN ({placeholders})
                ORDER  BY session_id, timestamp ASC
                """,
                session_ids,
            ).fetchall()

        from collections import defaultdict
        msgs: Dict[str, List[str]] = defaultdict(list)
        for row in rows:
            if row["user_message"]:
                msgs[row["session_id"]].append(row["user_message"])

        return [
            {
                "session_id": sid,
                "first_message": msgs[sid][0] if msgs[sid] else "",
                "user_messages": msgs[sid],
            }
            for sid in session_ids
        ]


# Shared singleton used by server.py and run_terminal.py
memory_manager = MemoryManager()
