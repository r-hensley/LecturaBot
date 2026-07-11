from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from lecturabot.config import BotConfig, ChannelPairConfig
from lecturabot.controller import LecturaController
from lecturabot.models import ChannelMode, SessionState
from lecturabot.service import Transition
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

    async def defer(self, **kwargs: Any) -> None:
        self.defer_calls.append(kwargs)


class _FakeInteraction:
    guild_id = 1

    def __init__(self, message: _FakeMessage) -> None:
        self.response = _FakeInteractionResponse()
        self.message = message
        self.edit_calls: list[dict[str, Any]] = []

    async def edit_original_response(self, **kwargs: Any) -> _FakeMessage:
        self.edit_calls.append(kwargs)
        return self.message


def _controller(service: _FakeService) -> LecturaController:
    config = BotConfig(
        token="test-token",
        guild_id=1,
        bug_contact_user_id=900,
        text_contact_user_id=901,
        database_path=Path("test.sqlite3"),
        channel_pairs=(),
    )
    return LecturaController(
        bot=object(),  # type: ignore[arg-type]
        config=config,
        service=service,  # type: ignore[arg-type]
    )


def _state() -> SessionState:
    return SessionState(
        session_id=12_007,
        guild_id=1,
        text_channel_id=101,
        voice_channel_id=201,
        queue_message_id=111,
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
