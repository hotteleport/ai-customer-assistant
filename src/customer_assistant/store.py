"""SQLite storage. Every conversation query is scoped to one user."""

import sqlite3
import threading
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, role TEXT NOT NULL,
    text TEXT NOT NULL, created_at REAL NOT NULL, summarised INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS messages_user ON messages(user_id, id);
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY, summary TEXT NOT NULL DEFAULT '', opted_out INTEGER NOT NULL DEFAULT 0,
    converted INTEGER NOT NULL DEFAULT 0, followed_up_at REAL
);
CREATE TABLE IF NOT EXISTS usage (user_id INTEGER, day TEXT, count INTEGER NOT NULL, PRIMARY KEY (user_id, day));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class Store:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False)
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(_SCHEMA)

    def _write(self, sql: str, params: tuple = ()) -> None:
        with self._lock, self._db:
            self._db.execute(sql, params)

    def _read(self, sql: str, params: tuple = ()) -> list:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def get_offset(self) -> Optional[int]:
        rows = self._read("SELECT value FROM meta WHERE key = 'offset'")
        return int(rows[0][0]) if rows else None

    def set_offset(self, offset: int) -> None:
        self._write("INSERT OR REPLACE INTO meta (key, value) VALUES ('offset', ?)", (str(offset),))

    def add_message(self, user_id: int, role: str, text: str, now: float) -> None:
        self._write("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        self._write(
            "INSERT INTO messages (user_id, role, text, created_at) VALUES (?, ?, ?, ?)",
            (user_id, role, text, now),
        )

    def recent(self, user_id: int, limit: int = 6) -> list:
        rows = self._read(
            "SELECT role, text FROM messages WHERE user_id = ? AND summarised = 0 ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        return list(reversed(rows))

    def summary(self, user_id: int) -> str:
        rows = self._read("SELECT summary FROM users WHERE user_id = ?", (user_id,))
        return rows[0][0] if rows else ""

    def messages_to_summarise(self, user_id: int, keep: int = 6) -> list:
        """Everything unsummarised except the newest `keep` messages."""
        rows = self._read(
            "SELECT id, role, text FROM messages WHERE user_id = ? AND summarised = 0 ORDER BY id",
            (user_id,),
        )
        return rows[:-keep] if len(rows) > keep else []

    def apply_summary(self, user_id: int, summary: str, last_id: int) -> None:
        with self._lock, self._db:
            self._db.execute("UPDATE users SET summary = ? WHERE user_id = ?", (summary, user_id))
            self._db.execute(
                "UPDATE messages SET summarised = 1 WHERE user_id = ? AND id <= ?", (user_id, last_id)
            )

    def count_usage(self, user_id: int, day: str, amount: int = 1) -> int:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO usage (user_id, day, count) VALUES (?, ?, ?) "
                "ON CONFLICT(user_id, day) DO UPDATE SET count = count + excluded.count",
                (user_id, day, amount),
            )
            return self._db.execute(
                "SELECT count FROM usage WHERE user_id = ? AND day = ?", (user_id, day)
            ).fetchone()[0]

    def set_opted_out(self, user_id: int, value: bool = True) -> None:
        self._write("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        self._write("UPDATE users SET opted_out = ? WHERE user_id = ?", (int(value), user_id))

    def set_converted(self, user_id: int) -> None:
        self._write("INSERT OR IGNORE INTO users (user_id) VALUES (?)", (user_id,))
        self._write("UPDATE users SET converted = 1 WHERE user_id = ?", (user_id,))

    def follow_up_candidates(self, older_than: float, limit: int) -> list:
        """Customers who went quiet, never bought, never opted out and were never followed up."""
        rows = self._read(
            "SELECT u.user_id FROM users u JOIN messages m ON m.user_id = u.user_id "
            "WHERE u.opted_out = 0 AND u.converted = 0 AND u.followed_up_at IS NULL "
            "GROUP BY u.user_id HAVING MAX(m.created_at) <= ? ORDER BY MAX(m.created_at) LIMIT ?",
            (older_than, limit),
        )
        return [row[0] for row in rows]

    def mark_followed_up(self, user_id: int, now: float) -> None:
        self._write("UPDATE users SET followed_up_at = ? WHERE user_id = ?", (now, user_id))
