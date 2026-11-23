from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import discord
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
    QueueLayout,
    SessionPhase,
    SessionState,
)
from lecturabot.queue_panel import GUIDE_URL, QueuePanel, QueueSkipButton
from lecturabot.repository import SQLiteRepository
from lecturabot.rendering import CorrectionSummaryOverflow
from lecturabot.service import SessionError, SessionService, Transition
from lecturabot.views import CustomTextModal, ReadingView


BOT_USER_ID = 1234567890123456789


class _FakeMessage:
    def __init__(
        self,
        message_id: int,
        name: str,
        events: list[str],
        *,
        failed_edits: int = 0,
        v2: bool = False,
    ) -> None:
        self.id = message_id
        self.name = name
        self.events = events
        self.failed_edits = failed_edits
        self.flags = discord.MessageFlags(components_v2=v2)
        self.components = []
        self.edit_calls: list[dict[str, Any]] = []

    async def edit(self, **kwargs: Any) -> None:
        self.events.append(f"{self.name}.edit")
        self.edit_calls.append(kwargs)
        if self.failed_edits:
            self.failed_edits -= 1
            raise RuntimeError("message edit failed")

    async def delete(self) -> None:
        self.events.append(f"{self.name}.delete")


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
        if isinstance(kwargs.get("view"), discord.ui.LayoutView):
            self.new_message.flags.components_v2 = True
            self.new_message.components = [
                discord.components._component_factory(component)
                for component in kwargs["view"].to_components()
            ]
        return self.new_message

    async def fetch_message(self, message_id: int) -> _FakeMessage:
        self.events.append(f"fetch:{message_id}")
        self.fetch_calls.append(message_id)
        if message_id == self.old_message.id:
            return self.old_message
        if message_id == self.new_message.id:
            return self.new_message
        raise AssertionError(f"unexpected message fetch: {message_id}")

    def get_partial_message(self, message_id: int) -> _FakeMessage:
        assert message_id == self.new_message.id
        return self.new_message


