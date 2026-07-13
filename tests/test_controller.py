from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lecturabot.config import BotConfig, ChannelPairConfig
from lecturabot.controller import LecturaController
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
from lecturabot.service import SessionError, Transition
from lecturabot.views import QueueView


class _FakeMessage:
    def __init__(self, message_id: int, name: str, events: list[str]) -> None:
        self.id = message_id
        self.name = name
        self.events = events
        self.edit_calls: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.events.append(f"{self.name}.edit")
        self.edit_calls.append(kwargs)


class _FakeChannel:
    def __init__(
        self,
        *,
        new_message: _FakeMessage,
        old_message: _FakeMessage,
        events: list[str],
    ) -> None:
        self.new_message = new_message
        self.old_message = old_message
        self.events = events
        self.send_calls: list[dict[str, Any]] = []
        self.fetch_calls: list[int] = []

    async def send(self, **kwargs: Any) -> _FakeMessage:
        self.events.append("send")
        self.send_calls.append(kwargs)
        return self.new_message

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        self.events.append(f"fetch:{message_id}")
        self.fetch_calls.append(message_id)
        if message_id != self.old_message.id:
            raise AssertionError(f"unexpected message fetch: {message_id}")
        return self.old_message


class _FakeService:
    def __init__(self, events: list[str], *, fail_persistence: bool = False) -> None:
        self.events = events
        self.fail_persistence = fail_persistence
        self.set_queue_calls: list[tuple[int, int]] = []

    async def set_queue_message(
        self,
        text_channel_id: int,
        message_id: int,
    ) -> None:
        self.events.append("persist")
        self.set_queue_calls.append((text_channel_id, message_id))
        if self.fail_persistence:
            raise RuntimeError("database write failed")


class _ShowQueueService(_FakeService):
    def __init__(self, events: list[str], state: SessionState) -> None:
        super().__init__(events)
        self.state = state

    async def get_or_create_session(self, **_: Any) -> SessionState:
        self.events.append("get_session")
        return self.state


class _FakeInteractionResponse:
    def __init__(self) -> None:
        self.defer_calls: list[dict[str, Any]] = []
        self.send_message_calls: list[tuple[str, dict[str, Any]]] = []
        self._done = False

    async def defer(self, **kwargs: Any) -> None:
        self.defer_calls.append(kwargs)
        self._done = True

    def is_done(self) -> bool:
        return self._done

    async def send_message(self, message: str, **kwargs: Any) -> None:
        self.send_message_calls.append((message, kwargs))
        self._done = True


class _FakeFollowup:
    def __init__(self) -> None:
        self.send_calls: list[tuple[str, dict[str, Any]]] = []

    async def send(self, message: str, **kwargs: Any) -> None:
        self.send_calls.append((message, kwargs))


class _FakeInteraction:
    guild_id = 1

    def __init__(self, message: _FakeMessage) -> None:
        self.response = _FakeInteractionResponse()
        self.followup = _FakeFollowup()
        self.message = message
        self.edit_calls: list[dict[str, Any]] = []

    async def edit_original_response(self, **kwargs: Any) -> _FakeMessage:
        self.edit_calls.append(kwargs)
        return self.message


def _controller(
    service: object,
    *,
    channel_pairs: tuple[ChannelPairConfig, ...] = (),
) -> LecturaController:
    config = BotConfig(
        token="test-token",
        guild_id=1,
        bot_status_contact_user_id=900,
        issue_contact_user_id=901,
        database_path=Path("test.sqlite3"),
        channel_pairs=channel_pairs,
    )
    return LecturaController(
        bot=object(),  # type: ignore[arg-type]
        config=config,
        service=service,  # type: ignore[arg-type]
    )


class _CorrectionService:
    def __init__(
        self,
        state: SessionState,
        *,
        error: SessionError | None = None,
    ) -> None:
        self.state = state
        self.error = error
        self.find_calls: list[int] = []
        self.add_calls: list[dict[str, Any]] = []

    async def find_by_reading_message(self, message_id: int) -> SessionState:
        self.find_calls.append(message_id)
        return self.state

    async def add_corrections(self, **kwargs: Any) -> Transition:
        self.add_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return Transition(self.state, "Corrections saved.")


class _ReplyMember:
    bot = False

    def __init__(self, user_id: int, display_name: str) -> None:
        self.id = user_id
        self.display_name = display_name
        self.voice = None


