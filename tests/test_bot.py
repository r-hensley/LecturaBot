from __future__ import annotations

import asyncio
from pathlib import Path
from stat import S_IMODE
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import lecturabot
from lecturabot import hotfix
from lecturabot.bot import FEATURES_EXTENSION, LecturaBot, MaintenanceCog
from lecturabot.bot_control import BotControl, send_request
from lecturabot.features import LecturaCog, SettingsView
from lecturabot.config import BotConfig
from lecturabot.models import QueueLayout, SessionState
from lecturabot.repository import SQLiteRepository


@pytest.mark.asyncio
async def test_privileged_intents_disabled_with_voice_and_message_events_retained(
    tmp_path: Path,
) -> None:
    bot = LecturaBot(
        BotConfig(
            token="test-token",
            guild_id=1,
            bot_status_contact_user_id=900,
            issue_contact_user_id=901,
            database_path=tmp_path / "test.sqlite3",
            channel_pairs=(),
        )
    )
    try:
        assert bot.intents.message_content is False
        assert bot.intents.guilds is True
        assert bot.intents.messages is True
        assert bot.intents.members is False
        assert bot.intents.voice_states is True
        assert bot.intents.presences is False
        assert bot._connection.member_cache_flags.value == 0
        assert bot._connection._chunk_guilds is False
    finally:
        await bot.close()


def test_bilingual_queue_commands_are_registered() -> None:
    cog = LecturaCog(object())  # type: ignore[arg-type]

    commands = cog.get_app_commands()

    assert [command.name for command in commands] == ["settings", "queue", "cola"]
    assert commands[1].description == "Open the LecturaBot reading queue."
    assert commands[2].description == "Abre la cola de lectura de LecturaBot."


@pytest.mark.asyncio
@pytest.mark.parametrize("dev_guild_id", [None, 123])
async def test_owner_can_reload_and_sync_in_configured_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dev_guild_id: int | None
) -> None:
    bot = LecturaBot(
        BotConfig(
            token="test-token",
            guild_id=1,
            bot_status_contact_user_id=900,
            issue_contact_user_id=901,
            database_path=tmp_path / "test.sqlite3",
            channel_pairs=(),
            dev_guild_id=dev_guild_id,
        )
    )
    async with bot:
        await bot.load_extension(FEATURES_EXTENSION)
        original = bot.get_cog("LecturaCog")
        controller = bot.controller
        old_settings = bot.tree.get_command("settings", guild=bot.command_guild)
        maintenance = MaintenanceCog(bot)
        await bot.add_cog(maintenance, guild=bot.command_guild)
        interaction = SimpleNamespace(
            user=object(),
            response=SimpleNamespace(defer=AsyncMock()),
            edit_original_response=AsyncMock(),
        )
        is_owner = AsyncMock(return_value=False)
        monkeypatch.setattr(bot, "is_owner", is_owner)
        reload = bot.tree.get_command("reload", guild=bot.command_guild)
        sync = bot.tree.get_command("sync", guild=bot.command_guild)
        publish = AsyncMock(return_value=[object()] * 5)
        monkeypatch.setattr(bot.tree, "sync", publish)
        for command in (reload, sync):
            await command.callback(maintenance, interaction)
            interaction.edit_original_response.assert_awaited_with(
                content="Only the bot owner can do this."
            )
        assert bot.get_cog("LecturaCog") is original
        publish.assert_not_awaited()

        is_owner.return_value = True
        await reload.callback(maintenance, interaction)
        assert bot.get_cog("LecturaCog") is not original
        assert bot.get_cog("LecturaCog").controller is controller
        assert bot.tree.get_command("settings", guild=bot.command_guild) is not old_settings
        assert {cmd.name for cmd in bot.tree.get_commands(guild=bot.command_guild)} == {
            "reload", "sync", "settings", "queue", "cola"
        }
        if dev_guild_id is not None:
            assert bot.tree.get_commands() == []

        await sync.callback(maintenance, interaction)
        publish.assert_awaited_once_with(guild=bot.command_guild)


