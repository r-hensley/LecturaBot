from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from lecturabot.config import ChannelPairConfig
from lecturabot.models import (
    ChannelMode,
    CorrectionSource,
    Language,
    Level,
    ReadingText,
    SessionPhase,
)
from lecturabot.repository import STATISTICS_INACTIVITY_SECONDS, SQLiteRepository
from lecturabot.service import (
    SessionError,
    SessionService,
    parse_correction_lines,
)


SEED_PATH = (
    Path(__file__).parents[1]
    / "src"
    / "lecturabot"
    / "data"
    / "readings.json"
)


@dataclass
class ManualClock:
    value: int = 1_000

    def __call__(self) -> int:
        return self.value


async def _make_service(
    tmp_path: Path,
    *,
    mode: ChannelMode = ChannelMode.STANDARD,
    name: str = "lectura-test",
    text_channel_id: int = 101,
    voice_channel_id: int = 201,
    maximum_participants: int = 25,
    maximum_correction_entries: int = 20,
    maximum_correction_characters: int = 1_400,
) -> tuple[SessionService, SQLiteRepository, ManualClock]:
    repository = SQLiteRepository(tmp_path / f"{name}.sqlite3")
    await repository.initialize()
    await repository.seed_texts(SEED_PATH)
    clock = ManualClock()
    service = SessionService(
        repository,
        maximum_participants=maximum_participants,
        maximum_correction_entries=maximum_correction_entries,
        maximum_correction_characters=maximum_correction_characters,
        clock=clock,
        chooser=lambda candidates: candidates[0],
    )
    await service.initialize()
    await service.get_or_create_session(
        guild_id=1,
        pair=ChannelPairConfig(
            name=name,
            text_channel_id=text_channel_id,
            voice_channel_id=voice_channel_id,
            mode=mode,
        ),
    )
    return service, repository, clock


async def _join(
    service: SessionService,
    *user_ids: int,
    text_channel_id: int = 101,
) -> None:
    for user_id in user_ids:
        await service.join(
            text_channel_id=text_channel_id,
            user_id=user_id,
            display_name=f"Reader {user_id}",
        )


def _assert_error(error: pytest.ExceptionInfo[SessionError], code: str) -> None:
    assert error.value.code == code


def test_correction_parser_splits_top_level_commas_and_newlines() -> None:
    assert parse_correction_lines(
        "vegetables, looking, produce (noun), baked, performances, variety,"
    ) == [
        "vegetables",
        "looking",
        "produce (noun)",
        "baked",
        "performances",
        "variety",
    ]
    assert parse_correction_lines(
        " produce (noun, countable), apple,\n\n perro,\n"
    ) == [
        "produce (noun, countable)",
        "apple",
        "perro",
    ]


async def _prepare_custom_reading(
    tmp_path: Path,
    *,
    language: Language,
    body: str,
) -> tuple[SessionService, SQLiteRepository]:
    service, repository, _ = await _make_service(tmp_path)
    await _join(service, 10, 20, 30)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=language,
        body=body,
    )
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=700,
    )
    return service, repository


