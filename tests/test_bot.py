from __future__ import annotations

import discord
from discord.ext import commands
import pytest

from lecturabot.bot import LecturaCog


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