class _FakeService:
    minimum_participants = 1
    skip_vote_threshold = 3
    def __init__(self, events: list[str], *, fail_persistence: bool = False) -> None:
        self.events = events
        self.fail_persistence = fail_persistence
        self.set_queue_calls: list[tuple[int, int]] = []

    async def set_queue_message(
        self,
        text_channel_id: int,
        message_id: int,
        *,
        includes_picker: bool = True,
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
    bot = SimpleNamespace(user=SimpleNamespace(id=BOT_USER_ID))
    return LecturaController(
        bot=bot,  # type: ignore[arg-type]
        config=config,
        service=service,  # type: ignore[arg-type]
    )


class _CorrectionService:
    def __init__(
        self,
        state: SessionState,
        *,
        error: SessionError | None = None,
        notice: str = "Corrections saved.",
    ) -> None:
        self.state = state
        self.error = error
        self.notice = notice
        self.find_calls: list[int] = []
        self.add_calls: list[dict[str, Any]] = []

    async def find_by_reading_message(self, message_id: int) -> SessionState | None:
        self.find_calls.append(message_id)
        reading = self.state.active_reading
        if reading is not None and (
            reading.message_id == message_id or message_id in reading.preceding_message_ids
        ):
            return self.state
        return None

    async def add_corrections(self, **kwargs: Any) -> Transition:
        self.add_calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return Transition(self.state, self.notice)


class _RolloverCorrectionService(_CorrectionService):
    def __init__(
        self,
        state: SessionState,
        events: list[str],
        *,
        fail_rollover: bool = False,
    ) -> None:
        super().__init__(state)
        self.events = events
        self.fail_rollover = fail_rollover
        self.rollover_calls: list[dict[str, Any]] = []

    async def add_corrections(self, **kwargs: Any) -> Transition:
        self.add_calls.append(kwargs)
        raise CorrectionSummaryOverflow("simulated full correction summary")

    async def rollover_corrections(self, **kwargs: Any) -> Transition:
        self.events.append("persist")
        self.rollover_calls.append(kwargs)
        if self.fail_rollover:
            raise RuntimeError("database write failed")
        reading = self.state.active_reading
        assert reading is not None
        reading.message_id = kwargs["new_reading_message_id"]
        return Transition(
            self.state,
            self.notice,
            retired_reading_message_id=kwargs["old_reading_message_id"],
            correction_rollover=True,
        )

    async def get_session(self, _: int) -> SessionState:
        return self.state


class _ReplacementService:
    def __init__(self, state: SessionState) -> None:
        self.state = state
        self.calls: list[dict[str, Any]] = []

    async def replace_catalog_reading(self, **kwargs: Any) -> Transition:
        self.calls.append(kwargs)
        return Transition(self.state, "A different text was selected.")


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
        self.attachments: list[object] = []
        self.stickers: list[object] = []
        self.reference = type("Reference", (), {"message_id": 700})()
        self.guild = type("Guild", (), {"id": 1})()
        self.channel = type("Channel", (), {"id": 101})()
        self.reply_calls: list[tuple[str, dict[str, Any]]] = []

    async def reply(self, content: str, **kwargs: Any) -> None:
        self.reply_calls.append((content, kwargs))


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
async def test_refresh_queue_edits_active_panel_without_resurfacing_it() -> None:
    events: list[str] = []
    old_message = _FakeMessage(111, "old", events, v2=True)
    channel = _FakeChannel(
        new_message=_FakeMessage(222, "new", events),
        old_message=old_message,
        events=events,
    )
    service = _FakeService(events)
    controller = _controller(service)

    async def text_channel(_channel_id: int) -> _FakeChannel:
        return channel

    controller._text_channel = text_channel  # type: ignore[method-assign]

    result = await controller._refresh_queue(_state())

    assert result is old_message
    assert channel.fetch_calls == [111]
    assert channel.send_calls == []
    assert service.set_queue_calls == []
    assert events == ["fetch:111", "old.edit"]
    assert len(old_message.edit_calls) == 1
    assert isinstance(old_message.edit_calls[0]["view"], QueuePanel)


async def test_legacy_selection_migrates_to_one_panel_before_retiring_messages() -> None:
    events: list[str] = []
    state = _reading_state()
    state.phase = SessionPhase.SELECTING
    state.active_reading = None
    state.queue_message_id = 111
    state.picker_message_id = 333
    channel = _FakeChannel(
        new_message=_FakeMessage(222, "new", events),
        old_message=_FakeMessage(111, "old", events),
        events=events,
    )
    controller = _controller(_FakeService(events))
    controller.queue_layout = QueueLayout.SINGLE
    controller._text_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]

    async def retire(_channel_id: int, message_id: int | None) -> None:
        events.append(f"retire:{message_id}")

    async def remove_picker(_state: SessionState, message_id: int | None) -> None:
        events.append(f"delete:{message_id}")

    controller._retire_message = retire  # type: ignore[method-assign]
    controller._remove_picker = remove_picker  # type: ignore[method-assign]
    await controller._refresh_queue(state)

    assert events == ["fetch:111", "send", "persist", "delete:333", "retire:111"]
    sent = channel.send_calls[0]
    assert any(
        isinstance(item, discord.ui.Button) and item.custom_id == "queue_catalog:es:0"
        for item in sent["view"].walk_children()
    )
    assert sent["allowed_mentions"].users is False