@pytest.mark.asyncio
async def test_start_gate_rotation_stats_and_restart_round_trip(
    tmp_path: Path,
) -> None:
    service, repository, clock = await _make_service(tmp_path)
    await _join(service, 10)

    with pytest.raises(SessionError) as insufficient:
        await service.start(text_channel_id=101, actor_id=10)
    _assert_error(insufficient, "not_enough_participants")

    await _join(service, 20)
    started = await service.start(text_channel_id=101, actor_id=10)
    assert started.state.phase is SessionPhase.SELECTING
    assert started.state.current_user_id == 10
    assert started.state.queue == [10, 20]
    assert started.state.turn_started_at == 1_000
    assert started.repost_queue is True

    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    clock.value = 1_010
    selected = await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        body="  A short custom reading.  ",
    )
    assert selected.state.active_reading is not None
    assert selected.state.active_reading.body == "A short custom reading."
    assert selected.state.active_reading.started_at == 1_010
    assert selected.state.picker_message_id == 500

    published = await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=600,
    )
    assert published.picker_message_id is None
    clock.value = 1_135
    passed = await service.pass_turn(
        text_channel_id=101,
        actor_id=10,
        source_message_id=600,
    )

    assert passed.advanced is True
    assert passed.repost_queue is True
    assert passed.retired_reading_message_id == 600
    assert passed.state.queue == [10, 20]
    assert passed.state.current_user_id == 20
    assert passed.state.phase is SessionPhase.SELECTING
    assert passed.state.members[10].turns == 1
    assert passed.state.members[10].total_seconds == 125
    assert passed.state.members[10].average_seconds == 125
    assert await repository.get_user_stats(
        1, 10, at_timestamp=clock.value
    ) == (1, 125)

    restarted = SessionService(repository, clock=clock)
    await restarted.initialize()
    recovered = await restarted.get_session(101)
    assert recovered is not None
    assert recovered.current_user_id == 20
    assert recovered.members[10].turns == 1
    assert recovered.members[10].total_seconds == 125

    clock.value = 1_135 + STATISTICS_INACTIVITY_SECONDS
    expired_service = SessionService(repository, clock=clock)
    await expired_service.initialize()
    expired = await expired_service.get_session(101)
    assert expired is not None
    assert expired.members[10].turns == 0
    assert expired.members[10].total_seconds == 0
    assert expired.members[10].average_seconds is None


@pytest.mark.asyncio
async def test_queued_users_expire_independently_when_another_reader_finishes(
    tmp_path: Path,
) -> None:
    service, repository, clock = await _make_service(tmp_path)
    first_completion = clock.value
    await repository.record_completed_turn(
        guild_id=1,
        user_id=10,
        duration_seconds=60,
        completed_at=first_completion,
    )
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )

    # Passing before a reading is published advances the queue without
    # extending the current reader's statistics window.
    clock.value = first_completion + STATISTICS_INACTIVITY_SECONDS - 20
    selection_pass = await service.pass_turn(
        text_channel_id=101,
        actor_id=10,
        source_message_id=500,
    )
    assert selection_pass.state.current_user_id == 20
    assert selection_pass.state.members[10].turns == 1

    clock.value = first_completion + STATISTICS_INACTIVITY_SECONDS - 10
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=20,
        message_id=600,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=20,
        picker_message_id=600,
        language=Language.ENGLISH,
        body="A short reading near the statistics boundary.",
    )
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=20,
        message_id=700,
    )

    clock.value = first_completion + STATISTICS_INACTIVITY_SECONDS
    passed = await service.pass_turn(
        text_channel_id=101,
        actor_id=20,
        source_message_id=700,
    )

    assert passed.state.members[10].turns == 0
    assert passed.state.members[10].total_seconds == 0
    assert passed.state.members[10].average_seconds is None
    assert passed.state.members[20].turns == 1
    assert passed.state.members[20].total_seconds == 10
    assert await repository.get_user_stats(
        1, 10, at_timestamp=clock.value
    ) == (0, 0)
    assert await repository.get_user_stats(
        1, 20, at_timestamp=clock.value
    ) == (1, 10)


@pytest.mark.asyncio
async def test_queue_activity_and_afk_skip_do_not_extend_statistics(
    tmp_path: Path,
) -> None:
    service, repository, clock = await _make_service(tmp_path)
    first_completion = clock.value
    await repository.record_completed_turn(
        guild_id=1,
        user_id=10,
        duration_seconds=60,
        completed_at=first_completion,
    )
    await _join(service, 10, 20, 30, 40)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )

    clock.value = first_completion + STATISTICS_INACTIVITY_SECONDS - 1
    await service.vote_to_skip(
        text_channel_id=101,
        voter_id=20,
        source_message_id=500,
    )
    await service.vote_to_skip(
        text_channel_id=101,
        voter_id=30,
        source_message_id=500,
    )
    skipped = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=40,
        source_message_id=500,
    )
    assert skipped.state.members[10].turns == 1

    await service.leave(text_channel_id=101, user_id=10)
    rejoined = await service.join(
        text_channel_id=101,
        user_id=10,
        display_name="Reader 10",
    )
    assert rejoined.state.members[10].turns == 1

    clock.value = first_completion + STATISTICS_INACTIVITY_SECONDS
    expired = await service.get_session(101)
    assert expired is not None
    assert expired.members[10].turns == 0
    assert expired.members[10].total_seconds == 0


