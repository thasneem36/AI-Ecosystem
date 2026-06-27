"""Authentication and per-account usage management for Koottam.

Storage: same SQLite file as memory_manager (memory/koottam.db).
No third-party auth deps — stdlib only: hashlib, secrets, sqlite3.

Tables added to koottam.db:
  accounts  — credentials, role, per-account budget config
  sessions  — bearer tokens (30-day validity)
  usage     — rolling-window call/token counters, reset per account's window
"""
from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from config.settings import settings

SESSION_DAYS = 30


class AuthManager:
    def __init__(self, db_file: Optional[Path] = None) -> None:
        self.db_file: Path = Path(db_file) if db_file else settings.DB_FILE
        self.db_file.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_file), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    username      TEXT    UNIQUE NOT NULL,
                    password_hash TEXT    NOT NULL,
                    role          TEXT    NOT NULL DEFAULT 'user',
                    token_limit   INTEGER NOT NULL DEFAULT 5000,
                    reset_hours   REAL    NOT NULL DEFAULT 3.0,
                    is_active     INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    token       TEXT    PRIMARY KEY,
                    account_id  INTEGER NOT NULL,
                    expires_at  TEXT    NOT NULL,
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage (
                    account_id    INTEGER PRIMARY KEY,
                    tokens_used   INTEGER NOT NULL DEFAULT 0,
                    api_calls     INTEGER NOT NULL DEFAULT 0,
                    context_bytes INTEGER NOT NULL DEFAULT 0,
                    last_active   TEXT,
                    reset_at      TEXT,
                    FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
                )
            """)

    # ------------------------------------------------------------------ #
    # Password hashing — PBKDF2-HMAC-SHA256, stdlib only
    # ------------------------------------------------------------------ #

    @staticmethod
    def _hash_password(password: str) -> str:
        salt = secrets.token_hex(16)
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return f"{salt}:{dk.hex()}"

    @staticmethod
    def _verify_password(password: str, stored: str) -> bool:
        try:
            salt, dk_hex = stored.split(":", 1)
        except ValueError:
            return False
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 260_000)
        return secrets.compare_digest(dk.hex(), dk_hex)

    # ------------------------------------------------------------------ #
    # Account management
    # ------------------------------------------------------------------ #

    def has_accounts(self) -> bool:
        with self._conn() as conn:
            return conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] > 0

    def create_account(
        self,
        username: str,
        password: str,
        role: str = "user",
        token_limit: int = 5000,
        reset_hours: float = 3.0,
    ) -> Dict[str, Any]:
        """Create a new account. Raises ValueError on duplicate username."""
        ph = self._hash_password(password)
        with self._lock, self._conn() as conn:
            try:
                conn.execute(
                    "INSERT INTO accounts (username, password_hash, role, token_limit, reset_hours)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (username, ph, role, token_limit, reset_hours),
                )
                row = conn.execute(
                    "SELECT * FROM accounts WHERE username = ?", (username,)
                ).fetchone()
                conn.execute(
                    "INSERT OR IGNORE INTO usage (account_id) VALUES (?)", (row["id"],)
                )
                return dict(row)
            except sqlite3.IntegrityError:
                raise ValueError(f"Username '{username}' already exists.")

    def get_accounts(self) -> List[Dict[str, Any]]:
        """All accounts with their current usage (LEFT JOIN on usage)."""
        with self._conn() as conn:
            rows = conn.execute("""
                SELECT a.id, a.username, a.role, a.token_limit, a.reset_hours,
                       a.is_active, a.created_at,
                       COALESCE(u.tokens_used,   0) AS tokens_used,
                       COALESCE(u.api_calls,      0) AS api_calls,
                       COALESCE(u.context_bytes,  0) AS context_bytes,
                       u.last_active, u.reset_at
                FROM accounts a
                LEFT JOIN usage u ON u.account_id = a.id
                ORDER BY a.id
            """).fetchall()
        return [dict(r) for r in rows]

    def update_account(
        self,
        account_id: int,
        *,
        is_active: Optional[int] = None,
        token_limit: Optional[int] = None,
        reset_hours: Optional[float] = None,
        role: Optional[str] = None,
        password: Optional[str] = None,
    ) -> bool:
        """Update one or more account fields. Returns True if found."""
        fields: List[str] = []
        vals: List[Any] = []
        if is_active is not None:
            fields.append("is_active = ?"); vals.append(is_active)
        if token_limit is not None:
            fields.append("token_limit = ?"); vals.append(token_limit)
        if reset_hours is not None:
            fields.append("reset_hours = ?"); vals.append(reset_hours)
        if role is not None:
            fields.append("role = ?"); vals.append(role)
        if password is not None:
            fields.append("password_hash = ?"); vals.append(self._hash_password(password))
        if not fields:
            return True
        vals.append(account_id)
        with self._lock, self._conn() as conn:
            cur = conn.execute(
                f"UPDATE accounts SET {', '.join(fields)} WHERE id = ?", vals
            )
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # Authentication
    # ------------------------------------------------------------------ #

    def login(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify credentials, issue a session token. Returns None on failure."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE username = ?", (username,)
            ).fetchone()
        if not row or not row["is_active"]:
            return None
        if not self._verify_password(password, row["password_hash"]):
            return None

        token = secrets.token_urlsafe(32)
        expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT INTO sessions (token, account_id, expires_at) VALUES (?, ?, ?)",
                (token, row["id"], expires),
            )
            conn.execute(
                "INSERT OR IGNORE INTO usage (account_id) VALUES (?)", (row["id"],)
            )
        return {
            "token": token,
            "account_id": row["id"],
            "username": row["username"],
            "role": row["role"],
            "token_limit": row["token_limit"],
            "reset_hours": row["reset_hours"],
            "expires_at": expires,
        }

    def logout(self, token: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (token,))

    def verify_session(self, token: str) -> Optional[Dict[str, Any]]:
        """Return account info if the token is valid and not expired; None otherwise."""
        now = datetime.now(timezone.utc).isoformat()
        with self._conn() as conn:
            row = conn.execute("""
                SELECT a.id, a.username, a.role, a.token_limit, a.reset_hours, a.is_active
                FROM sessions s
                JOIN accounts a ON a.id = s.account_id
                WHERE s.token = ? AND s.expires_at > ? AND a.is_active = 1
            """, (token, now)).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------ #
    # Usage tracking — rolling window per account
    # ------------------------------------------------------------------ #

    def check_limit(self, account_id: int) -> Dict[str, Any]:
        """Return budget status for this account. Resets the window when expired."""
        now = datetime.now(timezone.utc)
        with self._lock, self._conn() as conn:
            acct = conn.execute(
                "SELECT token_limit, reset_hours FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            if not acct:
                return {"allowed": False, "tokens_used": 0, "token_limit": 0, "resets_in_seconds": 0}

            token_limit = acct["token_limit"]
            reset_hours = acct["reset_hours"]

            urow = conn.execute(
                "SELECT tokens_used, reset_at FROM usage WHERE account_id = ?", (account_id,)
            ).fetchone()

            tokens_used = urow["tokens_used"] if urow else 0
            reset_at_str: Optional[str] = urow["reset_at"] if urow else None

            if reset_at_str:
                try:
                    reset_at = datetime.fromisoformat(reset_at_str)
                    if reset_at.tzinfo is None:
                        reset_at = reset_at.replace(tzinfo=timezone.utc)
                    if now >= reset_at:
                        new_reset = (now + timedelta(hours=reset_hours)).isoformat()
                        conn.execute(
                            "UPDATE usage SET tokens_used=0, api_calls=0, reset_at=?"
                            " WHERE account_id=?",
                            (new_reset, account_id),
                        )
                        tokens_used = 0
                        reset_at_str = new_reset
                except (ValueError, TypeError):
                    pass
            else:
                new_reset = (now + timedelta(hours=reset_hours)).isoformat()
                conn.execute("""
                    INSERT INTO usage (account_id, tokens_used, api_calls, reset_at)
                    VALUES (?, 0, 0, ?)
                    ON CONFLICT(account_id) DO UPDATE SET reset_at = excluded.reset_at
                """, (account_id, new_reset))
                reset_at_str = new_reset

        try:
            reset_at = datetime.fromisoformat(reset_at_str)
            if reset_at.tzinfo is None:
                reset_at = reset_at.replace(tzinfo=timezone.utc)
            resets_in = max(0.0, (reset_at - now).total_seconds())
        except (ValueError, TypeError):
            resets_in = 0.0

        return {
            "allowed": tokens_used < token_limit,
            "tokens_used": tokens_used,
            "token_limit": token_limit,
            "resets_in_seconds": resets_in,
        }

    def record_usage(
        self,
        account_id: int,
        tokens: int = 0,
        api_calls: int = 0,
        context_bytes: int = 0,
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO usage (account_id, tokens_used, api_calls, context_bytes, last_active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(account_id) DO UPDATE SET
                    tokens_used   = tokens_used   + excluded.tokens_used,
                    api_calls     = api_calls     + excluded.api_calls,
                    context_bytes = context_bytes + excluded.context_bytes,
                    last_active   = excluded.last_active
            """, (account_id, tokens, api_calls, context_bytes, now))

    def get_usage_stats(self, account_id: int) -> Dict[str, Any]:
        """Read-only usage snapshot; does NOT trigger a window reset."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT tokens_used, api_calls, context_bytes, last_active, reset_at"
                " FROM usage WHERE account_id = ?", (account_id,)
            ).fetchone()
        if not row:
            return {"tokens_used": 0, "api_calls": 0, "context_bytes": 0,
                    "last_active": None, "reset_at": None}
        return dict(row)

    def reset_usage(self, account_id: int) -> None:
        """Manually clear usage counters (admin action)."""
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE usage SET tokens_used=0, api_calls=0 WHERE account_id=?",
                (account_id,),
            )


auth_manager = AuthManager()
