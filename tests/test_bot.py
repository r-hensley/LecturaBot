from __future__ import annotations

from pathlib import Path

import discord
from discord.ext import commands
import pytest

from lecturabot.bot import LecturaBot, LecturaCog
from lecturabot.config import BotConfig


@pytest.mark.asyncio
async def test_message_content_disabled_with_other_required_intents_retained(
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
        assert bot.intents.members is True
        assert bot.intents.voice_states is True
        assert bot.intents.presences is False
    finally:
        await bot.close()


def test_bilingual_queue_commands_are_registered() -> None:
    cog = LecturaCog(object())  # type: ignore[arg-type]

    commands = cog.get_app_commands()

    assert [command.name for command in commands] == ["queue", "cola"]
    assert commands[0].description == "Open the LecturaBot reading queue."
    assert commands[1].description == "Abre la cola de lectura de LecturaBot."


@pytest.mark.asyncio
async def test_development_scope_can_clear_old_global_aliases() -> None:
    bot = commands.Bot(command_prefix="!", intents=discord.Intents.none())
    await bot.add_cog(LecturaCog(object()))  # type: ignore[arg-type]
    guild = discord.Object(id=123)

    bot.tree.copy_global_to(guild=guild)
    bot.tree.clear_commands(guild=None)

    assert bot.tree.get_commands() == []
    assert [command.name for command in bot.tree.get_commands(guild=guild)] == [
        "queue",
        "cola",
    ]
    await bot.close()