@pytest.mark.asyncio
async def test_restart_recovers_selection_when_reading_was_not_published(
    tmp_path: Path,
) -> None:
    service, repository, clock = await _make_service(tmp_path)
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        body="This reading was never sent.",
    )

    restarted = SessionService(repository, clock=clock)
    await restarted.initialize()
    recovered = await restarted.get_session(101)

    assert recovered is not None
    assert recovered.phase is SessionPhase.SELECTING
    assert recovered.current_user_id == 10
    assert recovered.picker_message_id == 500
    assert recovered.active_reading is None


@pytest.mark.asyncio
async def test_skip_votes_are_unique_and_reset_when_threshold_advances(
    tmp_path: Path,
) -> None:
    service, repository, _ = await _make_service(tmp_path)
    await _join(service, 10, 20, 30, 40)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )

    with pytest.raises(SessionError) as stale:
        await service.vote_to_skip(
            text_channel_id=101,
            voter_id=20,
            source_message_id=999,
        )
    _assert_error(stale, "stale_turn")

    with pytest.raises(SessionError) as non_reader_pass:
        await service.pass_turn(
            text_channel_id=101,
            actor_id=20,
            source_message_id=500,
        )
    _assert_error(non_reader_pass, "not_current_reader")

    with pytest.raises(SessionError) as reader_vote:
        await service.vote_to_skip(
            text_channel_id=101,
            voter_id=10,
            source_message_id=500,
        )
    _assert_error(reader_vote, "current_reader_skip_vote")

    first = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=20,
        source_message_id=500,
    )
    assert first.advanced is False
    assert (first.vote_count, first.votes_required) == (1, 3)
    assert first.state.skip_votes == {20}

    with pytest.raises(SessionError) as duplicate:
        await service.vote_to_skip(
            text_channel_id=101,
            voter_id=20,
            source_message_id=500,
        )
    _assert_error(duplicate, "already_voted")

    second = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=30,
        source_message_id=500,
    )
    assert second.advanced is False
    assert (second.vote_count, second.votes_required) == (2, 3)
    assert second.state.skip_votes == {20, 30}

    skipped = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=40,
        source_message_id=500,
    )
    assert skipped.advanced is True
    assert skipped.repost_queue is True
    assert (skipped.vote_count, skipped.votes_required) == (3, 3)
    assert skipped.state.current_user_id == 20
    assert skipped.state.skip_votes == set()
    assert skipped.retired_picker_message_id == 500
    assert await repository.get_user_stats(1, 10) == (0, 0)


@pytest.mark.asyncio
async def test_departure_removes_vote_without_lowering_fixed_threshold(
    tmp_path: Path,
) -> None:
    service, _, _ = await _make_service(tmp_path)
    await _join(service, 10, 20, 30, 40)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    first_vote = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=20,
        source_message_id=500,
    )
    second_vote = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=30,
        source_message_id=500,
    )
    assert first_vote.advanced is False
    assert second_vote.advanced is False

    departure = await service.leave(text_channel_id=101, user_id=30)

    assert departure.advanced is False
    assert departure.repost_queue is True
    assert departure.state.current_user_id == 10
    assert departure.state.skip_votes == {20}
    assert departure.retired_picker_message_id is None

    still_short = await service.vote_to_skip(
        text_channel_id=101,
        voter_id=40,
        source_message_id=500,
    )
    assert still_short.advanced is False
    assert (still_short.vote_count, still_short.votes_required) == (2, 3)


@pytest.mark.asyncio
async def test_leaving_current_advances_and_rejoining_uses_queue_tail(
    tmp_path: Path,
) -> None:
    service, _, _ = await _make_service(tmp_path)
    await _join(service, 10, 20, 30)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )

    left = await service.leave(text_channel_id=101, user_id=10)
    assert left.advanced is True
    assert left.repost_queue is True
    assert left.retired_picker_message_id == 500
    assert left.state.queue == [20, 30]
    assert left.state.current_user_id == 20

    await _join(service, 10)
    removed_waiter = await service.leave(text_channel_id=101, user_id=30)
    assert removed_waiter.advanced is False
    assert removed_waiter.repost_queue is True
    assert removed_waiter.state.queue == [20, 10]
    assert removed_waiter.state.current_user_id == 20

    await service.set_picker_message(
        text_channel_id=101,
        reader_id=20,
        message_id=501,
    )
    below_minimum = await service.leave(text_channel_id=101, user_id=20)
    assert below_minimum.advanced is False
    assert below_minimum.repost_queue is True
    assert below_minimum.state.queue == [10]
    assert below_minimum.state.phase is SessionPhase.WAITING
    assert below_minimum.state.current_user_id is None
    assert below_minimum.retired_picker_message_id == 501


