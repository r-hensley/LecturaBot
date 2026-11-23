"""Discord bot, startup wiring, and owner maintenance commands."""

from __future__ import annotations

import asyncio
import logging
import os
from importlib import resources
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands

from .bot_control import BotControl
from .config import BotConfig
from .controller import LecturaController
from .repository import SQLiteRepository
from .service import SessionService


LOGGER = logging.getLogger(__name__)
CATALOG_RESOURCE = "data/catalog.json"
FEATURES_EXTENSION = "lecturabot.features"


class MaintenanceCog(commands.Cog):
    """Keep owner commands available even when a feature reload fails."""

    def __init__(self, bot: LecturaBot) -> None:
        self.bot = bot

    async def _begin(self, interaction: discord.Interaction) -> bool:
        await interaction.response.defer(ephemeral=True, thinking=True)
        if await self.bot.is_owner(interaction.user):
            return True
        await interaction.edit_original_response(content="Only the bot owner can do this.")
        return False

    @app_commands.command(name="reload", description="Reload LecturaBot's reading features.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def reload(self, interaction: discord.Interaction) -> None:
        if not await self._begin(interaction):
            return
        try:
            await self.bot.reload_features()
        except commands.ExtensionError:
            LOGGER.exception("feature reload failed")
            message = "Reload failed. Check the bot log for details."
        else:
            message = "Reading features reloaded. Use `/sync` if slash command definitions changed."
        await interaction.edit_original_response(content=message)

    @app_commands.command(name="sync", description="Publish LecturaBot's slash command definitions.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def sync(self, interaction: discord.Interaction) -> None:
        if not await self._begin(interaction):
            return
        async with self.bot._maintenance_lock:
            try:
                count = await self.bot.sync_app_commands()
            except discord.HTTPException:
                LOGGER.exception("application command sync failed")
                message = "Slash command sync failed. Check the bot log for details."
            else:
                message = f"Synced {count} slash commands."
        await interaction.edit_original_response(content=message)


class LecturaBot(commands.Bot):
    """Configure intents, dependencies, persistent views, and command sync."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = False
        intents.voice_states = True
        # Correction replies use Discord's bot-mention exception; forms use
        # interactions. Keep message events without requesting broad content.
        intents.message_content = False
        intents.messages = True

        super().__init__(
            command_prefix=(),
            intents=intents,
            member_cache_flags=discord.MemberCacheFlags.none(),
            chunk_guilds_at_startup=False,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.repository = SQLiteRepository(config.database_path)
        self.service = SessionService(self.repository)
        self.controller = LecturaController(self, config, self.service)
        self.command_guild = (
            discord.Object(id=config.dev_guild_id)
            if config.dev_guild_id is not None
            else None
        )
        self._reconciled = False
        self._reconcile_lock = asyncio.Lock()
        self._maintenance_lock = asyncio.Lock()
        self._restart_notice_rooms: set[int] = set()
        self._control: BotControl | None = None

    async def setup_hook(self) -> None:
        await self.repository.initialize()
        self.controller.voice_grace_seconds = (
            await self.repository.get_grace_minutes()
        ) * 60
        self.controller.queue_layout = await self.repository.get_queue_layout()
        catalog_resource = resources.files("lecturabot").joinpath(
            CATALOG_RESOURCE
        )
        # ``as_file`` also supports packaged resources that are not
        # represented by a normal filesystem path.
        with resources.as_file(catalog_resource) as catalog_path:
            sync = await self.repository.sync_texts(catalog_path)
        LOGGER.info(
            "reading catalog ready; inserted %s, re-enabled %s, "
            "updated %s, disabled %s",
            sync.inserted,
            sync.reenabled,
            sync.updated,
            sync.disabled,
        )
        self._restart_notice_rooms = await self.service.initialize()

        await self.add_cog(MaintenanceCog(self), guild=self.command_guild)
        await self.load_extension(FEATURES_EXTENSION)

        if self.command_guild is not None:
            # Remove global aliases from older deployments in development mode.
            removed_global = await self.tree.sync()
            LOGGER.info(
                "synchronized global command cleanup; %s commands remain",
                len(removed_global),
            )
        await self.sync_app_commands()
        if socket_path := os.environ.get("LECTURABOT_CONTROL_SOCKET"):
            control = BotControl(
                Path(socket_path),
                features_loaded=lambda: FEATURES_EXTENSION in self.extensions,
                reload_features=self.reload_features,
            )
            try:
                await control.start()
            except (OSError, ValueError, NotImplementedError):
                LOGGER.exception("Could not start the local bot control socket")
            else:
                self._control = control
                LOGGER.info("Local bot control ready at %s", control.path)

    async def reload_features(self) -> None:
        async with self._maintenance_lock:
            await self.reload_extension(FEATURES_EXTENSION)
        LOGGER.info("Reloaded %s", FEATURES_EXTENSION)

    async def close(self) -> None:
        try:
            if self._control is not None:
                await self._control.close()
        finally:
            await super().close()

    async def sync_app_commands(self) -> int:
        synced = await self.tree.sync(guild=self.command_guild)
        scope = (
            "globally" if self.command_guild is None else f"to guild {self.command_guild.id}"
        )
        LOGGER.info("synced %s application commands %s", len(synced), scope)
        return len(synced)

    async def on_ready(self) -> None:
        if self.user is None:
            return
        LOGGER.info("connected as %s (%s)", self.user, self.user.id)
        await self._reconcile_startup()

    async def on_guild_available(self, guild: discord.Guild) -> None:
        if guild.id == self.config.guild_id and self.is_ready():
            await self._reconcile_startup()

    async def on_guild_unavailable(self, guild: discord.Guild) -> None:
        if guild.id == self.config.guild_id:
            async with self._reconcile_lock:
                self._reconciled = False

    async def _reconcile_startup(self) -> None:
        # Discord may deliver guild availability after READY. Serialize both
        # callbacks so they cannot restore/advance the same session twice.
        async with self._reconcile_lock:
            if self._reconciled:
                return
            voice_ready = await self.controller.reconcile_voice_membership()
            controls_ready = (
                await self.controller.reconcile_session_messages(self._restart_notice_rooms)
                if voice_ready
                else False
            )
            self._reconciled = voice_ready and controls_ready
