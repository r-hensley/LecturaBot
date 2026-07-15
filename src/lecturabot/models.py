"""Domain models and versioned serialization for active reading sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


SESSION_SNAPSHOT_VERSION = 1


class ChannelMode(StrEnum):
    STANDARD = "standard"
    CUSTOM_ONLY = "custom_only"


class Language(StrEnum):
    SPANISH = "es"
    ENGLISH = "en"


class Level(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class SessionPhase(StrEnum):
    WAITING = "waiting"
    SELECTING = "selecting"
    READING = "reading"


class CorrectionSource(StrEnum):
    BUTTON = "button"
    REPLY = "reply"


@dataclass(frozen=True, slots=True)
class ReadingText:
    id: int
    language: Language
    level: Level
    body: str
    expected_emotion: str | None = None


@dataclass(slots=True)
class MemberState:
    user_id: int
    display_name: str
    turns: int = 0
    total_seconds: int = 0

    @property
    def average_seconds(self) -> int | None:
        if self.turns == 0:
            return None
        return self.total_seconds // self.turns

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "turns": self.turns,
            "total_seconds": self.total_seconds,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemberState:
        return cls(
            user_id=int(data["user_id"]),
            display_name=str(data["display_name"]),
            turns=int(data.get("turns", 0)),
            total_seconds=int(data.get("total_seconds", 0)),
        )


@dataclass(frozen=True, slots=True)
class CorrectionEntry:
    text: str
    source: CorrectionSource
    discarded: bool = False
    match_text: str | None = None

    @property
    def target_text(self) -> str:
        """Stable comparison text for this listed suggestion.

        Matched entries use their source-text target. Unmatched feedback falls
        back to the text the corrector submitted so it can still participate in
        counting and duplicate handling without becoming a reading highlight.
        """
        return self.match_text or self.text

    def to_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "source": self.source.value,
            "discarded": self.discarded,
            # An explicit null means "list this entry without highlighting".
            "match_text": self.match_text,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrectionEntry:
        return cls(
            text=str(data["text"]),
            source=CorrectionSource(str(data["source"])),
            # Snapshots written before duplicate tracking did not include this
            # field. Treat their entries as accepted rather than rejecting an
            # otherwise valid active session during startup recovery.
            discarded=bool(data.get("discarded", False)),
            # Older snapshots had no match_text field and used the displayed
            # correction for matching. Explicit null is the new representation
            # for feedback that should be listed without a source highlight.
            match_text=(
                str(data["text"])
                if "match_text" not in data
                else (
                    None
                    if data["match_text"] is None
                    else str(data["match_text"])
                )
            ),
        )


@dataclass(slots=True)
class CorrectionGroup:
    user_id: int
    display_name: str
    entries: list[CorrectionEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "user_id": self.user_id,
            "display_name": self.display_name,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CorrectionGroup:
        return cls(
            user_id=int(data["user_id"]),
            display_name=str(data["display_name"]),
            entries=[
                CorrectionEntry.from_dict(entry)
                for entry in data.get("entries", [])
            ],
        )


@dataclass(slots=True)
class ActiveReading:
    reader_id: int
    reader_display_name: str
    language: Language
    level: Level | None
    body: str
    started_at: int
    expected_emotion: str | None = None
    custom_language_label: str | None = None
    source_text_id: int | None = None
    message_id: int | None = None
    correction_groups: list[CorrectionGroup] = field(default_factory=list)

    @property
    def correction_count(self) -> int:
        """Count normalized matched targets or unmatched feedback once."""
        return len(
            {
                self._normalize_correction(entry.target_text)
                for group in self.correction_groups
                for entry in group.entries
                if self._normalize_correction(entry.target_text)
            }
        )

    @property
    def correction_texts(self) -> list[str]:
        return [
            entry.match_text
            for group in self.correction_groups
            for entry in group.entries
            if entry.match_text
        ]

    def add_corrections(
        self,
        *,
        corrector_id: int,
        corrector_display_name: str,
        items: list[str],
        source: CorrectionSource,
        match_texts: list[str | None] | None = None,
    ) -> None:
        if match_texts is None:
            match_texts = items
        if len(match_texts) != len(items):
            raise ValueError("match_texts must align with correction items")
        group = next(
            (
                existing
                for existing in self.correction_groups
                if existing.user_id == corrector_id
            ),
            None,
        )
        if group is None:
            group = CorrectionGroup(corrector_id, corrector_display_name)
            self.correction_groups.append(group)
        group.display_name = corrector_display_name

        # Groups are organized by corrector for display, not submission time.
        # Persist the discard decision now so a later submission to an earlier
        # group cannot make rendering infer the chronology incorrectly.
        owners_by_suggestion: dict[str, set[int]] = {}
        for existing_group in self.correction_groups:
            for entry in existing_group.entries:
                normalized = self._normalize_correction(entry.target_text)
                if normalized:
                    owners_by_suggestion.setdefault(normalized, set()).add(
                        existing_group.user_id
                    )

        for item, match_text in zip(items, match_texts, strict=True):
            normalized = self._normalize_correction(match_text or item)
            prior_owners = owners_by_suggestion.get(normalized, set())
            discarded = bool(prior_owners - {corrector_id})
            group.entries.append(
                CorrectionEntry(
                    item,
                    source,
                    discarded=discarded,
                    match_text=match_text,
                )
            )
            if normalized:
                owners_by_suggestion.setdefault(normalized, set()).add(
                    corrector_id
                )

    @staticmethod
    def _normalize_correction(value: str) -> str:
        """Return the comparison key used for duplicate suggestions."""
        return " ".join(value.split()).casefold()

    def to_dict(self) -> dict[str, Any]:
        return {
            "reader_id": self.reader_id,
            "reader_display_name": self.reader_display_name,
            "language": self.language.value,
            "level": None if self.level is None else self.level.value,
            "body": self.body,
            "started_at": self.started_at,
            "expected_emotion": self.expected_emotion,
            "custom_language_label": self.custom_language_label,
            "source_text_id": self.source_text_id,
            "message_id": self.message_id,
            "correction_groups": [
                group.to_dict() for group in self.correction_groups
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActiveReading:
        return cls(
            reader_id=int(data["reader_id"]),
            reader_display_name=str(data["reader_display_name"]),
            language=Language(str(data["language"])),
            level=(
                None if data.get("level") is None else Level(str(data["level"]))
            ),
            body=str(data["body"]),
            started_at=int(data["started_at"]),
            expected_emotion=data.get("expected_emotion"),
            custom_language_label=data.get("custom_language_label"),
            source_text_id=data.get("source_text_id"),
            message_id=data.get("message_id"),
            correction_groups=[
                CorrectionGroup.from_dict(group)
                for group in data.get("correction_groups", [])
            ],
        )


@dataclass(slots=True)
class SessionState:
    """Authoritative state for one configured text/voice channel pair.

    Stored queue order represents a durable circular rotation while
    ``current_index`` identifies the active reader. Renderers may rotate that
    order to show turn-relative positions without mutating fairness.
    """

    session_id: int
    guild_id: int
    text_channel_id: int
    voice_channel_id: int
    channel_mode: ChannelMode = ChannelMode.STANDARD
    phase: SessionPhase = SessionPhase.WAITING
    queue: list[int] = field(default_factory=list)
    members: dict[int, MemberState] = field(default_factory=dict)
    current_index: int | None = None
    turn_started_at: int | None = None
    active_reading: ActiveReading | None = None
    skip_votes: set[int] = field(default_factory=set)
    used_text_ids: set[int] = field(default_factory=set)
    seen_text_ids_by_user: dict[int, set[int]] = field(default_factory=dict)
    queue_message_id: int | None = None
    picker_message_id: int | None = None
    revision: int = 0

    @property
    def current_user_id(self) -> int | None:
        if self.current_index is None or not self.queue:
            return None
        if not 0 <= self.current_index < len(self.queue):
            return None
        return self.queue[self.current_index]

    @property
    def current_member(self) -> MemberState | None:
        current_user_id = self.current_user_id
        if current_user_id is None:
            return None
        return self.members.get(current_user_id)

    def validate(self) -> None:
        if len(self.queue) != len(set(self.queue)):
            raise ValueError("queue contains duplicate user IDs")
        if set(self.queue) != set(self.members):
            raise ValueError("queue and member map are inconsistent")
        if self.current_index is not None and not (
            0 <= self.current_index < len(self.queue)
        ):
            raise ValueError("current index is outside the queue")
        if self.phase is SessionPhase.WAITING and self.current_index is not None:
            raise ValueError("waiting sessions cannot have a current reader")
        if self.phase is SessionPhase.READING and self.active_reading is None:
            raise ValueError("reading phase requires an active reading")
        if self.active_reading is not None:
            if self.phase is not SessionPhase.READING:
                raise ValueError("active reading requires reading phase")
            if self.active_reading.reader_id != self.current_user_id:
                raise ValueError("active reading does not belong to current reader")
        if not self.skip_votes.issubset(self.members):
            raise ValueError("skip votes contain users outside the queue")

    def to_dict(self) -> dict[str, Any]:
        self.validate()
        return {
            "snapshot_version": SESSION_SNAPSHOT_VERSION,
            "session_id": self.session_id,
            "guild_id": self.guild_id,
            "text_channel_id": self.text_channel_id,
            "voice_channel_id": self.voice_channel_id,
            "channel_mode": self.channel_mode.value,
            "phase": self.phase.value,
            "queue": self.queue,
            "members": {
                str(user_id): member.to_dict()
                for user_id, member in self.members.items()
            },
            "current_index": self.current_index,
            "turn_started_at": self.turn_started_at,
            "active_reading": (
                None if self.active_reading is None else self.active_reading.to_dict()
            ),
            "skip_votes": sorted(self.skip_votes),
            "used_text_ids": sorted(self.used_text_ids),
            "seen_text_ids_by_user": {
                str(user_id): sorted(text_ids)
                for user_id, text_ids in self.seen_text_ids_by_user.items()
            },
            "queue_message_id": self.queue_message_id,
            "picker_message_id": self.picker_message_id,
            "revision": self.revision,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SessionState:
        version = int(data.get("snapshot_version", 0))
        if version != SESSION_SNAPSHOT_VERSION:
            raise ValueError(f"unsupported session snapshot version: {version}")
        used_text_ids = {
            int(text_id) for text_id in data.get("used_text_ids", [])
        }
        if "seen_text_ids_by_user" in data:
            seen_text_ids_by_user = {
                int(user_id): {int(text_id) for text_id in text_ids}
                for user_id, text_ids in data["seen_text_ids_by_user"].items()
            }
        else:
            # Conservatively migrate the old room-wide history to every
            # currently queued reader; an empty queue is reset by the service.
            seen_text_ids_by_user = {
                int(user_id): set(used_text_ids)
                for user_id in data.get("members", {})
            }
        state = cls(
            session_id=int(data["session_id"]),
            guild_id=int(data["guild_id"]),
            text_channel_id=int(data["text_channel_id"]),
            voice_channel_id=int(data["voice_channel_id"]),
            channel_mode=ChannelMode(str(data.get("channel_mode", "standard"))),
            phase=SessionPhase(str(data.get("phase", "waiting"))),
            queue=[int(user_id) for user_id in data.get("queue", [])],
            members={
                int(user_id): MemberState.from_dict(member)
                for user_id, member in data.get("members", {}).items()
            },
            current_index=data.get("current_index"),
            turn_started_at=data.get("turn_started_at"),
            active_reading=(
                None
                if data.get("active_reading") is None
                else ActiveReading.from_dict(data["active_reading"])
            ),
            skip_votes={int(user_id) for user_id in data.get("skip_votes", [])},
            used_text_ids=used_text_ids,
            seen_text_ids_by_user=seen_text_ids_by_user,
            queue_message_id=data.get("queue_message_id"),
            picker_message_id=data.get("picker_message_id"),
            revision=int(data.get("revision", 0)),
        )
        state.validate()
        return state