@pytest.mark.asyncio
async def test_current_reader_leaving_final_slot_wraps_to_first_member(
    tmp_path: Path,
) -> None:
    service, _, _ = await _make_service(tmp_path)
    await _join(service, 10, 20, 30)
    await service.start(text_channel_id=101, actor_id=10)

    for reader_id, picker_id in ((10, 500), (20, 501)):
        await service.set_picker_message(
            text_channel_id=101,
            reader_id=reader_id,
            message_id=picker_id,
        )
        await service.pass_turn(
            text_channel_id=101,
            actor_id=reader_id,
            source_message_id=picker_id,
        )

    await service.set_picker_message(
        text_channel_id=101,
        reader_id=30,
        message_id=502,
    )
    left = await service.leave(text_channel_id=101, user_id=30)

    assert left.state.queue == [10, 20]
    assert left.state.current_index == 0
    assert left.state.current_user_id == 10
    assert left.activated_reader_id == 10
    assert left.retired_picker_message_id == 502


@pytest.mark.asyncio
async def test_failed_join_save_does_not_mutate_cached_or_persisted_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = await _make_service(tmp_path)
    before = await service.get_session(101)
    assert before is not None

    failing_save = AsyncMock(side_effect=OSError("simulated disk failure"))
    monkeypatch.setattr(repository, "save_session", failing_save)

    with pytest.raises(OSError, match="simulated disk failure"):
        await service.join(
            text_channel_id=101,
            user_id=10,
            display_name="Reader 10",
        )

    after = await service.get_session(101)
    persisted = await repository.load_session(101)
    assert after is not None
    assert persisted is not None
    assert after.to_dict() == before.to_dict()
    assert persisted.to_dict() == before.to_dict()
    failing_save.assert_awaited_once()


@pytest.mark.asyncio
async def test_queue_capacity_rejects_before_persisting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = await _make_service(
        tmp_path,
        maximum_participants=2,
    )
    await _join(service, 10, 20)
    before = await service.get_session(101)
    assert before is not None

    save_spy = AsyncMock(wraps=repository.save_session)
    monkeypatch.setattr(repository, "save_session", save_spy)
    with pytest.raises(SessionError) as full:
        await service.join(
            text_channel_id=101,
            user_id=30,
            display_name="Reader 30",
        )
    _assert_error(full, "queue_full")

    after = await service.get_session(101)
    persisted = await repository.load_session(101)
    assert after is not None
    assert persisted is not None
    assert after.to_dict() == before.to_dict()
    assert persisted.to_dict() == before.to_dict()
    save_spy.assert_not_awaited()


