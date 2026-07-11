from __future__ import annotations

import pytest

from lecturabot.models import (
    ActiveReading,
    ChannelMode,
    CorrectionSource,
    Language,
    Level,
    MemberState,
    SessionPhase,
    SessionState,
)


def _member(user_id: int, name: str) -> MemberState:
    return MemberState(user_id=user_id, display_name=name)


def test_session_snapshot_round_trip_preserves_complete_state() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="María ✨",
        language=Language.SPANISH,
        level=Level.INTERMEDIATE,
        body="Sin embargo, llegó temprano.",
        started_at=1_700_000_010,
        expected_emotion="Alivio",
        source_text_id=7,
        message_id=700,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="Alex",
        items=["Sin embargo", "temprano"],
        source=CorrectionSource.REPLY,
    )
    state = SessionState(
        session_id=11996,
        guild_id=1,
        text_channel_id=101,
        voice_channel_id=201,
        channel_mode=ChannelMode.STANDARD,
        phase=SessionPhase.READING,
        queue=[10, 20],
        members={
            10: MemberState(10, "María ✨", turns=2, total_seconds=249),
            20: _member(20, "Alex"),
        },
        current_index=0,
        turn_started_at=1_700_000_000,
        active_reading=reading,
        skip_votes={20},
        used_text_ids={4, 7},
        queue_message_id=500,
        revision=3,
    )

    payload = state.to_dict()
    restored = SessionState.from_dict(payload)

    assert restored == state
    assert payload["snapshot_version"] == 1
    assert payload["members"].keys() == {"10", "20"}
    assert payload["skip_votes"] == [20]
    assert payload["used_text_ids"] == [4, 7]
    assert restored.members[10].average_seconds == 124
    assert restored.active_reading is not None
    assert restored.active_reading.correction_count == 2
    assert restored.active_reading.correction_groups[0].entries[0].source is (
        CorrectionSource.REPLY
    )


def test_snapshot_rejects_unsupported_version() -> None:
    state = SessionState(1, 1, 101, 201)
    payload = state.to_dict()
    payload["snapshot_version"] = 999

    with pytest.raises(ValueError, match="unsupported session snapshot version"):
        SessionState.from_dict(payload)


def test_duplicate_corrections_preserve_submission_chronology() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.ENGLISH,
        level=Level.BEGINNER,
        body="Alpha beta.",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="First",
        items=["alpha"],
        source=CorrectionSource.BUTTON,
    )
    reading.add_corrections(
        corrector_id=30,
        corrector_display_name="Second",
        items=["Beta"],
        source=CorrectionSource.REPLY,
    )
    # This entry is appended to the first group, which appears before the
    # second group even though this submission happened last.
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="First",
        items=["  bEtA  "],
        source=CorrectionSource.BUTTON,
    )

    first_group, second_group = reading.correction_groups
    assert second_group.entries[0].discarded is False
    assert first_group.entries[1].discarded is True
    assert reading.correction_count == 2


def test_duplicate_normalization_and_legacy_snapshot_loading() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.ENGLISH,
        level=Level.INTERMEDIATE,
        body="New York",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="First",
        items=["New   York"],
        source=CorrectionSource.BUTTON,
    )
    reading.add_corrections(
        corrector_id=30,
        corrector_display_name="Second",
        items=["new york"],
        source=CorrectionSource.REPLY,
    )

    payload = reading.to_dict()
    assert payload["correction_groups"][1]["entries"][0]["discarded"] is True
    assert ActiveReading.from_dict(payload) == reading
    assert reading.correction_count == 1

    # Older snapshots remain readable when the newly persisted flag is absent.
    legacy_payload = reading.to_dict()
    for group in legacy_payload["correction_groups"]:
        for entry in group["entries"]:
            entry.pop("discarded")
    restored = ActiveReading.from_dict(legacy_payload)
    assert all(
        not entry.discarded
        for group in restored.correction_groups
        for entry in group.entries
    )
    assert restored.correction_count == 1


@pytest.mark.parametrize(
    ("state", "message"),
    [
        (
            SessionState(
                1,
                1,
                101,
                201,
                queue=[10, 10],
                members={10: _member(10, "A")},
            ),
            "duplicate user IDs",
        ),
        (
            SessionState(1, 1, 101, 201, queue=[10], members={}),
            "member map are inconsistent",
        ),
        (
            SessionState(
                1,
                1,
                101,
                201,
                phase=SessionPhase.SELECTING,
                queue=[10],
                members={10: _member(10, "A")},
                current_index=2,
            ),
            "current index is outside",
        ),
        (
            SessionState(
                1,
                1,
                101,
                201,
                queue=[10],
                members={10: _member(10, "A")},
                current_index=0,
            ),
            "waiting sessions cannot",
        ),
        (
            SessionState(
                1,
                1,
                101,
                201,
                phase=SessionPhase.READING,
                queue=[10],
                members={10: _member(10, "A")},
                current_index=0,
            ),
            "requires an active reading",
        ),
        (
            SessionState(
                1,
                1,
                101,
                201,
                phase=SessionPhase.SELECTING,
                queue=[10],
                members={10: _member(10, "A")},
                current_index=0,
                skip_votes={20},
            ),
            "outside the queue",
        ),
    ],
)
def test_session_state_rejects_invalid_invariants(
    state: SessionState,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        state.validate()