@pytest.mark.parametrize("picker_only", [False, True])
async def test_picker_buttons_route_language_and_level(picker_only: bool) -> None:
    state = _reading_state()
    state.phase = SessionPhase.SELECTING
    state.active_reading = None
    controller = _controller(_FakeService([]))
    controller.handle_catalog_selection = AsyncMock()  # type: ignore[method-assign]
    panel = QueuePanel(controller, state, includes_picker=not picker_only, picker_only=picker_only)
    buttons = [
        item for item in panel.walk_children()
        if isinstance(item, discord.ui.Button)
        and (item.custom_id or "").startswith("queue_catalog:")
    ]
    assert [item.custom_id for item in buttons] == [
        "queue_catalog:es:0", "queue_catalog:es:1", "queue_catalog:es:2",
        "queue_catalog:en:0", "queue_catalog:en:1", "queue_catalog:en:2",
    ]
    assert [item.style.value for item in buttons] == [3, 3, 3, 1, 1, 1]
    assert isinstance(panel.children[0], discord.ui.TextDisplay)
    assert panel.children[0].content.startswith("<@10>")
    assert (GUIDE_URL in str(panel.to_components())) is not picker_only
    assert panel.is_persistent()
    interaction = _FakeInteraction(_FakeMessage(222, "panel", []))
    await buttons[4].callback(interaction)  # type: ignore[arg-type]
    controller.handle_catalog_selection.assert_awaited_once_with(
        interaction, language=Language.ENGLISH, level_index=1,
    )

    state.channel_mode = ChannelMode.CUSTOM_ONLY
    custom_panel = QueuePanel(controller, state, picker_only=True)
    assert [
        item.custom_id for item in custom_panel.walk_children() if isinstance(item, discord.ui.Button)
    ] == ["queue_custom_text"]
    modal = CustomTextModal(
        controller, text_channel_id=101, picker_message_id=222, language=None,
        ask_language=False, max_text_length=4000,
    )
    assert modal.language_select is not None
    assert [option.value for option in modal.language_select.options] == ["es", "en"]
    assert modal.body_input.max_length == 4000


async def test_switch_layouts_preserves_turn_and_moves_the_picker(tmp_path: Path) -> None:
    events: list[str] = []
    state = _reading_state()
    state.phase = SessionPhase.SELECTING
    state.active_reading = None
    state.queue_message_id = 111
    state.turn_number = 5
    repository = SQLiteRepository(tmp_path / "settings.sqlite3")
    await repository.initialize()
    await repository.save_session(state)
    service = SessionService(repository)
    await service.initialize()
    controller = _controller(
        service, channel_pairs=(ChannelPairConfig("Lectura 1", 101, 201, ChannelMode.STANDARD),),
    )
    channel = _FakeChannel(
        new_message=_FakeMessage(222, "picker", events),
        old_message=_FakeMessage(111, "queue", events, v2=True),
        events=events,
    )
    controller._text_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]

    assert controller.queue_layout is QueueLayout.DUAL
    await controller._refresh_queue(state, notify_reader=True)
    picker = await service.get_session(101)
    assert picker is not None and picker.picker_message_id == 222
    assert channel.send_calls[0]["allowed_mentions"].to_dict() == {
        "parse": [], "users": [10],
    }
    assert channel.send_calls[0]["view"].to_components()[0]["content"].startswith("<@10>")
    assert all("queue_catalog" not in str(edit["view"].to_components())
               for edit in channel.old_message.edit_calls)

    assert await controller.set_queue_layout(QueueLayout.SINGLE) == []
    merged = await service.get_session(101)
    assert merged is not None and merged.picker_message_id == merged.queue_message_id == 111
    assert "picker.delete" in events
    assert "queue_catalog" in str(channel.old_message.edit_calls[-1]["view"].to_components())
    assert await SQLiteRepository(repository.database_path).get_queue_layout() is QueueLayout.SINGLE

    channel.new_message = _FakeMessage(223, "replacement", events)
    assert await controller.set_queue_layout(QueueLayout.DUAL) == []
    split = await service.get_session(101)
    assert split is not None
    assert (split.queue_message_id, split.picker_message_id) == (111, 223)
    assert split.current_user_id == 10 and split.queue == state.queue
    assert split.turn_number == 5
    assert channel.send_calls[-1]["allowed_mentions"].users is False
    assert "queue_catalog" not in str(channel.old_message.edit_calls[-1]["view"].to_components())
    assert await SQLiteRepository(repository.database_path).get_queue_layout() is QueueLayout.DUAL

    # A modal opened on the single embed still belongs to this turn.
    pair = controller.config.channel_pairs[0]
    member = SimpleNamespace(id=10)
    controller._interaction_context = lambda _: (pair, member)  # type: ignore[method-assign]
    controller._require_matching_voice = lambda *_: None  # type: ignore[method-assign]

    async def publish(transition: Transition) -> None:
        assert transition.state.active_reading is not None
        assert transition.state.active_reading.body == "My passage."
        await service.set_reading_message(text_channel_id=101, reader_id=10, message_id=700)

    controller._publish_reading = publish  # type: ignore[method-assign]
    interaction = _ModalInteraction()
    await controller.submit_custom_text(
        interaction,  # type: ignore[arg-type]
        text_channel_id=101, picker_message_id=111, language=Language.ENGLISH,
        body="My passage.", custom_language_label=None, turn=(state.session_id, 5),
    )
    assert "replacement.delete" in events
    assert await controller.set_queue_layout(QueueLayout.SINGLE) == []
    reading = await service.get_session(101)
    assert reading is not None and reading.active_reading is not None
    assert reading.phase is SessionPhase.READING and reading.active_reading.message_id == 700
    assert reading.picker_message_id is None
    assert len(channel.send_calls) == 2


