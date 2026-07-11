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
    SessionPhase,
)
from lecturabot.repository import SQLiteRepository
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
    assert await repository.get_user_stats(1, 10) == (1, 125)

    restarted = SessionService(repository, clock=clock)
    await restarted.initialize()
    recovered = await restarted.get_session(101)
    assert recovered is not None
    assert recovered.current_user_id == 20
    assert recovered.members[10].turns == 1
    assert recovered.members[10].total_seconds == 125


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
        body="However, she was abducted.",
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
        items=["abducted", "missing"],
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
        items=["word"],
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
            items=["one", "two"],
            source=CorrectionSource.BUTTON,
        )
    _assert_error(too_many_entries, "correction_summary_full")

    with pytest.raises(SessionError) as too_many_characters:
        await service.add_corrections(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Reader 20",
            items=["1234567"],
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