class _ReplyMessage:
    def __init__(self, author: _ReplyMember, content: str) -> None:
        self.author = author
        self.content = content
        self.reference = type("Reference", (), {"message_id": 700})()
        self.guild = type("Guild", (), {"id": 1})()
        self.channel = type("Channel", (), {"id": 101})()


class _ModalInteraction:
    def __init__(self) -> None:
        self.response = _FakeInteractionResponse()
        self.followup = _FakeFollowup()


def _state() -> SessionState:
    return SessionState(
        session_id=12_007,
        guild_id=1,
        text_channel_id=101,
        voice_channel_id=201,
        queue_message_id=111,
    )


def _reading_state() -> SessionState:
    return SessionState(
        session_id=12_008,
        guild_id=1,
        text_channel_id=101,
        voice_channel_id=201,
        phase=SessionPhase.READING,
        queue=[10, 20],
        members={
            10: MemberState(10, "Reader"),
            20: MemberState(20, "Listener"),
        },
        current_index=0,
        turn_started_at=1_000,
        active_reading=ActiveReading(
            reader_id=10,
            reader_display_name="Reader",
            language=Language.ENGLISH,
            level=Level.BEGINNER,
            body="They produce an apple.",
            started_at=1_000,
            message_id=700,
        ),
    )


@pytest.mark.asyncio
async def test_repost_queue_persists_new_panel_before_retiring_old_panel() -> None:
    events: list[str] = []
    old_message = _FakeMessage(111, "old", events)
    new_message = _FakeMessage(222, "new", events)
    channel = _FakeChannel(
        new_message=new_message,
        old_message=old_message,
        events=events,
    )
    service = _FakeService(events)
    controller = _controller(service)

    async def text_channel(_channel_id: int) -> _FakeChannel:
        return channel

    controller._text_channel = text_channel  # type: ignore[method-assign]

    result = await controller._refresh_queue(_state(), repost=True)

    assert result is new_message
    assert service.set_queue_calls == [(101, 222)]
    assert channel.fetch_calls == [111]
    assert events == ["send", "persist", "fetch:111", "old.edit"]
    assert old_message.edit_calls == [{"view": None}]
    assert new_message.edit_calls == []
    assert len(channel.send_calls) == 1
    assert isinstance(channel.send_calls[0]["view"], QueueView)


@pytest.mark.asyncio
async def test_repost_queue_persistence_failure_retires_only_new_panel() -> None:
    events: list[str] = []
    old_message = _FakeMessage(111, "old", events)
    new_message = _FakeMessage(222, "new", events)
    channel = _FakeChannel(
        new_message=new_message,
        old_message=old_message,
        events=events,
    )
    service = _FakeService(events, fail_persistence=True)
    controller = _controller(service)

    async def text_channel(_channel_id: int) -> _FakeChannel:
        return channel

    controller._text_channel = text_channel  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="database write failed"):
        await controller._refresh_queue(_state(), repost=True)

    assert service.set_queue_calls == [(101, 222)]
    assert events == ["send", "persist", "new.edit"]
    assert new_message.edit_calls == [{"view": None}]
    assert channel.fetch_calls == []
    assert old_message.edit_calls == []


@pytest.mark.asyncio
async def test_show_queue_uses_public_interaction_response_for_fresh_panel() -> None:
    events: list[str] = []
    state = _state()
    service = _ShowQueueService(events, state)
    controller = _controller(service)
    pair = ChannelPairConfig(
        name="sandbox-1",
        text_channel_id=101,
        voice_channel_id=201,
        mode=ChannelMode.STANDARD,
    )
    interaction = _FakeInteraction(_FakeMessage(222, "public", events))

    controller._interaction_context = lambda _interaction: (  # type: ignore[method-assign]
        pair,
        object(),
    )
    controller._require_matching_voice = (  # type: ignore[method-assign]
        lambda _member, _pair: None
    )

    async def retire(text_channel_id: int, message_id: int | None) -> None:
        events.append(f"retire:{text_channel_id}:{message_id}")

    controller._retire_message = retire  # type: ignore[method-assign]

    await controller.show_queue(interaction)  # type: ignore[arg-type]

    assert interaction.response.defer_calls == [{"thinking": True}]
    assert len(interaction.edit_calls) == 1
    assert interaction.edit_calls[0]["embed"] is not None
    assert isinstance(interaction.edit_calls[0]["view"], QueueView)
    assert interaction.edit_calls[0]["allowed_mentions"].everyone is False
    assert service.set_queue_calls == [(101, 222)]
    assert events == ["get_session", "persist", "retire:101:111"]