@pytest.mark.asyncio
async def test_catalog_and_custom_only_selection_enforce_picker_and_mode(
    tmp_path: Path,
) -> None:
    service, repository, _ = await _make_service(tmp_path)
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )

    with pytest.raises(SessionError) as stale_picker:
        await service.begin_catalog_reading(
            text_channel_id=101,
            reader_id=10,
            picker_message_id=999,
            language=Language.ENGLISH,
            level=Level.BEGINNER,
        )
    _assert_error(stale_picker, "stale_picker")

    expected = (await repository.list_texts(
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    ))[0]
    selected = await service.begin_catalog_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    )
    reading = selected.state.active_reading
    assert reading is not None
    assert selected.state.phase is SessionPhase.READING
    assert reading.source_text_id == expected.id
    assert reading.body == expected.body
    assert reading.expected_emotion == expected.expected_emotion
    assert expected.id in selected.state.used_text_ids

    custom, _, _ = await _make_service(
        tmp_path,
        mode=ChannelMode.CUSTOM_ONLY,
        name="other-language",
        text_channel_id=102,
        voice_channel_id=202,
    )
    await _join(custom, 10, 20, text_channel_id=102)
    await custom.start(text_channel_id=102, actor_id=10)
    await custom.set_picker_message(
        text_channel_id=102,
        reader_id=10,
        message_id=800,
    )
    with pytest.raises(SessionError) as catalog_forbidden:
        await custom.begin_catalog_reading(
            text_channel_id=102,
            reader_id=10,
            picker_message_id=800,
            language=Language.ENGLISH,
            level=Level.BEGINNER,
        )
    _assert_error(catalog_forbidden, "custom_only")

    with pytest.raises(SessionError) as missing_language:
        await custom.begin_custom_reading(
            text_channel_id=102,
            reader_id=10,
            picker_message_id=800,
            language=Language.ENGLISH,
            body="  こんにちは。  ",
        )
    _assert_error(missing_language, "missing_language")

    own_text = await custom.begin_custom_reading(
        text_channel_id=102,
        reader_id=10,
        picker_message_id=800,
        language=Language.ENGLISH,
        body="  こんにちは。  ",
        custom_language_label=" 日本語 ",
    )
    assert own_text.state.active_reading is not None
    assert own_text.state.active_reading.body == "こんにちは。"
    assert own_text.state.active_reading.level is None
    assert own_text.state.active_reading.custom_language_label == "日本語"


@pytest.mark.asyncio
async def test_catalog_history_is_per_reader_strict_and_resets_when_queue_empties(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = await _make_service(tmp_path)
    catalog = [
        ReadingText(
            id=9_001,
            language=Language.ENGLISH,
            level=Level.BEGINNER,
            body="First catalog text.",
        ),
        ReadingText(
            id=9_002,
            language=Language.ENGLISH,
            level=Level.BEGINNER,
            body="Second catalog text.",
        ),
    ]
    monkeypatch.setattr(
        repository,
        "list_texts",
        AsyncMock(return_value=catalog),
    )
    await _join(service, 10, 20)
    initial = await service.get_session(101)
    assert initial is not None
    initial_session_id = initial.session_id
    await service.start(text_channel_id=101, actor_id=10)

    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    first_for_10 = await service.begin_catalog_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    )
    assert first_for_10.state.active_reading is not None
    assert first_for_10.state.active_reading.source_text_id == 9_001
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=700,
    )
    await service.pass_turn(
        text_channel_id=101,
        actor_id=10,
        source_message_id=700,
    )

    # Histories are reader-specific: reader 20 may receive the same first text.
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=20,
        message_id=501,
    )
    first_for_20 = await service.begin_catalog_reading(
        text_channel_id=101,
        reader_id=20,
        picker_message_id=501,
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    )
    assert first_for_20.state.active_reading is not None
    assert first_for_20.state.active_reading.source_text_id == 9_001
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=20,
        message_id=701,
    )
    await service.pass_turn(
        text_channel_id=101,
        actor_id=20,
        source_message_id=701,
    )

    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=502,
    )
    second_for_10 = await service.begin_catalog_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=502,
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    )
    assert second_for_10.state.active_reading is not None
    assert second_for_10.state.active_reading.source_text_id == 9_002
    assert second_for_10.state.seen_text_ids_by_user == {
        10: {9_001, 9_002},
        20: {9_001},
    }
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=702,
    )
    await service.pass_turn(
        text_channel_id=101,
        actor_id=10,
        source_message_id=702,
    )

    # Reader 20 passes without choosing, returning the turn to exhausted reader 10.
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=20,
        message_id=503,
    )
    await service.pass_turn(
        text_channel_id=101,
        actor_id=20,
        source_message_id=503,
    )
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=504,
    )
    before_exhaustion = await service.get_session(101)
    with pytest.raises(SessionError) as exhausted:
        await service.begin_catalog_reading(
            text_channel_id=101,
            reader_id=10,
            picker_message_id=504,
            language=Language.ENGLISH,
            level=Level.BEGINNER,
        )
    _assert_error(exhausted, "no_unseen_texts")
    after_exhaustion = await service.get_session(101)
    assert before_exhaustion is not None
    assert after_exhaustion is not None
    assert after_exhaustion.to_dict() == before_exhaustion.to_dict()

    one_left = await service.leave(text_channel_id=101, user_id=10)
    assert one_left.state.session_id == initial_session_id
    assert one_left.state.seen_text_ids_by_user == {
        10: {9_001, 9_002},
        20: {9_001},
    }
    emptied = await service.leave(text_channel_id=101, user_id=20)
    assert emptied.state.queue == []
    assert emptied.state.session_id != initial_session_id
    assert emptied.state.seen_text_ids_by_user == {}

    # The empty queue starts a fresh session, so its first text is eligible again.
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=600,
    )
    fresh_selection = await service.begin_catalog_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=600,
        language=Language.ENGLISH,
        level=Level.BEGINNER,
    )
    assert fresh_selection.state.active_reading is not None
    assert fresh_selection.state.active_reading.source_text_id == 9_001


