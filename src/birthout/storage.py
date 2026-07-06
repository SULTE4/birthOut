from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from birthout.extractors.rules import normalize_person
from birthout.models import ApprovedBirthday, ImportSession, LeapPolicy
from birthout.sessions import SESSION_STATE_VERSION, deserialize_session, serialize_session


class Storage:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self.connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS imports (
                    content_hash TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    imported_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS birthdays (
                    id TEXT PRIMARY KEY,
                    normalized_name TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL,
                    month INTEGER NOT NULL,
                    day INTEGER NOT NULL,
                    birth_year INTEGER,
                    leap_policy TEXT,
                    source_hash TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS calendar_events (
                    birthday_id TEXT PRIMARY KEY REFERENCES birthdays(id) ON DELETE CASCADE,
                    calendar_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS review_sessions (
                    id TEXT PRIMARY KEY,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    state_version INTEGER NOT NULL,
                    state_json TEXT NOT NULL
                );
                """
            )

    def record_import(self, content_hash: str, filename: str) -> bool:
        with self.connect() as db:
            cursor = db.execute(
                "INSERT OR IGNORE INTO imports(content_hash, filename, imported_at) VALUES (?, ?, ?)",
                (content_hash, filename, datetime.now(UTC).isoformat()),
            )
            return cursor.rowcount == 1

    def save_review_session(self, session: ImportSession) -> None:
        session.updated_at = datetime.now(UTC).isoformat()
        state_json = serialize_session(session)
        with self.connect() as db:
            db.execute(
                """
                INSERT INTO review_sessions(id, filename, created_at, updated_at, state_version, state_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    filename=excluded.filename,
                    updated_at=excluded.updated_at,
                    state_version=excluded.state_version,
                    state_json=excluded.state_json
                """,
                (
                    session.id,
                    session.filename,
                    session.created_at,
                    session.updated_at,
                    SESSION_STATE_VERSION,
                    state_json,
                ),
            )

    def load_review_sessions(self) -> list[ImportSession]:
        with self.connect() as db:
            rows = db.execute(
                "SELECT id, state_version, state_json FROM review_sessions ORDER BY updated_at DESC"
            ).fetchall()
        sessions = []
        for row in rows:
            if row["state_version"] != SESSION_STATE_VERSION:
                continue
            try:
                session = deserialize_session(row["state_json"])
            except ValueError:
                continue
            if session.id == row["id"]:
                sessions.append(session)
        return sessions

    def delete_review_session(self, session_id: str) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM review_sessions WHERE id = ?", (session_id,))

    def upsert_birthday(
        self,
        *,
        display_name: str,
        month: int,
        day: int,
        year: int | None,
        leap_policy: LeapPolicy | None,
        source_hash: str,
    ) -> ApprovedBirthday:
        normalized = normalize_person(display_name)
        now = datetime.now(UTC).isoformat()
        with self.connect() as db:
            current = db.execute("SELECT id FROM birthdays WHERE normalized_name = ?", (normalized,)).fetchone()
            birthday_id = current["id"] if current else uuid4().hex
            db.execute(
                """
                INSERT INTO birthdays(id, normalized_name, display_name, month, day, birth_year, leap_policy, source_hash, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(normalized_name) DO UPDATE SET
                    display_name=excluded.display_name, month=excluded.month, day=excluded.day,
                    birth_year=excluded.birth_year, leap_policy=excluded.leap_policy,
                    source_hash=excluded.source_hash, updated_at=excluded.updated_at
                """,
                (birthday_id, normalized, display_name, month, day, year, leap_policy.value if leap_policy else None, source_hash, now),
            )
        return ApprovedBirthday(birthday_id, display_name, month, day, year, leap_policy)

    def snapshot_birthday(self, display_name: str) -> dict | None:
        normalized = normalize_person(display_name)
        with self.connect() as db:
            row = db.execute("SELECT * FROM birthdays WHERE normalized_name = ?", (normalized,)).fetchone()
        return dict(row) if row else None

    def restore_birthday(self, birthday_id: str, snapshot: dict | None) -> None:
        """Undo an unsynchronized approval while preserving any older birthday record."""
        with self.connect() as db:
            if snapshot is None:
                linked = db.execute(
                    "SELECT 1 FROM calendar_events WHERE birthday_id = ?", (birthday_id,)
                ).fetchone()
                if linked:
                    raise ValueError("A synchronized calendar event cannot be undone from review.")
                db.execute("DELETE FROM birthdays WHERE id = ?", (birthday_id,))
                return
            db.execute(
                """
                UPDATE birthdays SET normalized_name=?, display_name=?, month=?, day=?, birth_year=?,
                    leap_policy=?, source_hash=?, updated_at=? WHERE id=?
                """,
                (
                    snapshot["normalized_name"], snapshot["display_name"], snapshot["month"],
                    snapshot["day"], snapshot["birth_year"], snapshot["leap_policy"],
                    snapshot["source_hash"], snapshot["updated_at"], birthday_id,
                ),
            )

    def list_birthdays(self) -> list[ApprovedBirthday]:
        with self.connect() as db:
            rows = db.execute("SELECT * FROM birthdays ORDER BY month, day, display_name").fetchall()
        return [self._birthday(row) for row in rows]

    def get_birthday(self, birthday_id: str) -> ApprovedBirthday | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM birthdays WHERE id = ?", (birthday_id,)).fetchone()
        return self._birthday(row) if row else None

    @staticmethod
    def _birthday(row: sqlite3.Row) -> ApprovedBirthday:
        policy = LeapPolicy(row["leap_policy"]) if row["leap_policy"] else None
        return ApprovedBirthday(row["id"], row["display_name"], row["month"], row["day"], row["birth_year"], policy)

    def get_event_link(self, birthday_id: str) -> tuple[str, str] | None:
        with self.connect() as db:
            row = db.execute("SELECT calendar_id, event_id FROM calendar_events WHERE birthday_id = ?", (birthday_id,)).fetchone()
        return (row["calendar_id"], row["event_id"]) if row else None

    def set_event_link(self, birthday_id: str, calendar_id: str, event_id: str) -> None:
        with self.connect() as db:
            db.execute(
                """INSERT INTO calendar_events(birthday_id, calendar_id, event_id, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(birthday_id) DO UPDATE SET calendar_id=excluded.calendar_id,
                event_id=excluded.event_id, updated_at=excluded.updated_at""",
                (birthday_id, calendar_id, event_id, datetime.now(UTC).isoformat()),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as db:
            row = db.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as db:
            db.execute(
                "INSERT INTO settings(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
