"""Persistent conversation memory backed by SQLite (built-in sqlite3).

Each conversation turn is one row.  A session_id groups turns that belong to
the same interactive session (one terminal run, one browser tab) so history
and continuation logic never bleed between separate conversations.

Schema
------
conversations
    id            TEXT  PRIMARY KEY   — UUID per turn
    session_id    TEXT  NOT NULL      — groups turns into a session
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
        self._migrate_json()  # one-time import from legacy memory.json

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_file), check_same_thread=False)
        conn.row_factory = sqlite3.Row
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
                    timestamp     TEXT NOT NULL,
                    model         TEXT,
                    user_message  TEXT,
                    preview       TEXT,
                    route         TEXT,
                    messages_json TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session ON conversations (session_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_ts ON conversations (timestamp)"
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
                    return  # already migrated — don't import twice
                for rec in data:
                    conn.execute(
                        "INSERT OR IGNORE INTO conversations VALUES (?,?,?,?,?,?,?,?)",
                        (
                            rec.get("id") or str(uuid.uuid4()),
                            "migrated",
                            rec.get("timestamp") or datetime.now().isoformat(),
                            rec.get("model", ""),
                            rec.get("user_message", ""),
                            rec.get("preview", rec.get("user_message", ""))[:80],
                            rec.get("route", ""),
                            json.dumps(rec.get("messages", []), ensure_ascii=False),
                        ),
                    )
        except Exception:
            pass  # best-effort — never crash on migration failure

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
        d = dict(row)
        d["messages"] = json.loads(d.pop("messages_json") or "[]")
        return d

    # ------------------------------------------------------------------ #
    # Public API  (same signatures as the old JSON version + session_id)
    # ------------------------------------------------------------------ #

    def save_conversation(
        self,
        user_message: str,
        messages: List[Dict[str, Any]],
        model: str = "ollama",
        session_id: Optional[str] = None,
        route: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Persist one conversation turn and return the record dict."""
        record_id = str(uuid.uuid4())
        ts = datetime.now().isoformat()
        sid = session_id or str(uuid.uuid4())
        record: Dict[str, Any] = {
            "id": record_id,
            "session_id": sid,
            "timestamp": ts,
            "model": model,
            "user_message": user_message,
            "preview": user_message[:80],
            "route": route or "",
            "messages": messages,
        }
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO conversations VALUES (?,?,?,?,?,?,?,?)",
                (
                    record_id,
                    sid,
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
        self, session_id: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """Return conversations newest-first.

        Pass session_id to limit to one session; omit for the full table.
        """
        with self._conn() as conn:
            if session_id:
                rows = conn.execute(
                    "SELECT * FROM conversations WHERE session_id=? "
                    "ORDER BY timestamp DESC",
                    (session_id,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM conversations ORDER BY timestamp DESC"
                ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def get_conversation(self, conversation_id: str) -> Optional[Dict[str, Any]]:
        """Return a single turn by its UUID, or None."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM conversations WHERE id=?", (conversation_id,)
            ).fetchone()
        return self._row_to_dict(row) if row else None

    def count(self, session_id: Optional[str] = None) -> int:
        """Total stored turns (optionally scoped to one session)."""
        with self._conn() as conn:
            if session_id:
                return conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE session_id=?",
                    (session_id,),
                ).fetchone()[0]
            return conn.execute(
                "SELECT COUNT(*) FROM conversations"
            ).fetchone()[0]

    def clear(self, session_id: Optional[str] = None) -> None:
        """Delete all turns, or only the turns belonging to one session."""
        with self._lock, self._conn() as conn:
            if session_id:
                conn.execute(
                    "DELETE FROM conversations WHERE session_id=?", (session_id,)
                )
            else:
                conn.execute("DELETE FROM conversations")

    def get_recent_sessions(
        self,
        limit: int = 8,
        exclude_session: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Return the most recent past sessions for topic-bridge matching.

        Each item is:
            {session_id, first_message, user_messages: [str, ...]}

        Excludes the current session and migrated legacy records.
        """
        exclude = exclude_session or "\x00"  # guaranteed not to match any UUID
        with self._conn() as conn:
            session_ids: List[str] = [
                row["session_id"]
                for row in conn.execute(
                    """
                    SELECT session_id
                    FROM   conversations
                    WHERE  session_id != ? AND session_id != 'migrated'
                    GROUP  BY session_id
                    ORDER  BY MAX(timestamp) DESC
                    LIMIT  ?
                    """,
                    (exclude, limit),
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
            for sid in session_ids  # preserves MAX(timestamp) DESC order
        ]


# Shared singleton used by server.py and run_terminal.py
memory_manager = MemoryManager()