@pytest.mark.asyncio
async def test_corrections_preserve_sources_and_reject_stale_ids(
    tmp_path: Path,
) -> None:
    service, _, _ = await _make_service(tmp_path)
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        body="However, she was abducted by pirates.",
    )
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=700,
    )

    with pytest.raises(SessionError) as stale_reading:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=701,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["however"],
            source=CorrectionSource.BUTTON,
        )
    _assert_error(stale_reading, "stale_reading")

    with pytest.raises(SessionError) as self_correction:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=10,
            corrector_display_name="Reader 10",
            items=["however"],
            source=CorrectionSource.BUTTON,
        )
    _assert_error(self_correction, "reader_correction")

    parsed = parse_correction_lines(" however  \n\n abducted ")
    first = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=parsed,
        source=CorrectionSource.BUTTON,
    )
    final = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=30,
        corrector_display_name="Listener 30",
        items=["abducted", "pirates"],
        source=CorrectionSource.REPLY,
    )

    assert first.state.active_reading is not None
    reading = final.state.active_reading
    assert reading is not None
    assert reading.correction_count == 3
    assert [group.user_id for group in reading.correction_groups] == [20, 30]
    assert reading.correction_groups[0].entries[0].source is CorrectionSource.BUTTON
    assert reading.correction_groups[1].entries[0].source is CorrectionSource.REPLY
    assert await service.find_by_reading_message(700) is not None
    assert await service.find_by_reading_message(701) is None

    with pytest.raises(SessionError) as stale_turn:
        await service.pass_turn(
            text_channel_id=101,
            actor_id=10,
            source_message_id=701,
        )
    _assert_error(stale_turn, "stale_turn")


@pytest.mark.asyncio
async def test_annotated_corrections_match_and_deduplicate_by_base_word(
    tmp_path: Path,
) -> None:
    service, _ = await _prepare_custom_reading(
        tmp_path,
        language=Language.ENGLISH,
        body="They produce fresh food.",
    )

    first = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=parse_correction_lines("produce (noun),"),
        source=CorrectionSource.BUTTON,
    )
    assert first.state.active_reading is not None
    first_entry = first.state.active_reading.correction_groups[0].entries[0]
    assert first_entry.text == "produce (noun)"
    assert first_entry.target_text == "produce"

    with pytest.raises(SessionError) as repeated_target:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["produce (verb)"],
            source=CorrectionSource.REPLY,
        )
    _assert_error(repeated_target, "duplicate_correction")

    cross_corrector = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=30,
        corrector_display_name="Reader 30",
        items=["produce (verb)"],
        source=CorrectionSource.REPLY,
    )
    assert cross_corrector.state.active_reading is not None
    second_entry = (
        cross_corrector.state.active_reading.correction_groups[1].entries[0]
    )
    assert second_entry.text == "produce (verb)"
    assert second_entry.target_text == "produce"
    assert second_entry.discarded is True
    assert cross_corrector.state.active_reading.correction_count == 1


