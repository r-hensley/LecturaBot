"""SQLite persistence for the reading catalog, statistics, and sessions.

The repository keeps an asynchronous public API so the Discord-facing service
does not depend on a particular database driver. For this proof of concept,
each operation is a deliberately small, local ``sqlite3`` transaction. Writes
are serialized with an asyncio lock. A production deployment with heavier
traffic should replace this adapter with a supported asynchronous driver.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
import sqlite3
import time
from typing import Any

from .models import Language, Level, ReadingText, SessionState


SCHEMA_VERSION = 2
STATISTICS_INACTIVITY_SECONDS = 6 * 60 * 60


class RepositoryError(RuntimeError):
    """Raised when persisted data cannot be read or validated."""


class SQLiteRepository:
    """Persist POC state in short, serialized SQLite transactions.

    Active sessions use versioned JSON snapshots so this POC can recover its
    state without prematurely freezing a production schema. Catalog texts and
    user statistics remain normal queryable tables.
    """

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._write_lock = asyncio.Lock()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    async def initialize(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._write_lock:
            self._initialize_sync()

    def _initialize_sync(self) -> None:
        connection = self._connect()
        try:
            row = connection.execute("PRAGMA user_version").fetchone()
            existing_version = int(row[0])
            if existing_version > SCHEMA_VERSION:
                raise RepositoryError(
                    "database schema version "
                    f"{existing_version} is newer than supported version "
                    f"{SCHEMA_VERSION}"
                )
            if existing_version not in (0, 1, SCHEMA_VERSION):
                raise RepositoryError(
                    f"no migration is available from schema {existing_version} "
                    f"to {SCHEMA_VERSION}"
                )

            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS session_snapshots (
                    text_channel_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    voice_channel_id INTEGER NOT NULL,
                    state_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS user_stats (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    turns INTEGER NOT NULL DEFAULT 0 CHECK (turns >= 0),
                    total_seconds INTEGER NOT NULL DEFAULT 0
                        CHECK (total_seconds >= 0),
                    last_completed_at INTEGER
                        CHECK (
                            last_completed_at IS NULL
                            OR last_completed_at >= 0
                        ),
                    PRIMARY KEY (guild_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS reading_texts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    language TEXT NOT NULL,
                    level TEXT NOT NULL,
                    body TEXT NOT NULL,
                    expected_emotion TEXT,
                    enabled INTEGER NOT NULL DEFAULT 1
                        CHECK (enabled IN (0, 1)),
                    UNIQUE (language, level, body)
                );

                CREATE INDEX IF NOT EXISTS idx_reading_text_lookup
                ON reading_texts (language, level, enabled);
                """
            )
            user_stats_columns = {
                str(column["name"])
                for column in connection.execute("PRAGMA table_info(user_stats)")
            }
            if "last_completed_at" not in user_stats_columns:
                if existing_version >= SCHEMA_VERSION:
                    raise RepositoryError(
                        "database schema is missing user_stats.last_completed_at"
                    )
                connection.execute(
                    "ALTER TABLE user_stats ADD COLUMN last_completed_at INTEGER "
                    "CHECK (last_completed_at IS NULL OR last_completed_at >= 0)"
                )
            if existing_version < SCHEMA_VERSION:
                # Legacy totals have no completion timestamp, so their recency
                # cannot be reconstructed. Start every user's inactivity
                # window fresh instead of presenting lifetime data as current.
                connection.execute(
                    "UPDATE user_stats SET turns = 0, total_seconds = 0, "
                    "last_completed_at = NULL"
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def schema_version(self) -> int:
        return self._schema_version_sync()

    def _schema_version_sync(self) -> int:
        connection = self._connect()
        try:
            row = connection.execute("PRAGMA user_version").fetchone()
            return int(row[0])
        finally:
            connection.close()

    async def allocate_session_id(self) -> int:
        async with self._write_lock:
            return self._allocate_session_id_sync()

    def _allocate_session_id_sync(self) -> int:
        connection = self._connect()
        try:
            cursor = connection.execute("INSERT INTO session_ids DEFAULT VALUES")
            connection.commit()
            if cursor.lastrowid is None:
                raise RepositoryError("SQLite did not return a session ID")
            return int(cursor.lastrowid)
        finally:
            connection.close()

    async def save_session(self, state: SessionState) -> None:
        """Upsert a validated state snapshot and advance its revision."""
        state.validate()
        next_revision = state.revision + 1
        payload = state.to_dict()
        payload["revision"] = next_revision
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        async with self._write_lock:
            self._save_session_sync(state, encoded)
        state.revision = next_revision

    def _save_session_sync(self, state: SessionState, encoded: str) -> None:
        connection = self._connect()
        try:
            connection.execute(
                """
                INSERT INTO session_snapshots (
                    text_channel_id, guild_id, voice_channel_id,
                    state_json, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(text_channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    voice_channel_id = excluded.voice_channel_id,
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    state.text_channel_id,
                    state.guild_id,
                    state.voice_channel_id,
                    encoded,
                ),
            )
            connection.commit()
        finally:
            connection.close()

    async def load_session(self, text_channel_id: int) -> SessionState | None:
        encoded = self._load_session_json_sync(text_channel_id)
        if encoded is None:
            return None
        return self._decode_session(encoded, text_channel_id)

    def _load_session_json_sync(self, text_channel_id: int) -> str | None:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT state_json FROM session_snapshots WHERE text_channel_id = ?",
                (text_channel_id,),
            ).fetchone()
            return None if row is None else str(row["state_json"])
        finally:
            connection.close()

    async def load_all_sessions(self) -> list[SessionState]:
        rows = self._load_all_session_json_sync()
        return [
            self._decode_session(encoded, text_channel_id)
            for text_channel_id, encoded in rows
        ]

    def _load_all_session_json_sync(self) -> list[tuple[int, str]]:
        connection = self._connect()
        try:
            rows = connection.execute(
                "SELECT text_channel_id, state_json FROM session_snapshots"
            ).fetchall()
            return [
                (int(row["text_channel_id"]), str(row["state_json"]))
                for row in rows
            ]
        finally:
            connection.close()

    @staticmethod
    def _decode_session(encoded: str, text_channel_id: int) -> SessionState:
        try:
            return SessionState.from_dict(json.loads(encoded))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise RepositoryError(
                f"invalid session snapshot for text channel {text_channel_id}"
            ) from error

    async def get_user_stats(
        self,
        guild_id: int,
        user_id: int,
        *,
        at_timestamp: int | None = None,
    ) -> tuple[int, int]:
        statistics = self._get_user_stats_for_users_sync(
            guild_id,
            (user_id,),
            self._timestamp(at_timestamp),
        )
        return statistics[user_id]

    async def get_user_stats_for_users(
        self,
        guild_id: int,
        user_ids: list[int] | tuple[int, ...] | set[int],
        *,
        at_timestamp: int | None = None,
    ) -> dict[int, tuple[int, int]]:
        """Return effective statistics for several users in one SQLite read."""
        normalized_ids = tuple(dict.fromkeys(int(user_id) for user_id in user_ids))
        return self._get_user_stats_for_users_sync(
            guild_id,
            normalized_ids,
            self._timestamp(at_timestamp),
        )

    def _get_user_stats_for_users_sync(
        self,
        guild_id: int,
        user_ids: tuple[int, ...],
        at_timestamp: int,
    ) -> dict[int, tuple[int, int]]:
        if not user_ids:
            return {}
        connection = self._connect()
        try:
            placeholders = ", ".join("?" for _ in user_ids)
            rows = connection.execute(
                f"""
                SELECT user_id, turns, total_seconds, last_completed_at
                FROM user_stats
                WHERE guild_id = ? AND user_id IN ({placeholders})
                """,
                (guild_id, *user_ids),
            ).fetchall()
            rows_by_user = {int(row["user_id"]): row for row in rows}
            return {
                user_id: self._effective_statistics(
                    rows_by_user.get(user_id),
                    at_timestamp,
                )
                for user_id in user_ids
            }
        finally:
            connection.close()

    @staticmethod
    def _effective_statistics(
        row: sqlite3.Row | None,
        at_timestamp: int,
    ) -> tuple[int, int]:
        if row is None or row["last_completed_at"] is None:
            return 0, 0
        last_completed_at = int(row["last_completed_at"])
        if at_timestamp - last_completed_at >= STATISTICS_INACTIVITY_SECONDS:
            return 0, 0
        return int(row["turns"]), int(row["total_seconds"])

    @staticmethod
    def _timestamp(value: int | None) -> int:
        return max(0, int(time.time()) if value is None else int(value))

    async def record_completed_turn(
        self,
        *,
        guild_id: int,
        user_id: int,
        duration_seconds: int,
        completed_at: int | None = None,
    ) -> tuple[int, int]:
        timestamp = self._timestamp(completed_at)
        async with self._write_lock:
            return self._record_completed_turn_sync(
                guild_id,
                user_id,
                max(0, duration_seconds),
                timestamp,
            )

    def _record_completed_turn_sync(
        self,
        guild_id: int,
        user_id: int,
        duration_seconds: int,
        completed_at: int,
    ) -> tuple[int, int]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            statistics = self._upsert_completed_turn_sync(
                connection,
                guild_id=guild_id,
                user_id=user_id,
                duration_seconds=duration_seconds,
                completed_at=completed_at,
            )
            connection.commit()
            return statistics
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    async def complete_turn_and_save_session(
        self,
        *,
        state: SessionState,
        user_id: int,
        duration_seconds: int,
        completed_at: int | None = None,
    ) -> tuple[int, int]:
        """Atomically record one completed turn and its resulting session."""
        state.validate()
        next_revision = state.revision + 1
        timestamp = self._timestamp(completed_at)
        async with self._write_lock:
            turns, total_seconds = self._complete_turn_and_save_session_sync(
                state,
                user_id,
                max(0, duration_seconds),
                timestamp,
                next_revision,
            )
        state.members[user_id].turns = turns
        state.members[user_id].total_seconds = total_seconds
        state.revision = next_revision
        return turns, total_seconds

    def _complete_turn_and_save_session_sync(
        self,
        state: SessionState,
        user_id: int,
        duration_seconds: int,
        completed_at: int,
        next_revision: int,
    ) -> tuple[int, int]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            turns, total_seconds = self._upsert_completed_turn_sync(
                connection,
                guild_id=state.guild_id,
                user_id=user_id,
                duration_seconds=duration_seconds,
                completed_at=completed_at,
            )

            # Render the atomic snapshot with the authoritative active-window
            # stats, without mutating live state until the transaction commits.
            payload = state.to_dict()
            payload["revision"] = next_revision
            member_payload = payload["members"].get(str(user_id))
            if member_payload is None:
                raise RepositoryError("completed reader is missing from session state")
            member_payload["turns"] = turns
            member_payload["total_seconds"] = total_seconds
            encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            connection.execute(
                """
                INSERT INTO session_snapshots (
                    text_channel_id, guild_id, voice_channel_id,
                    state_json, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(text_channel_id) DO UPDATE SET
                    guild_id = excluded.guild_id,
                    voice_channel_id = excluded.voice_channel_id,
                    state_json = excluded.state_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    state.text_channel_id,
                    state.guild_id,
                    state.voice_channel_id,
                    encoded,
                ),
            )
            connection.commit()
            return turns, total_seconds
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _upsert_completed_turn_sync(
        connection: sqlite3.Connection,
        *,
        guild_id: int,
        user_id: int,
        duration_seconds: int,
        completed_at: int,
    ) -> tuple[int, int]:
        parameters = {
            "guild_id": guild_id,
            "user_id": user_id,
            "duration_seconds": duration_seconds,
            "completed_at": completed_at,
            "inactivity_seconds": STATISTICS_INACTIVITY_SECONDS,
        }
        connection.execute(
            """
            INSERT INTO user_stats (
                guild_id, user_id, turns, total_seconds, last_completed_at
            ) VALUES (
                :guild_id, :user_id, 1, :duration_seconds, :completed_at
            )
            ON CONFLICT(guild_id, user_id) DO UPDATE SET
                turns = CASE
                    WHEN user_stats.last_completed_at IS NULL
                        OR user_stats.last_completed_at
                            <= :completed_at - :inactivity_seconds
                    THEN 1
                    ELSE user_stats.turns + 1
                END,
                total_seconds = CASE
                    WHEN user_stats.last_completed_at IS NULL
                        OR user_stats.last_completed_at
                            <= :completed_at - :inactivity_seconds
                    THEN :duration_seconds
                    ELSE user_stats.total_seconds + :duration_seconds
                END,
                last_completed_at = :completed_at
            """,
            parameters,
        )
        row = connection.execute(
            """
            SELECT turns, total_seconds FROM user_stats
            WHERE guild_id = ? AND user_id = ?
            """,
            (guild_id, user_id),
        ).fetchone()
        if row is None:
            raise RepositoryError("completed-turn statistics were not persisted")
        return int(row["turns"]), int(row["total_seconds"])

    async def seed_texts(self, seed_path: Path) -> int:
        """Insert validated seed records idempotently and return the new count."""
        records = self._read_seed_records(seed_path)
        async with self._write_lock:
            return self._seed_texts_sync(records)

    async def disable_texts(self, retirement_path: Path) -> int:
        """Disable matching catalog records and return the changed row count."""
        records = self._read_seed_records(retirement_path)
        async with self._write_lock:
            return self._disable_texts_sync(records)

    @staticmethod
    def _read_seed_records(
        seed_path: Path,
    ) -> list[tuple[str, str, str, str | None]]:
        try:
            raw_items: list[dict[str, Any]] = json.loads(
                seed_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, TypeError) as error:
            raise RepositoryError(f"cannot read text seed file: {seed_path}") from error

        records: list[tuple[str, str, str, str | None]] = []
        for index, item in enumerate(raw_items):
            try:
                language = Language(str(item["language"]))
                level = Level(str(item["level"]))
                body = str(item["body"]).strip()
                emotion = item.get("expected_emotion")
                expected_emotion = None if emotion is None else str(emotion).strip() or None
            except (KeyError, TypeError, ValueError) as error:
                raise RepositoryError(
                    f"invalid reading seed record at index {index}"
                ) from error
            if not body:
                raise RepositoryError(f"empty reading seed record at index {index}")
            records.append((language.value, level.value, body, expected_emotion))
        return records

    def _seed_texts_sync(
        self,
        records: list[tuple[str, str, str, str | None]],
    ) -> int:
        connection = self._connect()
        try:
            before = connection.total_changes
            connection.executemany(
                """
                INSERT OR IGNORE INTO reading_texts (
                    language, level, body, expected_emotion
                ) VALUES (?, ?, ?, ?)
                """,
                records,
            )
            connection.commit()
            return connection.total_changes - before
        finally:
            connection.close()

    def _disable_texts_sync(
        self,
        records: list[tuple[str, str, str, str | None]],
    ) -> int:
        connection = self._connect()
        try:
            before = connection.total_changes
            connection.executemany(
                """
                UPDATE reading_texts
                SET enabled = 0
                WHERE language = ? AND level = ? AND body = ? AND enabled = 1
                """,
                ((language, level, body) for language, level, body, _ in records),
            )
            connection.commit()
            return connection.total_changes - before
        finally:
            connection.close()

    async def list_texts(
        self,
        *,
        language: Language,
        level: Level,
    ) -> list[ReadingText]:
        rows = self._list_texts_sync(language.value, level.value)
        return [
            ReadingText(
                id=int(row["id"]),
                language=Language(str(row["language"])),
                level=Level(str(row["level"])),
                body=str(row["body"]),
                expected_emotion=row["expected_emotion"],
            )
            for row in rows
        ]

    def _list_texts_sync(self, language: str, level: str) -> list[sqlite3.Row]:
        connection = self._connect()
        try:
            return connection.execute(
                """
                SELECT id, language, level, body, expected_emotion
                FROM reading_texts
                WHERE language = ? AND level = ? AND enabled = 1
                ORDER BY id
                """,
                (language, level),
            ).fetchall()
        finally:
            connection.close()