@pytest.mark.parametrize("legacy_controller", [False, True])
async def test_private_socket_reloads_features_and_keeps_session_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy_controller: bool,
) -> None:
    path = tmp_path / "control" / "bot.sock"
    monkeypatch.setenv("LECTURABOT_CONTROL_SOCKET", str(path))
    bot = LecturaBot(
        BotConfig(
            token="test-token",
            guild_id=1,
            bot_status_contact_user_id=900,
            issue_contact_user_id=901,
            database_path=tmp_path / "test.sqlite3",
            channel_pairs=(),
        )
    )
    original_queue_action = AsyncMock()
    if legacy_controller:
        del bot.controller.queue_help
        monkeypatch.setattr(bot.controller, "handle_queue_action", original_queue_action)
    sync = AsyncMock(return_value=0)
    monkeypatch.setattr(bot, "sync_app_commands", sync)
    async with bot:
        await bot.setup_hook()
        original = bot.get_cog("LecturaCog")
        assert original is not None
        controller = bot.controller
        assert S_IMODE(path.parent.stat().st_mode) == 0o700
        assert S_IMODE(path.stat().st_mode) == 0o600

        duplicate = BotControl(path, features_loaded=lambda: False, reload_features=bot.reload_features)
        with pytest.raises(OSError):
            await duplicate.start()
        await duplicate.close()
        assert await send_request(path, "status", []) == {"ok": True, "loaded": ["features"]}

        for action, targets in (("reload", ["lecturabot.controller"]), ("unload", ["features"])):
            assert not (await send_request(path, action, targets))["ok"]
        assert bot.get_cog("LecturaCog") is original

        assert await send_request(path, "reload", ["features"]) == {"ok": True, "completed": ["features"]}
        assert bot.get_cog("LecturaCog") is not original
        assert bot.get_cog("LecturaCog").controller is controller
        send_message = AsyncMock()
        interaction = SimpleNamespace(response=SimpleNamespace(send_message=send_message))
        await controller.handle_queue_action(interaction, "instructions")
        reply = send_message.await_args.kwargs
        assert reply["ephemeral"] is True
        support, = reply["embed"].fields
        assert support.value.count("https://discord.com/channels/243838819743432704/1525628752074113127") == 2
        assert "<@" not in support.value
        if legacy_controller:
            await controller.handle_queue_action(interaction, "enter_queue")
            original_queue_action.assert_awaited_once_with(interaction, "enter_queue")
        sync.assert_awaited_once()
    assert not path.exists()
    if legacy_controller:
        assert bot.controller.handle_queue_action is original_queue_action


