"""Discord bot, slash commands, gateway listeners, and startup wiring."""

from __future__ import annotations

import logging
from importlib import resources

import discord
from discord import app_commands
from discord.ext import commands

from .config import BotConfig
from .controller import LecturaController
from .repository import SQLiteRepository
from .service import SessionService
from .views import QueueView, ReadingView, TextPickerView


LOGGER = logging.getLogger(__name__)
CATALOG_RESOURCE = "data/catalog.json"


class LecturaCog(commands.Cog):
    """Expose the queue commands and gateway-driven correction events."""

    def __init__(self, controller: LecturaController) -> None:
        self.controller = controller

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
        if before_id is None or before_id == after_id:
            return
        await self.controller.handle_voice_departure(member, before_id)


class LecturaBot(commands.Bot):
    """Configure intents, dependencies, persistent views, and command sync."""

    def __init__(self, config: BotConfig) -> None:
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True
        intents.voice_states = True
        intents.message_content = True
        intents.messages = True

        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.config = config
        self.repository = SQLiteRepository(config.database_path)
        self.service = SessionService(self.repository)
        self.controller = LecturaController(self, config, self.service)
        self._reconciled = False

    async def setup_hook(self) -> None:
        await self.repository.initialize()
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
        await self.service.initialize()

        await self.add_cog(LecturaCog(self.controller))
        self.add_view(QueueView(self.controller))
        self.add_view(TextPickerView(self.controller))
        self.add_view(ReadingView(self.controller))

        if self.config.dev_guild_id is not None:
            guild = discord.Object(id=self.config.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            # Development commands are guild-scoped for immediate updates.
            # Also synchronize an empty global tree so aliases from an older
            # LecturaBot deployment do not remain registered remotely.
            self.tree.clear_commands(guild=None)
            removed_global = await self.tree.sync()
            LOGGER.info(
                "synchronized global command cleanup; %s commands remain",
                len(removed_global),
            )
            synced = await self.tree.sync(guild=guild)
            LOGGER.info(
                "synced %s application commands to development guild %s",
                len(synced),
                self.config.dev_guild_id,
            )
        else:
            synced = await self.tree.sync()
            LOGGER.info("synced %s global application commands", len(synced))

    async def on_ready(self) -> None:
        if self.user is None:
            return
        LOGGER.info("connected as %s (%s)", self.user, self.user.id)
        if not self._reconciled:
            voice_ready = await self.controller.reconcile_voice_membership()
            controls_ready = (
                await self.controller.reconcile_session_messages()
                if voice_ready
                else False
            )
            self._reconciled = voice_ready and controls_ready