@pytest.mark.parametrize("member_id,button_turn", [(10, 4), (20, 4), (20, 3)])
async def test_shared_skip_routes_reader_and_voter_and_rejects_old_turn(
    member_id: int, button_turn: int,
) -> None:
    state = _reading_state()
    state.queue_message_id = 111
    state.turn_number = 4
    transition = Transition(state, "Action saved.")
    service = SimpleNamespace(
        get_session=AsyncMock(return_value=state),
        pass_turn=AsyncMock(return_value=transition),
        vote_to_skip=AsyncMock(return_value=transition),
    )
    controller = _controller(service)
    pair = ChannelPairConfig("Lectura 1", 101, 201, ChannelMode.STANDARD)
    member = SimpleNamespace(id=member_id)
    controller._interaction_context = lambda _: (pair, member)  # type: ignore[method-assign]
    controller._require_matching_voice = lambda *_: None  # type: ignore[method-assign]
    controller._refresh_queue = AsyncMock()  # type: ignore[method-assign]
    interaction = _FakeInteraction(_FakeMessage(111, "queue", []))
    interaction.client = SimpleNamespace(controller=controller)
    button = QueueSkipButton(state.session_id, button_turn, state.phase, "Skip Turn")

    await button.callback(interaction)  # type: ignore[arg-type]

    if button_turn != state.turn_number:
        service.pass_turn.assert_not_awaited()
        service.vote_to_skip.assert_not_awaited()
        assert "no longer active" in interaction.followup.send_calls[0][0]
    elif member_id == 10:
        service.pass_turn.assert_awaited_once_with(
            text_channel_id=101, actor_id=10, source_message_id=111,
        )
        service.vote_to_skip.assert_not_awaited()
    else:
        service.vote_to_skip.assert_awaited_once_with(
            text_channel_id=101, voter_id=20, source_message_id=111,
        )
        service.pass_turn.assert_not_awaited()