async def test_socket_reloads_changed_reading_modules_and_recovers_from_bad_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Restore the imports when the test ends so other tests can keep using
    # the classes they imported during collection.
    for name in hotfix.RUNTIME_MODULES:
        qualified = f"lecturabot.{name}"
        monkeypatch.setitem(sys.modules, qualified, sys.modules[qualified])
        monkeypatch.setattr(lecturabot, name, getattr(lecturabot, name))
    replacement_dir = tmp_path / "sources"
    replacement_dir.mkdir()
    for name in hotfix.RUNTIME_MODULES:
        source = Path(hotfix.__file__).with_name(f"{name}.py")
        (replacement_dir / source.name).write_bytes(source.read_bytes())
    monkeypatch.setattr(hotfix, "Path", lambda _: replacement_dir / "hotfix.py")
    path = tmp_path / "control" / "bot.sock"
    monkeypatch.setenv("LECTURABOT_CONTROL_SOCKET", str(path))
    bot = LecturaBot(BotConfig(
        token="test-token", guild_id=1, bot_status_contact_user_id=900,
        issue_contact_user_id=901, database_path=tmp_path / "test.sqlite3", channel_pairs=(),
    ))
    monkeypatch.setattr(bot, "sync_app_commands", AsyncMock(return_value=0))
    async with bot:
        await bot.setup_hook()
        controller, service = bot.controller, bot.service
        old_class, old_cog = type(controller), bot.get_cog("LecturaCog")
        session = SessionState(1, 1, 101, 201)
        service._states[101] = session
        timer = controller._reconnects[(201, 10)] = asyncio.Event()
        renderer = replacement_dir / "rendering.py"
        good_source = renderer.read_bytes()
        renderer.write_bytes(good_source + b"\ninvalid syntax !!!\n")

        failed = await send_request(path, "reload", ["features"])
        assert not failed["ok"]
        assert type(controller) is old_class and service._states[101] is session
        assert type(bot.get_cog("LecturaCog")) is type(old_cog)

        renderer.write_bytes(good_source + b"\nRELOADED_IN_TEST = True\n")
        result = await send_request(path, "reload", ["features"])
        assert result == {"ok": True, "completed": ["features"]}
        assert sys.modules["lecturabot.rendering"].RELOADED_IN_TEST is True
        assert bot.controller is controller and type(controller) is not old_class
        assert bot.service is service and service._states[101].to_dict() == session.to_dict()
        assert controller._reconnects[(201, 10)] is timer


@pytest.mark.asyncio
async def test_grace_period_setting_persists(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "settings.sqlite3")
    await repository.initialize()
    assert await repository.get_grace_minutes() == 5
    assert await repository.get_queue_layout() is QueueLayout.DUAL
    await repository.set_grace_minutes(12)
    assert await SQLiteRepository(repository.database_path).get_grace_minutes() == 12


@pytest.mark.asyncio
async def test_settings_panel_rejects_another_user_before_writing(tmp_path: Path) -> None:
    repository = SQLiteRepository(tmp_path / "settings.sqlite3")
    await repository.initialize()
    controller = SimpleNamespace(
        config=SimpleNamespace(guild_id=1),
        service=SimpleNamespace(repository=repository),
        voice_grace_seconds=300,
        queue_layout=QueueLayout.DUAL,
    )
    view = SettingsView(controller, user_id=10)  # type: ignore[arg-type]
    interaction = SimpleNamespace(
        guild_id=1,
        user=SimpleNamespace(id=20),
        response=SimpleNamespace(send_message=AsyncMock()),
    )

    assert not await view.authorized(interaction)  # type: ignore[arg-type]
    assert await repository.get_grace_minutes() == 5
    interaction.response.send_message.assert_awaited_once()

    interaction.response.edit_message = AsyncMock()
    await view.save(interaction, 7)  # type: ignore[arg-type]
    assert await repository.get_grace_minutes() == 7
    assert controller.voice_grace_seconds == 420
    interaction.response.edit_message.assert_awaited_once()


async def test_settings_select_switches_layout_and_updates_the_menu() -> None:
    controller = SimpleNamespace(queue_layout=QueueLayout.DUAL, voice_grace_seconds=300)

    async def set_layout(layout: QueueLayout) -> list[str]:
        controller.queue_layout = layout
        return []

    controller.set_queue_layout = AsyncMock(side_effect=set_layout)
    view = SettingsView(controller, user_id=10)  # type: ignore[arg-type]
    view.authorized = AsyncMock(return_value=True)  # type: ignore[method-assign]
    interaction = SimpleNamespace(
        response=SimpleNamespace(defer=AsyncMock()), edit_original_response=AsyncMock(),
    )
    assert [option.value for option in view.layout.options if option.default] == ["dual"]
    view.layout._values = ["single"]

    await view.layout.callback(interaction)

    controller.set_queue_layout.assert_awaited_once_with(QueueLayout.SINGLE)
    assert [option.value for option in view.layout.options if option.default] == ["single"]
    assert interaction.edit_original_response.await_args.kwargs["embed"].fields[-1].value == "Single embed"
