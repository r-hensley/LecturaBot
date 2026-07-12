"""Locked session state machine independent of Discord transport objects."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from dataclasses import dataclass
import random
import re
import time

from .config import ChannelPairConfig
from .models import (
    ActiveReading,
    ChannelMode,
    CorrectionSource,
    Language,
    Level,
    MemberState,
    ReadingText,
    SessionPhase,
    SessionState,
)
from .repository import SQLiteRepository


class SessionError(RuntimeError):
    """A rejected domain transition with a stable code and bilingual copy."""

    def __init__(self, code: str, user_message: str) -> None:
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message


@dataclass(frozen=True, slots=True)
class Transition:
    """Committed state plus the Discord messages affected by the change."""

    state: SessionState
    notice: str
    activated_reader_id: int | None = None
    retired_picker_message_id: int | None = None
    retired_reading_message_id: int | None = None
    vote_count: int = 0
    votes_required: int = 0
    advanced: bool = False
    repost_queue: bool = False


def parse_correction_lines(
    raw_value: str,
    *,
    max_items: int = 20,
    max_item_length: int = 100,
) -> list[str]:
    items = [" ".join(line.split()) for line in raw_value.splitlines()]
    items = [item for item in items if item]
    if not items:
        raise SessionError(
            "empty_corrections",
            "Incluye al menos una corrección. / Include at least one correction.",
        )
    if len(items) > max_items:
        raise SessionError(
            "too_many_corrections",
            f"Máximo {max_items} correcciones por envío. / "
            f"Maximum {max_items} corrections per submission.",
        )
    if any(len(item) > max_item_length for item in items):
        raise SessionError(
            "correction_too_long",
            f"Cada corrección debe tener {max_item_length} caracteres o menos. / "
            f"Each correction must be {max_item_length} characters or fewer.",
        )
    return items


def _correction_pattern(correction: str) -> str:
    pattern = r"\s+".join(re.escape(part) for part in correction.split())
    if correction[0].isalnum():
        pattern = rf"(?<!\w){pattern}"
    if correction[-1].isalnum():
        pattern = rf"{pattern}(?!\w)"
    return pattern


def _contains_correction(body: str, correction: str) -> bool:
    return re.search(_correction_pattern(correction), body, re.IGNORECASE) is not None


class SessionService:
    """Coordinate queue and reading transitions for every channel pair.

    One lock per text channel serializes races within a session while allowing
    the six normal rooms and the other-language room to run concurrently.
    Public results contain defensive snapshots, never the mutable cached state.
    """

    def __init__(
        self,
        repository: SQLiteRepository,
        *,
        minimum_participants: int = 2,
        maximum_participants: int = 25,
        skip_vote_threshold: int = 3,
        maximum_correction_entries: int = 20,
        maximum_correction_characters: int = 1_400,
        clock: Callable[[], int] | None = None,
        chooser: Callable[[Sequence[ReadingText]], ReadingText] | None = None,
    ) -> None:
        if minimum_participants < 2:
            raise ValueError("minimum_participants must be at least 2")
        if maximum_participants < minimum_participants:
            raise ValueError(
                "maximum_participants cannot be below minimum_participants"
            )
        if skip_vote_threshold < 1:
            raise ValueError("skip_vote_threshold must be positive")
        if maximum_correction_entries < 1:
            raise ValueError("maximum_correction_entries must be positive")
        if maximum_correction_characters < 1:
            raise ValueError("maximum_correction_characters must be positive")
        self.repository = repository
        self.minimum_participants = minimum_participants
        self.maximum_participants = maximum_participants
        self.skip_vote_threshold = skip_vote_threshold
        self.maximum_correction_entries = maximum_correction_entries
        self.maximum_correction_characters = maximum_correction_characters
        self._clock = clock or (lambda: int(time.time()))
        self._chooser = chooser or random.choice
        self._states: dict[int, SessionState] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def initialize(self) -> None:
        """Load snapshots and repair states interrupted before publication."""
        for state in await self.repository.load_all_sessions():
            changed = False
            if (
                state.phase is SessionPhase.READING
                and state.active_reading is not None
                and state.active_reading.message_id is None
            ):
                # The process stopped after choosing a text but before its
                # Discord message ID was committed. Keep the original picker
                # usable when possible instead of leaving a phantom reading.
                state.phase = SessionPhase.SELECTING
                state.active_reading = None
                changed = True
            if changed:
                await self.repository.save_session(state)
            self._states[state.text_channel_id] = state

    def _lock_for(self, text_channel_id: int) -> asyncio.Lock:
        return self._locks.setdefault(text_channel_id, asyncio.Lock())

    async def _load_locked(self, text_channel_id: int) -> SessionState | None:
        state = self._states.get(text_channel_id)
        if state is not None:
            return state
        state = await self.repository.load_session(text_channel_id)
        if state is not None:
            self._states[text_channel_id] = state
        return state

    @staticmethod
    def _snapshot(state: SessionState) -> SessionState:
        # Serialization gives us a deep copy and continuously exercises the
        # restart format used by SQLite.
        return SessionState.from_dict(state.to_dict())

    async def _persist_locked(self, state: SessionState) -> None:
        """Commit a working copy before replacing the in-memory cache."""
        await self.repository.save_session(state)
        self._states[state.text_channel_id] = state

    async def get_or_create_session(
        self,
        *,
        guild_id: int,
        pair: ChannelPairConfig,
    ) -> SessionState:
        async with self._lock_for(pair.text_channel_id):
            state = await self._load_locked(pair.text_channel_id)
            if state is None:
                state = SessionState(
                    session_id=await self.repository.allocate_session_id(),
                    guild_id=guild_id,
                    text_channel_id=pair.text_channel_id,
                    voice_channel_id=pair.voice_channel_id,
                    channel_mode=pair.mode,
                )
                await self._persist_locked(state)
            elif (
                state.guild_id != guild_id
                or state.voice_channel_id != pair.voice_channel_id
                or state.channel_mode is not pair.mode
            ):
                raise SessionError(
                    "pair_mismatch",
                    "La configuración del canal cambió; reinicia esta sesión. / "
                    "The channel configuration changed; reset this session.",
                )
            return self._snapshot(state)

    async def get_session(self, text_channel_id: int) -> SessionState | None:
        async with self._lock_for(text_channel_id):
            state = await self._load_locked(text_channel_id)
            return None if state is None else self._snapshot(state)

    async def set_queue_message(
        self, text_channel_id: int, message_id: int
    ) -> SessionState:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            state.queue_message_id = message_id
            await self._persist_locked(state)
            return self._snapshot(state)

    async def set_picker_message(
        self,
        *,
        text_channel_id: int,
        reader_id: int,
        message_id: int,
    ) -> SessionState:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_current_reader(state, reader_id)
            if state.phase is not SessionPhase.SELECTING:
                raise self._wrong_phase("selecting")
            state.picker_message_id = message_id
            await self._persist_locked(state)
            return self._snapshot(state)

    async def set_reading_message(
        self,
        *,
        text_channel_id: int,
        reader_id: int,
        message_id: int,
    ) -> SessionState:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_current_reader(state, reader_id)
            if state.phase is not SessionPhase.READING or state.active_reading is None:
                raise self._wrong_phase("reading")
            state.active_reading.message_id = message_id
            # The old picker remains recoverable until the reading message is
            # durably identified. Once this commit succeeds it becomes stale.
            state.picker_message_id = None
            await self._persist_locked(state)
            return self._snapshot(state)

    async def join(
        self,
        *,
        text_channel_id: int,
        user_id: int,
        display_name: str,
    ) -> Transition:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            if user_id in state.members:
                raise SessionError(
                    "already_queued",
                    "Ya estás en la cola. / You are already in the queue.",
                )
            if len(state.queue) >= self.maximum_participants:
                raise SessionError(
                    "queue_full",
                    f"La cola admite hasta {self.maximum_participants} personas. / "
                    f"The queue supports up to {self.maximum_participants} people.",
                )
            turns, total_seconds = await self.repository.get_user_stats(
                state.guild_id, user_id
            )
            state.queue.append(user_id)
            state.members[user_id] = MemberState(
                user_id=user_id,
                display_name=display_name,
                turns=turns,
                total_seconds=total_seconds,
            )
            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Te uniste a la cola. / You joined the queue.",
            )

    async def leave(self, *, text_channel_id: int, user_id: int) -> Transition:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            if user_id not in state.members:
                raise SessionError(
                    "not_queued",
                    "No estás en la cola. / You are not in the queue.",
                )

            retired_picker = state.picker_message_id
            retired_reading = (
                None if state.active_reading is None else state.active_reading.message_id
            )
            removed_index = state.queue.index(user_id)
            was_current = state.current_index == removed_index
            state.queue.pop(removed_index)
            state.members.pop(user_id)
            state.skip_votes.discard(user_id)

            activated_reader_id: int | None = None
            advanced = False
            fell_below_minimum = len(state.queue) < self.minimum_participants
            if fell_below_minimum:
                self._set_waiting(state)
            elif was_current:
                # Removing the last visible row wraps the turn to the first
                # remaining participant; otherwise the same index is next.
                next_index = removed_index % len(state.queue)
                self._activate_reader(state, next_index)
                activated_reader_id = state.current_user_id
                advanced = True
            elif (
                state.current_index is not None
                and removed_index < state.current_index
            ):
                state.current_index -= 1

            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Saliste de la cola. / You left the queue.",
                activated_reader_id=activated_reader_id,
                retired_picker_message_id=(
                    retired_picker
                    if was_current or fell_below_minimum
                    else None
                ),
                retired_reading_message_id=(
                    retired_reading
                    if was_current or fell_below_minimum
                    else None
                ),
                advanced=advanced,
                repost_queue=True,
            )

    async def start(self, *, text_channel_id: int, actor_id: int) -> Transition:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            if actor_id not in state.members:
                raise SessionError(
                    "not_queued",
                    "Únete a la cola antes de comenzar. / "
                    "Join the queue before starting.",
                )
            if state.phase is not SessionPhase.WAITING:
                raise SessionError(
                    "already_started",
                    "La sesión ya comenzó. / The session has already started.",
                )
            if len(state.queue) < self.minimum_participants:
                raise SessionError(
                    "not_enough_participants",
                    f"Se necesitan {self.minimum_participants} participantes. / "
                    f"{self.minimum_participants} participants are required.",
                )
            self._activate_reader(state, 0)
            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "La sesión comenzó. / The session started.",
                activated_reader_id=state.current_user_id,
                advanced=True,
                repost_queue=True,
            )

    async def begin_catalog_reading(
        self,
        *,
        text_channel_id: int,
        reader_id: int,
        picker_message_id: int,
        language: Language,
        level: Level,
    ) -> Transition:
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_selection(state, reader_id, picker_message_id)
            if state.channel_mode is ChannelMode.CUSTOM_ONLY:
                raise SessionError(
                    "custom_only",
                    "Este canal requiere tu propio texto. / "
                    "This channel requires your own text.",
                )
            candidates = await self.repository.list_texts(
                language=language,
                level=level,
            )
            if not candidates:
                raise SessionError(
                    "no_texts",
                    "No hay textos disponibles para esa opción. / "
                    "No texts are available for that option.",
                )
            unused = [text for text in candidates if text.id not in state.used_text_ids]
            if not unused:
                # Reset only this language/level pool; selections in other
                # pools should still retain their no-repeat history.
                state.used_text_ids.difference_update(
                    text.id for text in candidates
                )
                unused = candidates
            selected = self._chooser(unused)
            state.used_text_ids.add(selected.id)
            self._begin_reading(
                state,
                language=selected.language,
                level=selected.level,
                body=selected.body,
                expected_emotion=selected.expected_emotion,
                source_text_id=selected.id,
            )
            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Texto seleccionado. / Text selected.",
                retired_picker_message_id=picker_message_id,
            )

    async def begin_custom_reading(
        self,
        *,
        text_channel_id: int,
        reader_id: int,
        picker_message_id: int,
        language: Language,
        body: str,
        custom_language_label: str | None = None,
    ) -> Transition:
        body = body.strip()
        if not body:
            raise SessionError(
                "empty_text",
                "El texto no puede estar vacío. / The text cannot be empty.",
            )
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_selection(state, reader_id, picker_message_id)
            label = None
            if state.channel_mode is ChannelMode.CUSTOM_ONLY:
                label = (custom_language_label or "").strip()
                if not label:
                    raise SessionError(
                        "missing_language",
                        "Indica el idioma del texto. / Specify the text language.",
                    )
            self._begin_reading(
                state,
                language=language,
                level=None,
                body=body,
                expected_emotion=None,
                source_text_id=None,
                custom_language_label=label,
            )
            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Texto recibido. / Text received.",
                retired_picker_message_id=picker_message_id,
            )

    async def rollback_reading(
        self,
        *,
        text_channel_id: int,
        reader_id: int,
        picker_message_id: int | None = None,
    ) -> SessionState:
        """Restore selection after a reading could not be published."""
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_current_reader(state, reader_id)
            if state.phase is SessionPhase.READING:
                state.phase = SessionPhase.SELECTING
                state.active_reading = None
                if picker_message_id is not None:
                    state.picker_message_id = picker_message_id
                await self._persist_locked(state)
            return self._snapshot(state)

    async def pause_unpublished_selection(
        self,
        *,
        text_channel_id: int,
        reader_id: int,
    ) -> SessionState:
        """Pause a turn when Discord could not publish its picker."""
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_current_reader(state, reader_id)
            if (
                state.phase is SessionPhase.SELECTING
                and state.picker_message_id is None
            ):
                self._set_waiting(state)
                await self._persist_locked(state)
            return self._snapshot(state)

    async def add_corrections(
        self,
        *,
        text_channel_id: int,
        reading_message_id: int,
        corrector_id: int,
        corrector_display_name: str,
        items: list[str],
        source: CorrectionSource,
        validator: Callable[[ActiveReading], None] | None = None,
    ) -> Transition:
        """Add corrections only if the resulting reading remains publishable."""
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            reading = state.active_reading
            if state.phase is not SessionPhase.READING or reading is None:
                raise self._wrong_phase("reading")
            if reading.message_id != reading_message_id:
                raise SessionError(
                    "stale_reading",
                    "Ese texto ya no está activo. / That reading is no longer active.",
                )
            if corrector_id == reading.reader_id:
                raise SessionError(
                    "reader_correction",
                    "El lector no puede corregirse a sí mismo. / "
                    "The reader cannot submit their own corrections.",
                )
            seen_in_submission: set[str] = set()
            seen_by_corrector = {
                reading._normalize_correction(entry.text)
                for group in reading.correction_groups
                if group.user_id == corrector_id
                for entry in group.entries
            }
            for item in items:
                normalized = reading._normalize_correction(item)
                if normalized in seen_in_submission or normalized in seen_by_corrector:
                    raise SessionError(
                        "duplicate_correction",
                        "Esa corrección ya fue enviada por ti. / "
                        "You already submitted that correction.",
                    )
                seen_in_submission.add(normalized)
                if not _contains_correction(reading.body, item):
                    raise SessionError(
                        "correction_not_in_text",
                        "Esa corrección no aparece en el texto original. / "
                        "That correction does not appear in the original text.",
                    )
            existing_entries = sum(
                len(group.entries) for group in reading.correction_groups
            )
            existing_characters = sum(
                len(entry.text)
                for group in reading.correction_groups
                for entry in group.entries
            )
            if existing_entries + len(items) > self.maximum_correction_entries:
                raise SessionError(
                    "correction_summary_full",
                    "La lista de correcciones está llena; comparte lo demás en "
                    "voz. / The correction list is full; share anything else "
                    "in voice.",
                )
            if (
                existing_characters + sum(len(item) for item in items)
                > self.maximum_correction_characters
            ):
                raise SessionError(
                    "correction_summary_full",
                    "Las correcciones son demasiado largas para mostrarlas. / "
                    "The corrections are too long to display.",
                )
            reading.add_corrections(
                corrector_id=corrector_id,
                corrector_display_name=corrector_display_name,
                items=items,
                source=source,
            )
            if validator is not None:
                validator(reading)
            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Correcciones guardadas. / Corrections saved.",
            )

    async def pass_turn(
        self,
        *,
        text_channel_id: int,
        actor_id: int,
        source_message_id: int,
    ) -> Transition:
        """Complete the active turn; only the current reader may do this."""
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_active_source(state, source_message_id)
            self._require_current_reader(state, actor_id)

            retired_picker, retired_reading = self._active_message_ids(state)
            completed_reading = state.active_reading
            self._advance(state)
            if completed_reading is not None:
                duration = max(0, self._clock() - completed_reading.started_at)
                await self.repository.complete_turn_and_save_session(
                    state=state,
                    user_id=actor_id,
                    duration_seconds=duration,
                )
                self._states[state.text_channel_id] = state
            else:
                await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Turno completado. / Turn completed.",
                activated_reader_id=state.current_user_id,
                retired_picker_message_id=retired_picker,
                retired_reading_message_id=retired_reading,
                advanced=True,
                repost_queue=True,
            )

    async def vote_to_skip(
        self,
        *,
        text_channel_id: int,
        voter_id: int,
        source_message_id: int,
    ) -> Transition:
        """Record one queued participant's vote to skip an AFK reader."""
        async with self._lock_for(text_channel_id):
            state = self._snapshot(
                self._require_state(await self._load_locked(text_channel_id))
            )
            self._require_active_source(state, source_message_id)
            current_user_id = state.current_user_id
            if current_user_id is None:
                raise self._wrong_phase("active turn")
            if voter_id == current_user_id:
                raise SessionError(
                    "current_reader_skip_vote",
                    "Usa Pasar turno para terminar tu propio turno. / "
                    "Use Pass Turn to finish your own turn.",
                )
            if voter_id not in state.members:
                raise SessionError(
                    "not_queued",
                    "Solo participantes en cola pueden votar. / "
                    "Only queued participants can vote.",
                )
            if voter_id in state.skip_votes:
                raise SessionError(
                    "already_voted",
                    "Ya votaste para saltar este turno. / "
                    "You already voted to skip this turn.",
                )

            state.skip_votes.add(voter_id)
            required = self.skip_vote_threshold
            if len(state.skip_votes) < required:
                await self._persist_locked(state)
                return Transition(
                    self._snapshot(state),
                    f"Voto registrado ({len(state.skip_votes)}/{required}). / "
                    f"Vote recorded ({len(state.skip_votes)}/{required}).",
                    vote_count=len(state.skip_votes),
                    votes_required=required,
                )

            retired_picker, retired_reading = self._active_message_ids(state)
            self._advance(state)
            await self._persist_locked(state)
            return Transition(
                self._snapshot(state),
                "Se saltó el turno ausente. / The AFK turn was skipped.",
                activated_reader_id=state.current_user_id,
                retired_picker_message_id=retired_picker,
                retired_reading_message_id=retired_reading,
                vote_count=required,
                votes_required=required,
                advanced=True,
                repost_queue=True,
            )

    async def find_by_reading_message(
        self, message_id: int
    ) -> SessionState | None:
        for text_channel_id in list(self._states):
            async with self._lock_for(text_channel_id):
                state = await self._load_locked(text_channel_id)
                if (
                    state is not None
                    and state.active_reading is not None
                    and state.active_reading.message_id == message_id
                ):
                    return self._snapshot(state)
        return None

    @staticmethod
    def _require_state(state: SessionState | None) -> SessionState:
        if state is None:
            raise SessionError(
                "no_session",
                "Usa /lecturatest primero. / Use /lecturatest first.",
            )
        return state

    def _require_active_source(
        self,
        state: SessionState,
        source_message_id: int,
    ) -> None:
        """Reject buttons belonging to an older picker or reading message."""
        if state.phase not in (SessionPhase.SELECTING, SessionPhase.READING):
            raise self._wrong_phase("selecting or reading")
        expected_message_id = (
            state.picker_message_id
            if state.phase is SessionPhase.SELECTING
            else (
                None
                if state.active_reading is None
                else state.active_reading.message_id
            )
        )
        if expected_message_id != source_message_id:
            raise SessionError(
                "stale_turn",
                "Ese turno ya no está activo. / That turn is no longer active.",
            )

    @staticmethod
    def _require_current_reader(state: SessionState, user_id: int) -> None:
        if state.current_user_id != user_id:
            raise SessionError(
                "not_current_reader",
                "No es tu turno. / It is not your turn.",
            )

    def _require_selection(
        self,
        state: SessionState,
        reader_id: int,
        picker_message_id: int,
    ) -> None:
        self._require_current_reader(state, reader_id)
        if state.phase is not SessionPhase.SELECTING:
            raise self._wrong_phase("selecting")
        if state.picker_message_id != picker_message_id:
            raise SessionError(
                "stale_picker",
                "Ese selector ya no está activo. / That picker is no longer active.",
            )

    @staticmethod
    def _wrong_phase(expected: str) -> SessionError:
        return SessionError(
            "wrong_phase",
            f"La sesión no está en fase {expected}. / "
            f"The session is not in the {expected} phase.",
        )

    def _activate_reader(self, state: SessionState, index: int) -> None:
        state.current_index = index
        state.phase = SessionPhase.SELECTING
        state.turn_started_at = self._clock()
        state.active_reading = None
        state.skip_votes.clear()
        state.picker_message_id = None

    def _set_waiting(self, state: SessionState) -> None:
        state.phase = SessionPhase.WAITING
        state.current_index = None
        state.turn_started_at = None
        state.active_reading = None
        state.skip_votes.clear()
        state.picker_message_id = None

    def _begin_reading(
        self,
        state: SessionState,
        *,
        language: Language,
        level: Level | None,
        body: str,
        expected_emotion: str | None,
        source_text_id: int | None,
        custom_language_label: str | None = None,
    ) -> None:
        member = state.current_member
        if member is None:
            raise self._wrong_phase("active turn")
        state.phase = SessionPhase.READING
        state.active_reading = ActiveReading(
            reader_id=member.user_id,
            reader_display_name=member.display_name,
            language=language,
            level=level,
            body=body,
            started_at=self._clock(),
            expected_emotion=expected_emotion,
            source_text_id=source_text_id,
            custom_language_label=custom_language_label,
        )
        state.skip_votes.clear()
        # Keep the selector ID until the reading message ID is committed. This
        # gives failure and restart recovery a valid control to return to.

    def _advance(self, state: SessionState) -> None:
        # Keep durable join order stable and move only the current marker.
        # Rendering rotates this list so the active reader appears as #1.
        current_index = state.current_index or 0
        if len(state.queue) < self.minimum_participants:
            self._set_waiting(state)
            return
        next_index = (current_index + 1) % len(state.queue)
        self._activate_reader(state, next_index)

    @staticmethod
    def _active_message_ids(state: SessionState) -> tuple[int | None, int | None]:
        return (
            state.picker_message_id,
            None if state.active_reading is None else state.active_reading.message_id,
        )
