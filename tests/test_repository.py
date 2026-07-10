from __future__ import annotations

import json
from pathlib import Path
import sqlite3

import pytest

from lecturabot.models import ChannelMode, MemberState, SessionState
from lecturabot.models import Language, Level
from lecturabot.repository import RepositoryError, SCHEMA_VERSION, SQLiteRepository


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

    assert await repository.get_user_stats(100, 41) == (0, 0)
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=30,
    ) == (1, 30)
    assert await repository.record_completed_turn(
        guild_id=100,
        user_id=41,
        duration_seconds=-5,
    ) == (2, 30)

    reopened = SQLiteRepository(repository.database_path)
    assert await reopened.get_user_stats(100, 41) == (2, 30)


async def test_complete_turn_updates_stats_and_session_atomically(
    tmp_path: Path,
) -> None:
    repository = await _repository(tmp_path)
    state = _session()

    result = await repository.complete_turn_and_save_session(
        state=state,
        user_id=41,
        duration_seconds=25,
    )

    assert result == (1, 25)
    assert await repository.get_user_stats(100, 41) == (1, 25)
    assert state.members[41].turns == 1
    assert state.members[41].total_seconds == 25
    assert state.revision == 1
    assert await repository.load_session(state.text_channel_id) == state
