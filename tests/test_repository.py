from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from lecturabot.models import ChannelMode, MemberState, SessionState
from lecturabot.models import Language, Level
from lecturabot.repository import (
    RepositoryError,
    SCHEMA_VERSION,
    STATISTICS_INACTIVITY_SECONDS,
    SQLiteRepository,
)


async def _repository(tmp_path: Path) -> SQLiteRepository:
    repository = SQLiteRepository(tmp_path / "state" / "lecturabot.sqlite3")
    await repository.initialize()
    return repository


def _session() -> SessionState:
    return SessionState(
        session_id=17,
        guild_id=100,
        text_channel_id=200,
        voice_channel_id=300,
        channel_mode=ChannelMode.STANDARD,
        queue=[41, 42],
        members={
            41: MemberState(41, "Ángela", turns=2, total_seconds=90),
            42: MemberState(42, "Reader Two"),
        },
        used_text_ids={3, 8},
        queue_message_id=900,
    )


async def test_initialize_is_idempotent_and_sets_schema_version(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)

    await repository.initialize()

    assert await repository.schema_version() == SCHEMA_VERSION
    assert await repository.allocate_session_id() == 1
    assert await repository.allocate_session_id() == 2


async def test_initialize_rejects_database_from_newer_schema(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "newer.sqlite3"
    database_path.parent.mkdir()
    newer_version = SCHEMA_VERSION + 1
    with sqlite3.connect(database_path) as connection:
        connection.execute(f"PRAGMA user_version = {newer_version}")

    repository = SQLiteRepository(database_path)
    with pytest.raises(RepositoryError, match="newer than supported"):
        await repository.initialize()

    with sqlite3.connect(database_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == newer_version


async def test_initialize_migrates_lifetime_stats_to_fresh_windows(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "legacy.sqlite3"
    database_path.parent.mkdir()
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE user_stats (
                guild_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                turns INTEGER NOT NULL,
                total_seconds INTEGER NOT NULL,
                PRIMARY KEY (guild_id, user_id)
            )
            """
        )
        connection.execute(
            "INSERT INTO user_stats VALUES (?, ?, ?, ?)",
            (100, 41, 7, 420),
        )
        connection.execute("PRAGMA user_version = 1")

    repository = SQLiteRepository(database_path)
    await repository.initialize()

    assert await repository.schema_version() == SCHEMA_VERSION
    assert await repository.get_user_stats(100, 41, at_timestamp=10_000) == (0, 0)
    with sqlite3.connect(database_path) as connection:
        columns = {
            str(row[1]) for row in connection.execute("PRAGMA table_info(user_stats)")
        }
        row = connection.execute(
            """
            SELECT turns, total_seconds, last_completed_at
            FROM user_stats WHERE guild_id = 100 AND user_id = 41
            """
        ).fetchone()
    assert "last_completed_at" in columns
    assert row == (0, 0, None)


async def test_seed_is_idempotent_and_catalog_filters_language_and_level(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    seed_path = tmp_path / "readings.json"
    seed_path.write_text(
        json.dumps(
            [
                {
                    "language": "en",
                    "level": "beginner",
                    "body": "First English text.",
                },
                {
                    "language": "en",
                    "level": "beginner",
                    "body": "Second English text.",
                    "expected_emotion": "Delight",
                },
                {
                    "language": "en",
                    "level": "advanced",
                    "body": "Advanced English text.",
                },
                {
                    "language": "es",
                    "level": "beginner",
                    "body": "Texto inicial en español.",
                },
            ]
        ),
        encoding="utf-8",
    )

    assert await repository.seed_texts(seed_path) == 4
    assert await repository.seed_texts(seed_path) == 0

    english_beginner = await repository.list_texts(
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    )
    assert [text.body for text in english_beginner] == [
        "First English text.",
        "Second English text.",
    ]
    assert english_beginner[1].expected_emotion == "Delight"
    assert all(text.language is Language.ENGLISH for text in english_beginner)
    assert all(text.level is Level.BEGINNER for text in english_beginner)
    assert await repository.list_texts(
        language=Language.SPANISH,
        level=Level.INTERMEDIATE,
    ) == []

    retirement_path = tmp_path / "retired_readings.json"
    retirement_path.write_text(
        json.dumps(
            [
                {
                    "language": "en",
                    "level": "beginner",
                    "body": "First English text.",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert await repository.disable_texts(retirement_path) == 1
    assert await repository.disable_texts(retirement_path) == 0
    assert [
        text.body
        for text in await repository.list_texts(
            language=Language.ENGLISH,
            level=Level.BEGINNER,
        )
    ] == ["Second English text."]


async def test_catalog_sync_inserts_updates_reenables_and_disables(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    empty_path = tmp_path / "empty.json"
    empty_path.write_text("[]", encoding="utf-8")
    with pytest.raises(
        RepositoryError,
        match="authoritative reading catalog is empty",
    ):
        await repository.sync_texts(empty_path)

    initial_path = tmp_path / "initial.json"
    initial_path.write_text(
        json.dumps(
            [
                {
                    "language": "en",
                    "level": "beginner",
                    "body": "Keep and update emotion.",
                    "expected_emotion": "Old",
                },
                {
                    "language": "en",
                    "level": "beginner",
                    "body": "Remove from active catalog.",
                },
                {
                    "language": "es",
                    "level": "beginner",
                    "body": "Volver a activar.",
                },
            ]
        ),
        encoding="utf-8",
    )
    assert await repository.seed_texts(initial_path) == 3
    retirement_path = tmp_path / "retire.json"
    retirement_path.write_text(
        json.dumps(
            [
                {
                    "language": "es",
                    "level": "beginner",
                    "body": "Volver a activar.",
                }
            ]
        ),
        encoding="utf-8",
    )
    assert await repository.disable_texts(retirement_path) == 1

    desired_path = tmp_path / "desired.json"
    desired_path.write_text(
        json.dumps(
            [
                {
                    "language": "en",
                    "level": "beginner",
                    "body": "Keep and update emotion.",
                    "expected_emotion": "New",
                },
                {
                    "language": "es",
                    "level": "beginner",
                    "body": "Volver a activar.",
                },
                {
                    "language": "en",
                    "level": "advanced",
                    "body": "Brand-new passage.",
                },
            ]
        ),
        encoding="utf-8",
    )

    result = await repository.sync_texts(desired_path)
    assert (
        result.inserted,
        result.reenabled,
        result.updated,
        result.disabled,
    ) == (1, 1, 1, 1)
    assert [
        (text.body, text.expected_emotion)
        for text in await repository.list_texts(
            language=Language.ENGLISH,
            level=Level.BEGINNER,
        )
    ] == [("Keep and update emotion.", "New")]
    assert [
        text.body
        for text in await repository.list_texts(
            language=Language.SPANISH,
            level=Level.BEGINNER,
        )
    ] == ["Volver a activar."]

    repeated = await repository.sync_texts(desired_path)
    assert (
        repeated.inserted,
        repeated.reenabled,
        repeated.updated,
        repeated.disabled,
    ) == (0, 0, 0, 0)


async def test_session_round_trip_and_load_all(tmp_path: Path) -> None:
    repository = await _repository(tmp_path)
    state = _session()

    assert await repository.load_session(state.text_channel_id) is None
    await repository.save_session(state)

    assert state.revision == 1
    assert await repository.load_session(state.text_channel_id) == state
    assert await repository.load_all_sessions() == [state]


async def test_user_stats_persist_across_repository_instances(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    first_completion = 10_000

    assert await repository.get_user_stats(
        100, 41, at_timestamp=first_completion
    ) == (0, 0)
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=30,
        completed_at=first_completion,
    ) == (1, 30)
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=-5,
        completed_at=first_completion + 60,
    ) == (2, 30)

    reopened = SQLiteRepository(repository.database_path)
    await reopened.initialize()
    assert await reopened.get_user_stats(
        100,
        41,
        at_timestamp=first_completion + 60,
    ) == (2, 30)


async def test_user_stats_expire_independently_after_six_idle_hours(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    first_completion = 50_000
    just_before_expiry = first_completion + STATISTICS_INACTIVITY_SECONDS - 1

    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=30,
        completed_at=first_completion,
    ) == (1, 30)
    assert await repository.get_user_stats(
        100,
        41,
        at_timestamp=just_before_expiry,
    ) == (1, 30)

    # A completion one second before expiry extends this user's window from
    # that new completion, rather than from the first turn in the window.
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=45,
        completed_at=just_before_expiry,
    ) == (2, 75)
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=42,
        duration_seconds=20,
        completed_at=just_before_expiry + 1,
    ) == (1, 20)

    second_expiry = just_before_expiry + STATISTICS_INACTIVITY_SECONDS
    assert await repository.get_user_stats_for_users(
        100,
        [41, 42],
        at_timestamp=second_expiry,
    ) == {41: (0, 0), 42: (1, 20)}

    # The first completion after expiry starts a fresh aggregate.
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=25,
        completed_at=second_expiry,
    ) == (1, 25)


async def test_complete_turn_updates_stats_and_session_atomically(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    state = _session()

    result = await repository.complete_turn_and_save_session(
        state=state,
        user_id=41,
        duration_seconds=25,
        completed_at=10_000,
    )

    assert result == (1, 25)
    assert await repository.get_user_stats(
        100, 41, at_timestamp=10_000
    ) == (1, 25)
    assert state.members[41].turns == 1
    assert state.members[41].total_seconds == 25
    assert state.revision == 1
    assert await repository.load_session(state.text_channel_id) == state


async def test_atomic_completion_resets_expired_aggregate_and_snapshot(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    state = _session()
    previous_completion = 20_000
    await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=30,
        completed_at=previous_completion,
    )
    await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=40,
        completed_at=previous_completion + 60,
    )

    completed_at = previous_completion + 60 + STATISTICS_INACTIVITY_SECONDS
    result = await repository.complete_turn_and_save_session(
        state=state,
        user_id=41,
        duration_seconds=25,
        completed_at=completed_at,
    )

    assert result == (1, 25)
    assert await repository.get_user_stats(
        100, 41, at_timestamp=completed_at
    ) == (1, 25)
    assert state.members[41].turns == 1
    assert state.members[41].total_seconds == 25
    recovered = await repository.load_session(state.text_channel_id)
    assert recovered is not None
    assert recovered.members[41].turns == 1
    assert recovered.members[41].total_seconds == 25
