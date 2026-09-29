from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import discord
import pytest

from lecturabot.bot import LecturaBot, LecturaCog
from lecturabot.config import BotConfig, ChannelPairConfig
from lecturabot.models import ChannelMode
from lecturabot.service import SessionError


@pytest.fixture
async def bot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[LecturaBot]:
    config = BotConfig(
        token="test-token",
        guild_id=1,
        bot_status_contact_user_id=900,
        issue_contact_user_id=901,
        database_path=tmp_path / "voice.sqlite3",
        channel_pairs=tuple(
            ChannelPairConfig(
                name=f"room-{index}",
                text_channel_id=100 + index,
                voice_channel_id=200 + index,
                mode=ChannelMode.STANDARD,
            )
            for index in (1, 2)
        ),
    )
    client = LecturaBot(config)
    await client.repository.initialize()
    await client.service.initialize()
    for pair in config.channel_pairs:
        await client.service.get_or_create_session(guild_id=1, pair=pair)
    # Keep actual queue persistence and Discord models; suppress outgoing UI.
    monkeypatch.setattr(client.controller, "_apply_transition", AsyncMock())
    try:
        yield client
    finally:
        await client.close()


def _user_data(user_id: int) -> dict[str, Any]:
    return {
        "id": str(user_id),
        "username": f"user-{user_id}",
        "discriminator": "0",
        "avatar": None,
    }


def _member_data(user_id: int) -> dict[str, Any]:
    return {"user": _user_data(user_id), "roles": [], "flags": 0}


def _voice_data(user_id: int, channel_id: int | None) -> dict[str, Any]:
    return {
        "guild_id": "1",
        "user_id": str(user_id),
        "channel_id": None if channel_id is None else str(channel_id),
        "session_id": f"voice-{user_id}",
    }


def _guild(
    bot: LecturaBot,
    connected: dict[int, int],
    *,
    channel_type: int | None = 2,
) -> discord.Guild:
    channels = [
        {
            "id": str(channel_id),
            "name": f"room-{channel_id}",
            "type": kind,
            "position": 0,
            "bitrate": 64000,
            "user_limit": 0,
        }
        for channel_id, kind in ((201, channel_type), (202, 2))
        if kind is not None
    ]
    guild = discord.Guild(
        state=bot._connection,
        data={
            "id": "1",
            "name": "Voice test guild",
            "channels": channels,
            "members": [_member_data(user_id) for user_id in connected],
            "voice_states": [
                _voice_data(user_id, channel_id)
                for user_id, channel_id in connected.items()
            ],
        },
    )
    bot._connection._add_guild(guild)
    assert list(guild.members) == []
    return guild


async def _queue(
    bot: LecturaBot,
    *user_ids: int,
    channel_id: int = 101,
    start: bool = False,
) -> None:
    for user_id in user_ids:
        await bot.service.join(
            text_channel_id=channel_id,
            user_id=user_id,
            display_name=f"Reader {user_id}",
        )
    if start:
        await bot.service.start(text_channel_id=channel_id, actor_id=user_ids[0])


@pytest.mark.parametrize("channel_type", [2, 13], ids=["voice", "stage"])
async def test_startup_preserves_connected_users_without_cached_members(
    bot: LecturaBot,
    channel_type: int,
) -> None:
    guild = _guild(bot, {10: 201, 20: 201, 30: 202}, channel_type=channel_type)
    await _queue(bot, 10, 20, 30, 40, start=True)

    assert await bot.controller.reconcile_voice_membership() is True

    state = await bot.service.get_session(101)
    assert state is not None
    assert state.queue == [10, 20]
    assert state.current_user_id == 10
    assert list(guild.members) == []


async def test_startup_removes_absent_waiting_users_before_absent_reader(
    bot: LecturaBot,
) -> None:
    _guild(bot, {30: 201})
    await _queue(bot, 10, 20, 30, start=True)

    assert await bot.controller.reconcile_voice_membership() is True

    updates = bot.controller._apply_transition.await_args_list
    assert [call.args[0].state.queue for call in updates] == [[10, 30], [30]]
    assert [call.args[0].activated_reader_id for call in updates] == [None, 30]


