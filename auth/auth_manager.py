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

# Per-account locks that serialize the check_limit → pipeline → record_usage
# window so two simultaneous requests from the same user can't both pass the
# limit check before either one records its token usage.
_ACCOUNT_LOCKS: Dict[int, threading.Lock] = {}
_ACCOUNT_LOCKS_GUARD = threading.Lock()


def _get_account_lock(account_id: int) -> threading.Lock:
    with _ACCOUNT_LOCKS_GUARD:
        if account_id not in _ACCOUNT_LOCKS:
            _ACCOUNT_LOCKS[account_id] = threading.Lock()
        return _ACCOUNT_LOCKS[account_id]


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
        # timeout=10: retry for up to 10 s if another connection holds a write-lock.
        # WAL mode: readers and writers don't block each other — critical because
        # auth_manager and memory_manager both write to the same koottam.db file
        # using separate Python-level locks, so SQLite file-level locking is the
        # only coordination between them.
        conn = sqlite3.connect(str(self.db_file), check_same_thread=False, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=9000")
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS api_providers (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    name         TEXT    UNIQUE NOT NULL,
                    env_var      TEXT    NOT NULL,
                    model        TEXT    NOT NULL DEFAULT '',
                    key_prefix   TEXT    NOT NULL DEFAULT '',
                    key_tail     TEXT    NOT NULL DEFAULT '',
                    status       TEXT    NOT NULL DEFAULT 'untested',
                    status_msg   TEXT    NOT NULL DEFAULT '',
                    last_tested  TEXT,
                    last_used    TEXT,
                    total_calls  INTEGER NOT NULL DEFAULT 0,
                    total_tokens INTEGER NOT NULL DEFAULT 0,
                    created_at   TEXT    NOT NULL DEFAULT (datetime('now'))
                )
            """)
            # Single-row counter table — id=1 always exists.
            # All writes use SET x = x + N (atomic SQL) so overlapping
            # requests never clobber each other's increments.
            conn.execute("""
                CREATE TABLE IF NOT EXISTS server_stats (
                    id             INTEGER PRIMARY KEY DEFAULT 1,
                    problems_date  TEXT    NOT NULL DEFAULT '',
                    problems_count INTEGER NOT NULL DEFAULT 0,
                    api_calls      INTEGER NOT NULL DEFAULT 0,
                    input_tokens   INTEGER NOT NULL DEFAULT 0,
                    output_tokens  INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("INSERT OR IGNORE INTO server_stats (id) VALUES (1)")

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

    def get_account_lock(self, account_id: int) -> threading.Lock:
        """Return the per-account lock used to serialize limit-check → record_usage."""
        return _get_account_lock(account_id)

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

    def delete_account(self, account_id: int) -> bool:
        """Permanently delete an account and cascade-delete its sessions/usage."""
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
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

    def login_as(self, account_id: int) -> Optional[Dict[str, Any]]:
        """Issue a session token for an account without password verification (admin use only)."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT id, username, role, token_limit, reset_hours, is_active"
                " FROM accounts WHERE id = ?",
                (account_id,),
            ).fetchone()
        if not row or not row["is_active"]:
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

    # ------------------------------------------------------------------ #
    # API provider registry
    # ------------------------------------------------------------------ #

    @staticmethod
    def _mask_key(key: str) -> tuple[str, str]:
        """Return (prefix, tail) for display. Never exposes the full key."""
        if len(key) <= 8:
            return key[:2], key[-2:]
        return key[:6], key[-4:]

    def upsert_provider(
        self, name: str, env_var: str, model: str, key_value: str
    ) -> Dict[str, Any]:
        """Insert or update a provider record (key stored in .env, not here)."""
        prefix, tail = self._mask_key(key_value) if key_value else ("", "")
        with self._lock, self._conn() as conn:
            conn.execute("""
                INSERT INTO api_providers (name, env_var, model, key_prefix, key_tail)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    env_var    = excluded.env_var,
                    model      = excluded.model,
                    key_prefix = excluded.key_prefix,
                    key_tail   = excluded.key_tail,
                    status     = 'untested',
                    status_msg = ''
            """, (name, env_var, model, prefix, tail))
            row = conn.execute(
                "SELECT * FROM api_providers WHERE name = ?", (name,)
            ).fetchone()
        d = dict(row)
        d["key_masked"] = f"{d['key_prefix']}...{d['key_tail']}" if d["key_prefix"] else "(no key)"
        return d

    def get_providers(self) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM api_providers ORDER BY id").fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["key_masked"] = f"{d['key_prefix']}...{d['key_tail']}" if d["key_prefix"] else "(no key)"
            result.append(d)
        return result

    def get_provider(self, provider_id: int) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM api_providers WHERE id = ?", (provider_id,)
            ).fetchone()
        if not row:
            return None
        d = dict(row)
        d["key_masked"] = f"{d['key_prefix']}...{d['key_tail']}" if d["key_prefix"] else "(no key)"
        return d

    def update_provider_status(
        self, provider_id: int, status: str, msg: str = ""
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE api_providers SET status=?, status_msg=?, last_tested=? WHERE id=?",
                (status, msg, now, provider_id),
            )

    def record_provider_usage(self, name: str, calls: int = 0, tokens: int = 0) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE api_providers"
                " SET total_calls=total_calls+?, total_tokens=total_tokens+?, last_used=?"
                " WHERE name=?",
                (calls, tokens, now, name),
            )

    def delete_provider(self, provider_id: int) -> bool:
        with self._lock, self._conn() as conn:
            cur = conn.execute("DELETE FROM api_providers WHERE id=?", (provider_id,))
        return cur.rowcount > 0

    def insert_provider_if_absent(
        self, name: str, env_var: str, model: str, key_value: str
    ) -> None:
        """Import a provider from .env without overwriting an existing record."""
        prefix, tail = self._mask_key(key_value) if key_value else ("", "")
        with self._lock, self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO api_providers (name, env_var, model, key_prefix, key_tail)"
                " VALUES (?, ?, ?, ?, ?)",
                (name, env_var, model, prefix, tail),
            )

    def update_provider_model(self, provider_id: int, model: str) -> None:
        with self._lock, self._conn() as conn:
            conn.execute(
                "UPDATE api_providers SET model=? WHERE id=?",
                (model, provider_id),
            )

    # ------------------------------------------------------------------ #
    # Server-wide atomic counters
    # ------------------------------------------------------------------ #

    def bump_problems_solved(self) -> None:
        """Atomically increment today's problem counter.

        Uses SET x = x + 1 so concurrent calls never clobber each other —
        the DB serialises the write, not a Python read-modify-write in memory.
        Resets automatically when the calendar date rolls over.
        """
        today = datetime.now(timezone.utc).date().isoformat()
        with self._lock, self._conn() as conn:
            conn.execute("""
                UPDATE server_stats SET
                    problems_count = CASE WHEN problems_date = ? THEN problems_count + 1 ELSE 1 END,
                    problems_date  = ?
                WHERE id = 1
            """, (today, today))

    def record_session_stats(
        self,
        api_calls: int = 0,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        """Atomically add to cumulative session counters."""
        with self._lock, self._conn() as conn:
            conn.execute("""
                UPDATE server_stats SET
                    api_calls     = api_calls     + ?,
                    input_tokens  = input_tokens  + ?,
                    output_tokens = output_tokens + ?
                WHERE id = 1
            """, (api_calls, input_tokens, output_tokens))

    def get_server_stats(self) -> Dict[str, Any]:
        """Read current server-wide counters.

        problems_count is 0 when the stored date is not today (resets on
        the next bump_problems_solved call).
        """
        today = datetime.now(timezone.utc).date().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM server_stats WHERE id = 1"
            ).fetchone()
        if not row:
            return {
                "problems_count": 0, "api_calls": 0,
                "input_tokens": 0, "output_tokens": 0,
            }
        d = dict(row)
        if d.get("problems_date") != today:
            d["problems_count"] = 0
        return d


auth_manager = AuthManager()