@pytest.mark.asyncio
async def test_fuzzy_typo_uses_source_target_for_duplicate_handling(
    tmp_path: Path,
) -> None:
    service, _ = await _prepare_custom_reading(
        tmp_path,
        language=Language.ENGLISH,
        body="They are receiving help.",
    )

    typo = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=["recieving"],
        source=CorrectionSource.BUTTON,
    )
    assert typo.state.active_reading is not None
    typo_entry = typo.state.active_reading.correction_groups[0].entries[0]
    assert typo_entry.text == "recieving"
    assert typo_entry.match_text == "receiving"

    exact = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=30,
        corrector_display_name="Reader 30",
        items=["receiving"],
        source=CorrectionSource.REPLY,
    )
    assert exact.state.active_reading is not None
    exact_entry = exact.state.active_reading.correction_groups[1].entries[0]
    assert exact_entry.match_text == "receiving"
    assert exact_entry.discarded is True
    assert exact.state.active_reading.correction_count == 1


@pytest.mark.asyncio
async def test_screenshot_comma_reply_entries_are_all_accepted(
    tmp_path: Path,
) -> None:
    service, _ = await _prepare_custom_reading(
        tmp_path,
        language=Language.ENGLISH,
        body=(
            "The market has fresh fruits, vegetables, and other local products. "
            "I enjoy looking at the colorful displays of produce and trying "
            "samples. There are homemade jams, cheeses, and baked goods. "
            "There are often live music performances or craft booths. It is "
            "fun to see the variety of foods."
        ),
    )

    accepted = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=parse_correction_lines(
            "vegetables, looking, produce (noun), baked, performances, variety,"
        ),
        source=CorrectionSource.REPLY,
    )

    assert accepted.state.active_reading is not None
    entries = accepted.state.active_reading.correction_groups[0].entries
    assert [entry.text for entry in entries] == [
        "vegetables",
        "looking",
        "produce (noun)",
        "baked",
        "performances",
        "variety",
    ]
    assert [entry.target_text for entry in entries] == [
        "vegetables",
        "looking",
        "produce",
        "baked",
        "performances",
        "variety",
    ]


@pytest.mark.parametrize(
    "feedback",
    ["🍎", ":whatCat:", "<:peepoPray:922638020035883058>"],
)
@pytest.mark.asyncio
async def test_standalone_emojis_are_saved_without_source_highlights(
    tmp_path: Path,
    feedback: str,
) -> None:
    service, _ = await _prepare_custom_reading(
        tmp_path,
        language=Language.ENGLISH,
        body="An apple fell.",
    )

    accepted = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=[feedback],
        source=CorrectionSource.BUTTON,
    )

    assert accepted.state.active_reading is not None
    entry = accepted.state.active_reading.correction_groups[0].entries[0]
    assert entry.text == feedback
    assert entry.match_text is None
    assert accepted.state.active_reading.correction_texts == []
    assert accepted.notice.endswith("Listed without highlighting: 1.")


@pytest.mark.asyncio
async def test_matched_and_unmatched_corrections_are_saved_together(
    tmp_path: Path,
) -> None:
    service, repository = await _prepare_custom_reading(
        tmp_path,
        language=Language.ENGLISH,
        body="An apple grows here. Stress matters.",
    )
    accepted = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=parse_correction_lines(
            "apple, banana (noun), 🐕, (stress :peepoPray:)"
        ),
        source=CorrectionSource.BUTTON,
    )

    after = await service.get_session(101)
    persisted = await repository.load_session(101)
    assert after is not None
    assert persisted is not None
    assert after.to_dict() == accepted.state.to_dict()
    assert persisted.to_dict() == accepted.state.to_dict()
    assert after.active_reading is not None
    entries = after.active_reading.correction_groups[0].entries
    assert [entry.text for entry in entries] == [
        "apple",
        "banana (noun)",
        "🐕",
        "(stress :peepoPray:)",
    ]
    assert [entry.match_text for entry in entries] == [
        "apple",
        None,
        None,
        "Stress",
    ]
    assert after.active_reading.correction_texts == ["apple", "Stress"]
    assert accepted.notice.endswith("Listed without highlighting: 2.")