@pytest.mark.parametrize("fail_sending_second_part", [False, True])
@pytest.mark.asyncio
async def test_custom_passage_posts_parts_before_committing_controls(
    fail_sending_second_part: bool,
) -> None:
    state = _reading_state()
    reading = state.active_reading
    assert reading is not None
    reading.level = None
    reading.body = "x" * 2_400
    first = SimpleNamespace(id=699, delete=AsyncMock())
    last = SimpleNamespace(id=700, delete=AsyncMock())
    second_send = RuntimeError("send failed") if fail_sending_second_part else last
    channel = SimpleNamespace(send=AsyncMock(side_effect=[first, second_send]))
    service = SimpleNamespace(set_reading_message=AsyncMock())
    controller = _controller(service)
    controller._text_channel = AsyncMock(return_value=channel)  # type: ignore[method-assign]
    transition = Transition(state, "Text received.")

    if fail_sending_second_part:
        with pytest.raises(RuntimeError, match="send failed"):
            await controller._publish_reading(transition)
        first.delete.assert_awaited_once()
        service.set_reading_message.assert_not_awaited()
        return

    await controller._publish_reading(transition)
    first_send, final_send = [call.kwargs for call in channel.send.await_args_list]
    assert first_send["content"].startswith("## Reader")
    assert "view" not in first_send and "embed" not in first_send
    assert isinstance(final_send["view"], ReadingView)
    assert final_send["embed"] is not None
    service.set_reading_message.assert_awaited_once_with(
        text_channel_id=101, reader_id=10, message_id=700, preceding_message_ids=(699,),
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
    assert isinstance(channel.send_calls[0]["view"], QueuePanel)


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
    archived = new_message.edit_calls[0]["view"]
    assert archived.has_components_v2()
    assert not any(isinstance(item, discord.ui.ActionRow) for item in archived.walk_children())
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
    assert "embed" not in interaction.edit_calls[0]
    assert isinstance(interaction.edit_calls[0]["view"], QueuePanel)
    assert interaction.edit_calls[0]["allowed_mentions"].everyone is False
    assert service.set_queue_calls == [(101, 222)]
    assert events == ["get_session", "persist", "retire:101:111"]


@pytest.mark.parametrize("layout", [QueueLayout.SINGLE, QueueLayout.DUAL])
@pytest.mark.asyncio
async def test_turn_transition_notifies_the_next_reader(layout: QueueLayout) -> None:
    events: list[str] = []
    service = _FakeService(events)
    controller = _controller(service)
    controller.queue_layout = layout
    state = _state()

    async def retire(_channel_id: int, message_id: int | None) -> None:
        events.append(f"retire:{message_id}")

    async def remove_picker(_state: SessionState, message_id: int | None) -> None:
        events.append(f"delete:{message_id}")

    async def refresh(
        refreshed_state: SessionState,
        *,
        repost: bool = False,
        notify_reader: bool = False,
    ) -> _FakeMessage:
        assert refreshed_state is state
        events.append(f"refresh:{repost}:{notify_reader}")
        return _FakeMessage(222, "new", events)

    controller._retire_message = retire  # type: ignore[method-assign]
    controller._remove_picker = remove_picker  # type: ignore[method-assign]
    controller._refresh_queue = refresh  # type: ignore[method-assign]

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
        "delete:500",
        "retire:600",
        "refresh:True:True",
    ]


@pytest.mark.asyncio
async def test_different_text_action_replaces_and_refreshes_current_reading() -> None:
    events: list[str] = []
    state = _reading_state()
    assert state.active_reading is not None
    state.active_reading.source_text_id = 91
    service = _ReplacementService(state)
    pair = ChannelPairConfig(
        name="sandbox-1",
        text_channel_id=101,
        voice_channel_id=201,
        mode=ChannelMode.STANDARD,
    )
    controller = _controller(service, channel_pairs=(pair,))
    interaction = _FakeInteraction(_FakeMessage(700, "reading", events))
    member = type("Member", (), {"id": 10})()
    controller._interaction_context = lambda _interaction: (  # type: ignore[method-assign]
        pair,
        member,
    )
    controller._require_matching_voice = (  # type: ignore[method-assign]
        lambda _member, _pair: None
    )

    async def refresh(refreshed_state: SessionState) -> bool:
        assert refreshed_state is state
        events.append("refresh")
        return True

    controller._refresh_reading = refresh  # type: ignore[method-assign]

    async def refresh_queue(refreshed_state: SessionState) -> None:
        assert refreshed_state is state
        events.append("queue")

    controller._refresh_queue = refresh_queue  # type: ignore[method-assign]
    await controller.handle_different_text(interaction)  # type: ignore[arg-type]

    assert interaction.response.defer_calls == [{}]
    assert len(service.calls) == 1
    assert service.calls[0]["text_channel_id"] == 101
    assert service.calls[0]["reader_id"] == 10
    assert service.calls[0]["reading_message_id"] == 700
    assert service.calls[0]["validator"] == controller._validate_reading_render
    assert events == ["refresh", "queue"]
    assert interaction.followup.send_calls == [
        ("A different text was selected.", {"ephemeral": True})
    ]


