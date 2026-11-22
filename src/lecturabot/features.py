"""Reloadable slash commands, settings panel, and gateway listeners."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from .bot import LecturaBot
    from .controller import LecturaController


def settings_embed(minutes: int) -> discord.Embed:
    embed = discord.Embed(title="LecturaBot settings")
    embed.add_field(
        name="Voice reconnect grace period",
        value=f"{minutes} minute{'s' if minutes != 1 else ''}",
        inline=False,
    )
    embed.set_footer(text="Changes apply to new disconnects.")
    return embed


class GracePeriodModal(discord.ui.Modal, title="Custom grace period"):
    minutes = discord.ui.TextInput(
        label="Minutes (0–60)", placeholder="For example: 7", max_length=2
    )

    def __init__(self, view: SettingsView) -> None:
        super().__init__()
        self.settings_view = view

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.settings_view.authorized(interaction):
            return
        value = str(self.minutes.value).strip()
        if not value.isascii() or not value.isdecimal() or not 0 <= int(value) <= 60:
            await interaction.response.send_message(
                "Enter a whole number of minutes from 0 to 60.", ephemeral=True
            )
            return
        await self.settings_view.save(interaction, int(value))


class SettingsView(discord.ui.View):
    def __init__(self, controller: LecturaController, user_id: int) -> None:
        super().__init__(timeout=300)
        self.controller = controller
        self.user_id = user_id

    async def authorized(self, interaction: discord.Interaction) -> bool:
        if (
            interaction.guild_id == self.controller.config.guild_id
            and interaction.user.id == self.user_id
            and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        ):
            return True
        await interaction.response.send_message(
            "Only the administrator who opened this panel can change its settings.",
            ephemeral=True,
        )
        return False

    async def save(self, interaction: discord.Interaction, minutes: int) -> None:
        await self.controller.service.repository.set_grace_minutes(minutes)
        self.controller.voice_grace_seconds = minutes * 60
        await interaction.response.edit_message(
            embed=settings_embed(minutes), view=self
        )

    @discord.ui.select(
        placeholder="Set voice reconnect grace period",
        options=[
            discord.SelectOption(label="No grace period", value="0"),
            discord.SelectOption(label="1 minute", value="1"),
            discord.SelectOption(label="5 minutes", value="5"),
            discord.SelectOption(label="10 minutes", value="10"),
            discord.SelectOption(label="15 minutes", value="15"),
            discord.SelectOption(label="Other…", value="other", description="Enter a custom duration"),
        ],
    )
    async def grace_period(
        self, interaction: discord.Interaction, select: discord.ui.Select[SettingsView]
    ) -> None:
        if not await self.authorized(interaction):
            return
        if select.values[0] == "other":
            await interaction.response.send_modal(GracePeriodModal(self))
            return
        await self.save(interaction, int(select.values[0]))


class LecturaCog(commands.Cog):
    """Expose the queue commands and gateway-driven correction events."""

    def __init__(self, controller: LecturaController) -> None:
        self.controller = controller

    @app_commands.command(name="settings", description="Manage LecturaBot settings.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.checks.has_permissions(administrator=True)
    async def settings(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id != self.controller.config.guild_id:
            await interaction.response.send_message("This server is not configured for LecturaBot.", ephemeral=True)
            return
        minutes = self.controller.voice_grace_seconds // 60
        await interaction.response.send_message(
            embed=settings_embed(minutes),
            view=SettingsView(self.controller, interaction.user.id),
            ephemeral=True,
        )

    @app_commands.command(
        name="queue",
        description="Open the LecturaBot reading queue.",
    )
    @app_commands.guild_only()
    async def queue(self, interaction: discord.Interaction) -> None:
        await self.controller.show_queue(interaction)

    @app_commands.command(
        name="cola",
        description="Abre la cola de lectura de LecturaBot.",
    )
    @app_commands.guild_only()
    async def cola(self, interaction: discord.Interaction) -> None:
        await self.controller.show_queue(interaction)

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        await self.controller.handle_reply(message)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self,
        member: discord.Member,
        before: discord.VoiceState,
        after: discord.VoiceState,
    ) -> None:
        before_id = None if before.channel is None else before.channel.id
        after_id = None if after.channel is None else after.channel.id
        if before_id != after_id:
            await self.controller.handle_voice_change(member, before_id, after_id)


async def setup(bot: LecturaBot) -> None:
    await bot.add_cog(LecturaCog(bot.controller), guild=bot.command_guild)
