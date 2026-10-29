"""SQLite-Persistenz fuer Nutzerprofile, Quiz-Sessions und Haushalts-Einstellungen.

Es wird bewusst nur die Standardbibliothek verwendet: pro Operation eine kurze
Verbindung im WAL-Modus. Das reicht fuer einen Haushalts-Microservice und
vermeidet globale, thread-unsichere Verbindungen.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    ip TEXT,
    providers TEXT NOT NULL DEFAULT '[]',
    taste TEXT NOT NULL DEFAULT '{}',
    liked TEXT NOT NULL DEFAULT '[]',
    disliked TEXT NOT NULL DEFAULT '[]',
    skipped TEXT NOT NULL DEFAULT '[]',
    seen TEXT NOT NULL DEFAULT '[]',
    avatar_url TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_users_name ON users (lower(name));

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS quiz_sessions (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    providers TEXT NOT NULL DEFAULT '[]',
    state TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return default


class Store:
    """Kleiner Repository-Layer um SQLite."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def _db(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------ users

    @staticmethod
    def _user_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "ip": row["ip"],
            "providers": _loads(row["providers"], []),
            "taste": _loads(row["taste"], {}),
            "liked": _loads(row["liked"], []),
            "disliked": _loads(row["disliked"], []),
            "skipped": _loads(row["skipped"], []),
            "seen": _loads(row["seen"], []),
            "avatar_url": row["avatar_url"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def public_user(user: dict[str, Any]) -> dict[str, Any]:
        """Nur unkritische Profilfelder (keine IP-Adresse)."""
        return {
            "id": user["id"],
            "name": user["name"],
            "avatar_url": user["avatar_url"],
            "providers": user["providers"],
            "created_at": user["created_at"],
        }

    def list_users(self) -> list[dict[str, Any]]:
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY lower(name)").fetchall()
        return [self._user_from_row(row) for row in rows]

    def get_user(self, user_id: str) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return self._user_from_row(row) if row else None

    def get_user_by_name(self, name: str) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE lower(name) = lower(?) LIMIT 1", (name.strip(),)
            ).fetchone()
        return self._user_from_row(row) if row else None

    def upsert_user(
        self,
        *,
        name: str,
        ip: str | None,
        providers: list[str],
        taste: dict[str, Any],
        liked: list[dict[str, Any]],
        disliked: list[dict[str, Any]],
        skipped: list[dict[str, Any]],
        seen: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Legt an oder aktualisiert das Profil gleichen Namens (case-insensitiv).

        Der Avatar bleibt bei einem erneuten Onboarding erhalten.
        Rueckgabe: (Nutzer, neu_angelegt).
        """
        now = utc_now()
        existing = self.get_user_by_name(name)
        payload = (
            json.dumps(providers, ensure_ascii=False),
            json.dumps(taste, ensure_ascii=False),
            json.dumps(liked, ensure_ascii=False),
            json.dumps(disliked, ensure_ascii=False),
            json.dumps(skipped, ensure_ascii=False),
            json.dumps(seen, ensure_ascii=False),
        )
        with self._db() as conn:
            if existing:
                conn.execute(
                    """
                    UPDATE users
                       SET name = ?, ip = ?, providers = ?, taste = ?, liked = ?,
                           disliked = ?, skipped = ?, seen = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (name.strip(), ip, *payload, now, existing["id"]),
                )
                user_id = existing["id"]
                created = False
            else:
                user_id = uuid.uuid4().hex
                conn.execute(
                    """
                    INSERT INTO users
                        (id, name, ip, providers, taste, liked, disliked, skipped,
                         seen, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, name.strip(), ip, *payload, now, now),
                )
                created = True
        user = self.get_user(user_id)
        assert user is not None
        return user, created

    def update_user_ip(self, user_id: str, ip: str) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE users SET ip = ?, updated_at = ? WHERE id = ?",
                (ip, utc_now(), user_id),
            )

    def set_user_avatar(self, user_id: str, avatar_url: str | None) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE users SET avatar_url = ?, updated_at = ? WHERE id = ?",
                (avatar_url, utc_now(), user_id),
            )

    def delete_user(self, user_id: str) -> dict[str, Any] | None:
        user = self.get_user(user_id)
        if not user:
            return None
        with self._db() as conn:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
        return user

    def reset_user_profile(self, user_id: str) -> dict[str, Any] | None:
        """Setzt Swipes/Geschmack zurueck und behaelt Identitaet und Avatar."""
        if not self.get_user(user_id):
            return None
        with self._db() as conn:
            conn.execute(
                """
                UPDATE users
                   SET taste = '{}', liked = '[]', disliked = '[]', skipped = '[]',
                       seen = '[]', updated_at = ?
                 WHERE id = ?
                """,
                (utc_now(), user_id),
            )
        return self.get_user(user_id)

    # --------------------------------------------------------------- settings

    def get_setting(self, key: str) -> Any | None:
        with self._db() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return _loads(row["value"], None) if row else None

    def set_setting(self, key: str, value: Any) -> None:
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, json.dumps(value, ensure_ascii=False)),
            )

    def get_household_providers(self) -> list[str] | None:
        value = self.get_setting("household_providers")
        return value if isinstance(value, list) else None

    def set_household_providers(self, providers: list[str]) -> None:
        self.set_setting("household_providers", providers)

    # ---------------------------------------------------------- quiz sessions

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "name": row["name"],
            "providers": _loads(row["providers"], []),
            "state": _loads(row["state"], {}),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def create_quiz_session(
        self, *, name: str, providers: list[str], state: dict[str, Any]
    ) -> str:
        session_id = uuid.uuid4().hex
        now = utc_now()
        with self._db() as conn:
            conn.execute(
                """
                INSERT INTO quiz_sessions (id, name, providers, state, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    name,
                    json.dumps(providers, ensure_ascii=False),
                    json.dumps(state, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return session_id

    def get_quiz_session(self, session_id: str) -> dict[str, Any] | None:
        with self._db() as conn:
            row = conn.execute(
                "SELECT * FROM quiz_sessions WHERE id = ?", (session_id,)
            ).fetchone()
        return self._session_from_row(row) if row else None

    def update_quiz_session(self, session_id: str, state: dict[str, Any]) -> None:
        with self._db() as conn:
            conn.execute(
                "UPDATE quiz_sessions SET state = ?, updated_at = ? WHERE id = ?",
                (json.dumps(state, ensure_ascii=False), utc_now(), session_id),
            )

    def delete_quiz_session(self, session_id: str) -> None:
        with self._db() as conn:
            conn.execute("DELETE FROM quiz_sessions WHERE id = ?", (session_id,))

    def purge_quiz_sessions(self, max_age_hours: int = 48) -> int:
        cutoff = (
            datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
        ).isoformat(timespec="seconds")
        with self._db() as conn:
            cursor = conn.execute(
                "DELETE FROM quiz_sessions WHERE created_at < ?", (cutoff,)
            )
        return cursor.rowcount or 0

    # ------------------------------------------------------------------ misc

    def count_users(self) -> int:
        with self._db() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()
        return int(row["n"]) if row else 0