@pytest.mark.asyncio
async def test_full_correction_summary_hands_off_to_inert_continuation() -> None:
    events: list[str] = []
    state = _reading_state()
    assert state.active_reading is not None
    new_message = _FakeMessage(701, "new", events)
    old_message = _FakeMessage(700, "old", events)
    channel = _FakeChannel(
        new_message=new_message,
        old_message=old_message,
        events=events,
    )
    service = _RolloverCorrectionService(state, events)
    controller = _controller(service)

    async def text_channel(_: int) -> _FakeChannel:
        return channel

    controller._text_channel = text_channel  # type: ignore[method-assign]

    transition = await controller._add_corrections_with_rollover(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Listener",
        items=["short"],
        source=CorrectionSource.BUTTON,
    )

    assert transition.correction_rollover is True
    assert events == [
        "send",
        "fetch:700",
        "old.edit",
        "persist",
        "new.edit",
    ]
    assert "view" not in channel.send_calls[0]
    assert len(old_message.edit_calls) == 1
    assert old_message.edit_calls[0]["content"].startswith("## Reader")
    assert old_message.edit_calls[0]["embed"] is not None
    assert old_message.edit_calls[0]["view"] is None
    assert len(new_message.edit_calls) == 1
    assert isinstance(new_message.edit_calls[0]["view"], ReadingView)
    assert service.rollover_calls[0]["old_reading_message_id"] == 700
    assert service.rollover_calls[0]["new_reading_message_id"] == 701
    assert (
        service.rollover_calls[0]["validator"]
        == controller._validate_reading_render
    )


@pytest.mark.asyncio
async def test_failed_correction_rollover_restores_old_controls() -> None:
    events: list[str] = []
    state = _reading_state()
    new_message = _FakeMessage(701, "new", events)
    old_message = _FakeMessage(700, "old", events)
    channel = _FakeChannel(
        new_message=new_message,
        old_message=old_message,
        events=events,
    )
    service = _RolloverCorrectionService(
        state,
        events,
        fail_rollover=True,
    )
    controller = _controller(service)

    async def text_channel(_: int) -> _FakeChannel:
        return channel

    controller._text_channel = text_channel  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="database write failed"):
        await controller._add_corrections_with_rollover(
            text_channel_id=101,
            reading_message_id=700,
            corrector_id=20,
            corrector_display_name="Listener",
            items=["short"],
            source=CorrectionSource.REPLY,
        )

    assert events == [
        "send",
        "fetch:700",
        "old.edit",
        "persist",
        "new.edit",
        "fetch:700",
        "old.edit",
    ]
    assert new_message.edit_calls[0]["view"] is None
    assert isinstance(old_message.edit_calls[-1]["view"], ReadingView)


@pytest.mark.asyncio
async def test_correction_rollover_repairs_failed_continuation_activation() -> None:
    events: list[str] = []
    state = _reading_state()
    new_message = _FakeMessage(701, "new", events, failed_edits=1)
    old_message = _FakeMessage(700, "old", events)
    channel = _FakeChannel(
        new_message=new_message,
        old_message=old_message,
        events=events,
    )
    service = _RolloverCorrectionService(state, events)
    controller = _controller(service)

    async def text_channel(_: int) -> _FakeChannel:
        return channel

    controller._text_channel = text_channel  # type: ignore[method-assign]

    transition = await controller._add_corrections_with_rollover(
        text_channel_id=101,
        reading_message_id=700,
        corrector_id=20,
        corrector_display_name="Listener",
        items=["short"],
        source=CorrectionSource.BUTTON,
    )

    assert transition.correction_rollover is True
    assert events == [
        "send",
        "fetch:700",
        "old.edit",
        "persist",
        "new.edit",
        "fetch:701",
        "new.edit",
    ]
    assert len(new_message.edit_calls) == 2
    assert isinstance(new_message.edit_calls[-1]["view"], ReadingView)


_CorrectionReplyContext = tuple[
    LecturaController, _CorrectionService, _ReplyMessage, list[SessionState]
]


