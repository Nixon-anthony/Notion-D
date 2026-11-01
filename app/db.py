"""Tiny local SQLite store: duplicate-webhook guard and the last action (for undo/move).

Your diary itself lives in Notion; nothing you write is kept here except the text of
the most recent message, so it can be moved to another list.
"""
from __future__ import annotations

import json
import sqlite3
import threading


class Database:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        with self._conn:
            self._conn.execute("CREATE TABLE IF NOT EXISTS events (id TEXT PRIMARY KEY)")
            self._conn.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)")

    def mark_event_processed(self, event_id: str) -> bool:
        """True the first time an event id is seen, False for LINE's redeliveries."""
        with self._lock, self._conn:
            cur = self._conn.execute("INSERT OR IGNORE INTO events (id) VALUES (?)", (event_id,))
            self._conn.execute(
                "DELETE FROM events WHERE rowid < (SELECT MAX(rowid) - 1000 FROM events)")
            return cur.rowcount == 1

    def get(self, key: str):
        with self._lock:
            row = self._conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else None

    def set(self, key: str, value) -> None:
        with self._lock, self._conn:
            if value is None:
                self._conn.execute("DELETE FROM kv WHERE key = ?", (key,))
            else:
                self._conn.execute("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)",
                                   (key, json.dumps(value)))

    def claim(self, key: str) -> bool:
        """True only the first time `key` is claimed."""
        with self._lock, self._conn:
            cur = self._conn.execute("INSERT OR IGNORE INTO kv (key, value) VALUES (?, '1')", (key,))
            return cur.rowcount == 1