async def test_startup_keeps_users_who_rejoin_during_earlier_removal(
    bot: LecturaBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _guild(bot, {})
    await _queue(bot, 10, 20, 30, start=True)

    async def update_ui(_transition: Any) -> None:
        for user_id in (10, 30):
            guild._update_voice_state(_voice_data(user_id, 201), 201)

    monkeypatch.setattr(
        bot.controller, "_apply_transition", AsyncMock(side_effect=update_ui)
    )

    assert await bot.controller.reconcile_voice_membership() is True

    state = await bot.service.get_session(101)
    assert state is not None
    assert state.queue == [10, 30]
    assert state.current_user_id == 10
    bot.controller._apply_transition.assert_awaited_once()


@pytest.mark.parametrize(
    "unavailable", [False, True], ids=["missing", "unavailable"]
)
async def test_startup_defers_without_removing_anyone_when_guild_unavailable(
    bot: LecturaBot,
    unavailable: bool,
) -> None:
    if unavailable:
        _guild(bot, {}).unavailable = True
    await _queue(bot, 10, 20, start=True)

    assert await bot.controller.reconcile_voice_membership() is False

    state = await bot.service.get_session(101)
    assert state is not None
    assert state.queue == [10, 20]
    bot.controller._apply_transition.assert_not_awaited()


@pytest.mark.parametrize("replace_guild", [False, True], ids=["unavailable", "replaced"])
async def test_startup_stops_removing_users_if_guild_changes_during_reconciliation(
    bot: LecturaBot,
    monkeypatch: pytest.MonkeyPatch,
    replace_guild: bool,
) -> None:
    guild = _guild(bot, {})
    await _queue(bot, 10, 20, 30, start=True)

    async def update_ui(_transition: Any) -> None:
        if replace_guild:
            _guild(bot, {})
        else:
            guild.unavailable = True

    monkeypatch.setattr(
        bot.controller, "_apply_transition", AsyncMock(side_effect=update_ui)
    )

    assert await bot.controller.reconcile_voice_membership() is False

    state = await bot.service.get_session(101)
    assert state is not None
    assert state.queue == [10, 30]
    assert state.current_user_id == 10
    bot.controller._apply_transition.assert_awaited_once()


@pytest.mark.parametrize("channel_type", [None, 0], ids=["missing", "text-channel"])
async def test_invalid_voice_channel_preserves_its_queue_but_other_rooms_reconcile(
    bot: LecturaBot,
    channel_type: int | None,
) -> None:
    _guild(bot, {}, channel_type=channel_type)
    await _queue(bot, 10, 20)
    await _queue(bot, 30, channel_id=102)

    assert await bot.controller.reconcile_voice_membership() is False

    state = await bot.service.get_session(101)
    other_state = await bot.service.get_session(102)
    assert state is not None and other_state is not None
    assert state.queue == [10, 20]
    assert other_state.queue == []


@pytest.mark.parametrize(
    "next_channel", [None, 202, 201], ids=["leave", "move", "mute"]
)
async def test_uncached_voice_event_member_supports_voice_checks_and_departure(
    bot: LecturaBot,
    monkeypatch: pytest.MonkeyPatch,
    next_channel: int | None,
) -> None:
    guild = _guild(bot, {20: 201})
    await _queue(bot, 20)
    connection = bot._connection
    connection.user = discord.ClientUser(state=connection, data=_user_data(999))
    member = discord.Member(state=connection, guild=guild, data=_member_data(20))
    pair = bot.config.channel_pairs[0]
    bot.controller._require_matching_voice(member, pair)
    assert guild.get_member(20) is None
    dispatch = Mock()
    monkeypatch.setattr(connection, "dispatch", dispatch)
    event = _voice_data(20, next_channel)
    event["member"] = _member_data(20)

    connection.parse_voice_state_update(event)

    event_name, member, before, after = dispatch.call_args.args
    assert event_name == "voice_state_update"
    assert isinstance(member, discord.Member)
    assert guild.get_member(member.id) is None
    assert before.channel.id == 201
    assert (None if after.channel is None else after.channel.id) == next_channel
    await LecturaCog(bot.controller).on_voice_state_update(member, before, after)
    state = await bot.service.get_session(101)
    assert state is not None
    if next_channel == 201:
        bot.controller._require_matching_voice(member, pair)
        assert state.queue == [20]
    else:
        with pytest.raises(SessionError, match="matching voice"):
            bot.controller._require_matching_voice(member, pair)
        assert state.queue == []


async def test_ready_retries_after_target_guild_becomes_available(
    bot: LecturaBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    guild = _guild(bot, {})
    bot._connection.user = discord.ClientUser(
        state=bot._connection, data=_user_data(999)
    )
    voice = AsyncMock(side_effect=[False, True])
    controls = AsyncMock(return_value=True)
    monkeypatch.setattr(bot.controller, "reconcile_voice_membership", voice)
    monkeypatch.setattr(bot.controller, "reconcile_session_messages", controls)
    monkeypatch.setattr(bot, "is_ready", lambda: True)

    await bot.on_ready()
    assert bot._reconciled is False
    controls.assert_not_awaited()
    await bot.on_guild_available(guild)

    assert bot._reconciled is True
    assert voice.await_count == 2
    controls.assert_awaited_once()


async def test_guild_availability_ignores_other_guilds_and_pre_ready_events(
    bot: LecturaBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reconcile = AsyncMock()
    monkeypatch.setattr(bot, "_reconcile_startup", reconcile)
    monkeypatch.setattr(bot, "is_ready", lambda: False)
    await bot.on_guild_available(discord.Object(id=1))
    monkeypatch.setattr(bot, "is_ready", lambda: True)
    await bot.on_guild_available(discord.Object(id=2))
    reconcile.assert_not_awaited()
    bot._reconciled = True
    await bot.on_guild_unavailable(discord.Object(id=2))
    assert bot._reconciled is True
    await bot.on_guild_unavailable(discord.Object(id=1))
    assert bot._reconciled is False


async def test_concurrent_startup_events_reconcile_once(
    bot: LecturaBot,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = asyncio.Event()
    release = asyncio.Event()

    async def reconcile_voice() -> bool:
        entered.set()
        await release.wait()
        return True

    voice = AsyncMock(side_effect=reconcile_voice)
    controls = AsyncMock(return_value=True)
    monkeypatch.setattr(bot.controller, "reconcile_voice_membership", voice)
    monkeypatch.setattr(bot.controller, "reconcile_session_messages", controls)
    first = asyncio.create_task(bot._reconcile_startup())
    await entered.wait()
    second = asyncio.create_task(bot._reconcile_startup())
    release.set()
    await asyncio.gather(first, second)

    voice.assert_awaited_once()
    controls.assert_awaited_once()
    assert bot._reconciled is True