@pytest.fixture
def correction_reply_context(
    monkeypatch: pytest.MonkeyPatch,
) -> _CorrectionReplyContext:
    state = _reading_state()
    service = _CorrectionService(state)
    pair = ChannelPairConfig(
        name="sandbox-1",
        text_channel_id=101,
        voice_channel_id=201,
        mode=ChannelMode.STANDARD,
    )
    controller = _controller(service, channel_pairs=(pair,))
    refreshed: list[SessionState] = []

    async def refresh(refreshed_state: SessionState) -> bool:
        refreshed.append(refreshed_state)
        return True

    controller._refresh_reading = refresh  # type: ignore[method-assign]
    monkeypatch.setattr("lecturabot.controller.discord.Member", _ReplyMember)
    member = _ReplyMember(20, "Listener")
    member.voice = SimpleNamespace(channel=SimpleNamespace(id=201))
    reply = _ReplyMessage(member, "")
    return controller, service, reply, refreshed


@pytest.mark.parametrize(
    ("content", "expected_items"),
    [
        (
            "produce (noun), (stress, keep trying :peepoPray:),",
            ["produce (noun)", "(stress, keep trying :peepoPray:)"],
        ),
        (f"<@{BOT_USER_ID}> apple", ["apple"]),
        ("árbol 🍎 (https://example.com)", ["árbol 🍎 (https://example.com)"]),
        (f"apple <@!{BOT_USER_ID}>", ["apple"]),
        (
            f"<@{BOT_USER_ID}>an<@!{BOT_USER_ID}>apple<@{BOT_USER_ID}>",
            ["an apple"],
        ),
        (f"<@{BOT_USER_ID}> " + "a" * 100, ["a" * 100]),
        (
            f"<@{BOT_USER_ID}> apple (<@42>, <@!43>, <@&44>)",
            ["apple (<@42>, <@!43>, <@&44>)"],
        ),
    ],
    ids=[
        "reply-ping",
        "mention",
        "text-with-emoji-and-link",
        "nickname-mention",
        "adjacent-mentions",
        "length-limit",
        "other-mentions",
    ],
)
@pytest.mark.asyncio
async def test_reply_to_reading_submits_corrections_and_refreshes_message(
    correction_reply_context: _CorrectionReplyContext,
    content: str,
    expected_items: list[str],
) -> None:
    controller, service, reply, refreshed = correction_reply_context
    reply.content = content

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert service.find_calls == [700]
    assert len(service.add_calls) == 1
    assert service.add_calls[0]["text_channel_id"] == 101
    assert service.add_calls[0]["reading_message_id"] == 700
    assert service.add_calls[0]["corrector_id"] == 20
    assert service.add_calls[0]["items"] == expected_items
    assert service.add_calls[0]["source"] is CorrectionSource.REPLY
    assert refreshed == [service.state]
    assert reply.reply_calls == []


@pytest.mark.asyncio
async def test_reply_to_preceding_passage_part_uses_active_summary(
    correction_reply_context: _CorrectionReplyContext,
) -> None:
    controller, service, reply, refreshed = correction_reply_context
    reading = service.state.active_reading
    assert reading is not None
    reading.preceding_message_ids = (699,)
    reply.reference.message_id = 699
    reply.content = "apple"

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert service.find_calls == [699]
    assert service.add_calls[0]["reading_message_id"] == 700
    assert service.add_calls[0]["items"] == ["apple"]
    assert refreshed == [service.state]


@pytest.mark.parametrize(
    "content",
    ["", " \n ", f"<@{BOT_USER_ID}>", f"<@!{BOT_USER_ID}> <@{BOT_USER_ID}>"],
    ids=["redacted", "whitespace", "mention-only", "multiple-mentions-only"],
)
@pytest.mark.asyncio
async def test_unreadable_or_empty_reply_gets_guidance_without_saving(
    correction_reply_context: _CorrectionReplyContext,
    content: str,
) -> None:
    controller, service, reply, refreshed = correction_reply_context
    reply.content = content

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert service.add_calls == []
    assert refreshed == []
    assert len(reply.reply_calls) == 1
    guidance, kwargs = reply.reply_calls[0]
    spanish, english = guidance.splitlines()
    assert spanish.startswith("- No pude leer ninguna corrección")
    assert english.startswith("- I couldn't read any correction text")
    assert "ON" in guidance
    assert kwargs["mention_author"] is False
    assert (
        kwargs["allowed_mentions"].to_dict()
        == discord.AllowedMentions.none().to_dict()
    )