@pytest.mark.asyncio
async def test_unmatched_corrections_are_saved_and_duplicates_still_rejected(
    tmp_path: Path,
) -> None:
    service, repository, _ = await _make_service(tmp_path)
    await _join(service, 10, 20, 30)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        body="New York is not new to this reader.",
    )
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=700,
    )
    unmatched = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=["Boston"],
        source=CorrectionSource.BUTTON,
    )
    assert unmatched.state.active_reading is not None
    unmatched_entry = (
        unmatched.state.active_reading.correction_groups[0].entries[0]
    )
    assert unmatched_entry.text == "Boston"
    assert unmatched_entry.match_text is None

    with pytest.raises(SessionError) as repeated_unmatched:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["boston"],
            source=CorrectionSource.REPLY,
        )
    _assert_error(repeated_unmatched, "duplicate_correction")

    with pytest.raises(SessionError) as repeated_submission:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["New York", "new   york"],
            source=CorrectionSource.BUTTON,
        )
    _assert_error(repeated_submission, "duplicate_correction")

    accepted = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=["New York"],
        source=CorrectionSource.BUTTON,
    )

    with pytest.raises(SessionError) as repeated_existing:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["new york"],
            source=CorrectionSource.REPLY,
        )
    _assert_error(repeated_existing, "duplicate_correction")

    cross_corrector = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=30,
        corrector_display_name="Reader 30",
        items=["new york"],
        source=CorrectionSource.REPLY,
    )

    after = await service.get_session(101)
    persisted = await repository.load_session(101)
    assert accepted.state.active_reading is not None
    assert cross_corrector.state.active_reading is not None
    assert after is not None
    assert persisted is not None
    assert after.to_dict() == cross_corrector.state.to_dict()
    assert persisted.to_dict() == cross_corrector.state.to_dict()
    assert cross_corrector.state.active_reading.correction_count == 2
    assert cross_corrector.state.active_reading.correction_texts == [
        "New York",
        "New York",
    ]
    assert (
        cross_corrector.state.active_reading.correction_groups[1]
        .entries[0]
        .discarded
        is True
    )


@pytest.mark.asyncio
async def test_correction_validator_rejects_before_state_is_committed(
    tmp_path: Path,
) -> None:
    service, repository, _ = await _make_service(tmp_path)
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        body="A short reading.",
    )
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=700,
    )
    before = await service.get_session(101)

    def reject_render(_: object) -> None:
        raise ValueError("simulated Discord limit")

    with pytest.raises(ValueError, match="simulated Discord limit"):
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["short"],
            source=CorrectionSource.BUTTON,
            validator=reject_render,
        )

    after = await service.get_session(101)
    persisted = await repository.load_session(101)
    assert before is not None
    assert after is not None
    assert persisted is not None
    assert after.to_dict() == before.to_dict()
    assert persisted.to_dict() == before.to_dict()


@pytest.mark.asyncio
async def test_aggregate_correction_limits_reject_before_persisting(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, repository, _ = await _make_service(
        tmp_path,
        maximum_correction_entries=2,
        maximum_correction_characters=10,
    )
    await _join(service, 10, 20)
    await service.start(text_channel_id=101, actor_id=10)
    await service.set_picker_message(
        text_channel_id=101,
        reader_id=10,
        message_id=500,
    )
    await service.begin_custom_reading(
        text_channel_id=101,
        reader_id=10,
        picker_message_id=500,
        language=Language.ENGLISH,
        body="A short reading.",
    )
    await service.set_reading_message(
        text_channel_id=101,
        reader_id=10,
        message_id=700,
    )
    accepted = await service.add_corrections(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Reader 20",
        items=["short"],
        source=CorrectionSource.BUTTON,
    )
    before = accepted.state

    save_spy = AsyncMock(wraps=repository.save_session)
    monkeypatch.setattr(repository, "save_session", save_spy)

    with pytest.raises(SessionError) as too_many_entries:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["A", "reading"],
            source=CorrectionSource.BUTTON,
        )
    _assert_error(too_many_entries, "correction_summary_full")

    with pytest.raises(SessionError) as too_many_characters:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["reading"],
            source=CorrectionSource.BUTTON,
        )
    _assert_error(too_many_characters, "correction_summary_full")

    after = await service.get_session(101)
    persisted = await repository.load_session(101)
    assert after is not None
    assert persisted is not None
    assert after.to_dict() == before.to_dict()
    assert persisted.to_dict() == before.to_dict()
    save_spy.assert_not_awaited()