@pytest.mark.asyncio
async def test_turn_transition_reposts_queue_before_sending_next_picker() -> None:
    events: list[str] = []
    service = _FakeService(events)
    controller = _controller(service)
    state = _state()

    async def retire(_channel_id: int, message_id: int | None) -> None:
        events.append(f"retire:{message_id}")

    async def refresh(
        refreshed_state: SessionState,
        *,
        repost: bool = False,
    ) -> _FakeMessage:
        assert refreshed_state is state
        events.append(f"refresh:{repost}")
        return _FakeMessage(222, "new", events)

    async def send_picker(picker_state: SessionState) -> None:
        assert picker_state is state
        events.append("picker")

    controller._retire_message = retire  # type: ignore[method-assign]
    controller._refresh_queue = refresh  # type: ignore[method-assign]
    controller._send_picker = send_picker  # type: ignore[method-assign]

    await controller._apply_transition(
        Transition(
            state=state,
            notice="Turn completed.",
            activated_reader_id=20,
            retired_picker_message_id=500,
            retired_reading_message_id=600,
            advanced=True,
            repost_queue=True,
        )
    )

    assert events == [
        "retire:500",
        "retire:600",
        "refresh:True",
        "picker",
    ]


@pytest.mark.asyncio
async def test_reply_to_reading_submits_corrections_and_refreshes_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = _reading_state()
    service = _CorrectionService(state)
    pair = ChannelPairConfig(
        name="sandbox-1",
        text_channel_id=101,
        voice_channel_id=201,
        mode=ChannelMode.STANDARD,
    )
    controller = _controller(service, channel_pairs=(pair,))
    controller._require_matching_voice = (  # type: ignore[method-assign]
        lambda _member, _pair: None
    )
    refreshed: list[SessionState] = []

    async def refresh(refreshed_state: SessionState) -> bool:
        refreshed.append(refreshed_state)
        return True

    controller._refresh_reading = refresh  # type: ignore[method-assign]
    monkeypatch.setattr("lecturabot.controller.discord.Member", _ReplyMember)
    reply = _ReplyMessage(
        _ReplyMember(20, "Listener"),
        "produce (noun), 🍎,",
    )

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert service.find_calls == [700]
    assert len(service.add_calls) == 1
    assert service.add_calls[0]["text_channel_id"] == 101
    assert service.add_calls[0]["reading_message_id"] == 700
    assert service.add_calls[0]["corrector_id"] == 20
    assert service.add_calls[0]["items"] == ["produce (noun)", "🍎"]
    assert service.add_calls[0]["source"] is CorrectionSource.REPLY
    assert refreshed == [state]


@pytest.mark.asyncio
async def test_modal_reports_all_unmatched_corrections_ephemerally() -> None:
    state = _reading_state()
    user_message = (
        "No se encontraron: banana (noun), 🐕. / "
        "Not found: banana (noun), 🐕."
    )
    service = _CorrectionService(
        state,
        error=SessionError("correction_not_in_text", user_message),
    )
    pair = ChannelPairConfig(
        name="sandbox-1",
        text_channel_id=101,
        voice_channel_id=201,
        mode=ChannelMode.STANDARD,
    )
    member = _ReplyMember(20, "Listener")
    interaction = _ModalInteraction()
    controller = _controller(service, channel_pairs=(pair,))
    controller._interaction_context = (  # type: ignore[method-assign]
        lambda _interaction: (pair, member)
    )
    controller._require_matching_voice = (  # type: ignore[method-assign]
        lambda _member, _pair: None
    )

    await controller.submit_corrections(
        interaction,  # type: ignore[arg-type]
        text_channel_id=101,
        reading_message_id=700,
        raw_items="apple, banana (noun), 🐕",
    )

    assert interaction.response.defer_calls == [
        {"ephemeral": True, "thinking": True}
    ]
    assert len(service.add_calls) == 1
    assert service.add_calls[0]["items"] == [
        "apple",
        "banana (noun)",
        "🐕",
    ]
    assert interaction.response.send_message_calls == []
    assert len(interaction.followup.send_calls) == 1
    sent_message, sent_kwargs = interaction.followup.send_calls[0]
    assert sent_message == user_message
    assert sent_kwargs["ephemeral"] is True
    assert sent_kwargs["allowed_mentions"].everyone is False