@pytest.mark.parametrize(
    ("content", "media_field"),
    [
        ("", "attachments"),
        (f"<@{BOT_USER_ID}>", "attachments"),
        ("", "stickers"),
        ("👍🏽 1️⃣ ℹ️", None),
        ("<:apple:123> <a:party:456> :apple:", None),
        ("https://example.com/gif", None),
        ("ℹwww.example.com/gif", None),
        (f"<@{BOT_USER_ID}> 🍎 ||<https://example.com/gif>||", None),
    ],
)
@pytest.mark.asyncio
async def test_non_text_reply_gets_neither_guidance_nor_correction(
    correction_reply_context: _CorrectionReplyContext,
    content: str,
    media_field: str | None,
) -> None:
    controller, service, reply, refreshed = correction_reply_context
    reply.content = content
    if media_field is not None:
        getattr(reply, media_field).append(object())

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert reply.reply_calls == []
    assert service.add_calls == []
    assert refreshed == []


@pytest.mark.parametrize("content", ["", "apple"], ids=["redacted", "visible"])
@pytest.mark.parametrize(
    "ineligible",
    [
        "not-a-reply",
        "stale-reply",
        "bot",
        "reader",
        "wrong-channel",
        "wrong-guild",
        "wrong-voice",
    ],
)
@pytest.mark.asyncio
async def test_ineligible_reply_gets_neither_guidance_nor_correction(
    correction_reply_context: _CorrectionReplyContext,
    ineligible: str,
    content: str,
) -> None:
    controller, service, reply, refreshed = correction_reply_context
    reply.content = content
    if ineligible == "not-a-reply":
        reply.reference = None
    elif ineligible == "stale-reply":
        reply.reference.message_id = 699
    elif ineligible == "bot":
        reply.author.bot = True
    elif ineligible == "reader":
        reply.author.id = 10
    elif ineligible == "wrong-channel":
        reply.channel.id = 102
    elif ineligible == "wrong-guild":
        reply.guild.id = 2
    elif ineligible == "wrong-voice":
        reply.author.voice.channel.id = 202

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert service.add_calls == []
    assert refreshed == []
    assert reply.reply_calls == []


@pytest.mark.asyncio
async def test_reply_without_authenticated_bot_identity_keeps_message_text(
    correction_reply_context: _CorrectionReplyContext,
) -> None:
    controller, service, reply, refreshed = correction_reply_context
    controller.bot.user = None
    reply.content = f"apple (<@{BOT_USER_ID}>)"

    await controller.handle_reply(reply)  # type: ignore[arg-type]

    assert service.add_calls[0]["items"] == [reply.content]
    assert refreshed == [service.state]


@pytest.mark.asyncio
async def test_modal_saves_unmatched_corrections_and_reports_unhighlighted_count() -> None:
    state = _reading_state()
    user_message = (
        "Correcciones guardadas. Sin resaltar: 2. / Corrections saved. "
        "Listed without highlighting: 2."
    )
    service = _CorrectionService(
        state,
        notice=user_message,
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
    refreshed: list[SessionState] = []

    async def refresh(refreshed_state: SessionState) -> bool:
        refreshed.append(refreshed_state)
        return True

    controller._refresh_reading = refresh  # type: ignore[method-assign]

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
    assert refreshed == [state]
    assert interaction.response.send_message_calls == []
    assert len(interaction.followup.send_calls) == 1
    sent_message, sent_kwargs = interaction.followup.send_calls[0]
    assert sent_message == user_message
    assert sent_kwargs["ephemeral"] is True
