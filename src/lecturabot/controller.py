"""Discord transport adapter for the session service and UI renderers."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import discord

from .config import BotConfig, ChannelPairConfig
from .models import (
    ActiveReading,
    ChannelMode,
    CorrectionSource,
    Language,
    Level,
    SessionPhase,
    SessionState,
)
from .rendering import (
    RenderError,
    build_corrections_embed,
    build_instructions_embed,
    build_picker_embed,
    build_queue_embed,
    build_reading_content,
)
from .service import SessionError, SessionService, Transition, parse_correction_lines
from .views import (
    CorrectionModal,
    CustomTextModal,
    QueueView,
    ReadingView,
    TextPickerView,
)

if TYPE_CHECKING:
    from discord.ext import commands


LOGGER = logging.getLogger(__name__)
LEVEL_BY_INDEX = {
    0: Level.BEGINNER,
    1: Level.INTERMEDIATE,
    2: Level.ADVANCED,
}
MAX_CUSTOM_TEXT_LENGTH = 1_600


class LecturaController:
    """Authorize interactions, acknowledge Discord, and publish state changes.

    Static persistent ``custom_id`` values are treated only as routes. Every
    handler also validates guild, channel pair, phase, actor, and source message
    against the current persisted session before changing state.
    """

    def __init__(
        self,
        bot: commands.Bot,
        config: BotConfig,
        service: SessionService,
    ) -> None:
        self.bot = bot
        self.config = config
        self.service = service
        # Service locks protect state commits; these locks extend the critical
        # section through Discord publication so an older snapshot cannot win
        # a race and overwrite a newer queue or correction message.
        self._ui_locks: dict[int, asyncio.Lock] = {}

    def _ui_lock_for(self, text_channel_id: int) -> asyncio.Lock:
        return self._ui_locks.setdefault(text_channel_id, asyncio.Lock())

    async def show_queue(self, interaction: discord.Interaction) -> None:
        try:
            pair, member = self._interaction_context(interaction)
            self._require_matching_voice(member, pair)
            await interaction.response.defer(thinking=True)
            async with self._ui_lock_for(pair.text_channel_id):
                state = await self.service.get_or_create_session(
                    guild_id=interaction.guild_id or 0,
                    pair=pair,
                )
                await self._publish_queue_interaction(interaction, state)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("failed to show queue")
            await self._send_error(interaction, self._generic_error())

    async def handle_queue_action(
        self,
        interaction: discord.Interaction,
        action: str,
    ) -> None:
        if action == "instructions":
            await interaction.response.send_message(
                embed=build_instructions_embed(),
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return

        try:
            pair, member = self._interaction_context(interaction)
            if action in {"enter_queue", "start_reading"}:
                self._require_matching_voice(member, pair)
            if interaction.message is None:
                raise self._stale_turn()
            await interaction.response.defer()
            async with self._ui_lock_for(pair.text_channel_id):
                state = await self.service.get_session(pair.text_channel_id)
                if (
                    state is None
                    or state.queue_message_id != interaction.message.id
                ):
                    raise SessionError(
                        "stale_queue",
                        "Este panel de cola ya no está activo. / "
                        "This queue panel is no longer active.",
                    )
                if action == "enter_queue":
                    transition = await self.service.join(
                        text_channel_id=pair.text_channel_id,
                        user_id=member.id,
                        display_name=member.display_name,
                    )
                elif action == "leave_queue":
                    transition = await self.service.leave(
                        text_channel_id=pair.text_channel_id,
                        user_id=member.id,
                    )
                elif action == "start_reading":
                    transition = await self.service.start(
                        text_channel_id=pair.text_channel_id,
                        actor_id=member.id,
                    )
                else:
                    raise SessionError(
                        "unknown_action",
                        "Acción desconocida. / Unknown action.",
                    )
                await self._apply_transition(transition)
            await interaction.followup.send(transition.notice, ephemeral=True)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("queue action failed: %s", action)
            await self._send_error(interaction, self._generic_error())

    async def handle_catalog_selection(
        self,
        interaction: discord.Interaction,
        *,
        language: Language,
        level_index: int,
    ) -> None:
        published = False
        try:
            pair, member = self._interaction_context(interaction)
            self._require_matching_voice(member, pair)
            if interaction.message is None:
                raise self._stale_turn()
            level = LEVEL_BY_INDEX.get(level_index)
            if level is None:
                raise SessionError(
                    "invalid_level",
                    "Nivel inválido. / Invalid level.",
                )
            await interaction.response.defer()
            async with self._ui_lock_for(pair.text_channel_id):
                transition = await self.service.begin_catalog_reading(
                    text_channel_id=pair.text_channel_id,
                    reader_id=member.id,
                    picker_message_id=interaction.message.id,
                    language=language,
                    level=level,
                )
                await self._publish_reading(transition)
                published = True
                await self._retire_message(
                    transition.state.text_channel_id,
                    transition.retired_picker_message_id,
                )
                await self._refresh_queue(transition.state)
            await interaction.followup.send(transition.notice, ephemeral=True)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except RenderError as error:
            LOGGER.warning("catalog text could not be rendered: %s", error)
            if published:
                await self._send_error(interaction, self._generic_error())
            else:
                await self._rollback_after_publish_failure(
                    interaction,
                    picker_message_id=(
                        None
                        if interaction.message is None
                        else interaction.message.id
                    ),
                )
        except Exception:
            LOGGER.exception("catalog selection failed")
            if published:
                await self._send_error(interaction, self._generic_error())
            else:
                await self._rollback_after_publish_failure(
                    interaction,
                    picker_message_id=(
                        None
                        if interaction.message is None
                        else interaction.message.id
                    ),
                )

    async def open_custom_text_modal(
        self,
        interaction: discord.Interaction,
        *,
        language: Language,
    ) -> None:
        try:
            pair, member = self._interaction_context(interaction)
            self._require_matching_voice(member, pair)
            if interaction.message is None:
                raise self._stale_turn()
            state = await self.service.get_session(pair.text_channel_id)
            if (
                state is None
                or state.phase is not SessionPhase.SELECTING
                or state.current_user_id != member.id
                or state.picker_message_id != interaction.message.id
            ):
                raise self._stale_turn()
            await interaction.response.send_modal(
                CustomTextModal(
                    self,
                    text_channel_id=pair.text_channel_id,
                    picker_message_id=interaction.message.id,
                    language=language,
                    ask_language=state.channel_mode is ChannelMode.CUSTOM_ONLY,
                    max_text_length=MAX_CUSTOM_TEXT_LENGTH,
                )
            )
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("failed to open custom-text modal")
            await self._send_error(interaction, self._generic_error())

    async def submit_custom_text(
        self,
        interaction: discord.Interaction,
        *,
        text_channel_id: int,
        picker_message_id: int,
        language: Language,
        body: str,
        custom_language_label: str | None,
    ) -> None:
        published = False
        try:
            pair, member = self._interaction_context(interaction)
            if pair.text_channel_id != text_channel_id:
                raise self._stale_turn()
            self._require_matching_voice(member, pair)
            await interaction.response.defer(ephemeral=True, thinking=True)
            async with self._ui_lock_for(text_channel_id):
                transition = await self.service.begin_custom_reading(
                    text_channel_id=text_channel_id,
                    reader_id=member.id,
                    picker_message_id=picker_message_id,
                    language=language,
                    body=body,
                    custom_language_label=custom_language_label,
                )
                await self._publish_reading(transition)
                published = True
                await self._retire_message(text_channel_id, picker_message_id)
                await self._refresh_queue(transition.state)
            await interaction.followup.send(transition.notice, ephemeral=True)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except RenderError as error:
            LOGGER.warning("custom text could not be rendered: %s", error)
            if published:
                await self._send_error(interaction, self._generic_error())
            else:
                await self._rollback_after_publish_failure(
                    interaction,
                    text_channel_id=text_channel_id,
                    picker_message_id=picker_message_id,
                )
        except Exception:
            LOGGER.exception("custom-text submission failed")
            if published:
                await self._send_error(interaction, self._generic_error())
            else:
                await self._rollback_after_publish_failure(
                    interaction,
                    text_channel_id=text_channel_id,
                    picker_message_id=picker_message_id,
                )

    async def open_correction_modal(self, interaction: discord.Interaction) -> None:
        try:
            pair, member = self._interaction_context(interaction)
            self._require_matching_voice(member, pair)
            if interaction.message is None:
                raise self._stale_turn()
            state = await self.service.get_session(pair.text_channel_id)
            reading = None if state is None else state.active_reading
            if (
                state is None
                or state.phase is not SessionPhase.READING
                or reading is None
                or reading.message_id != interaction.message.id
            ):
                raise self._stale_turn()
            if reading.reader_id == member.id:
                raise SessionError(
                    "reader_correction",
                    "El lector no puede corregirse a sí mismo. / "
                    "The reader cannot submit their own corrections.",
                )
            await interaction.response.send_modal(
                CorrectionModal(
                    self,
                    text_channel_id=pair.text_channel_id,
                    reading_message_id=interaction.message.id,
                    opener_interaction_id=interaction.id,
                )
            )
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("failed to open correction modal")
            await self._send_error(interaction, self._generic_error())

    async def submit_corrections(
        self,
        interaction: discord.Interaction,
        *,
        text_channel_id: int,
        reading_message_id: int,
        raw_items: str,
    ) -> None:
        try:
            pair, member = self._interaction_context(interaction)
            if pair.text_channel_id != text_channel_id:
                raise self._stale_turn()
            self._require_matching_voice(member, pair)
            items = parse_correction_lines(raw_items)
            await interaction.response.defer(ephemeral=True, thinking=True)
            async with self._ui_lock_for(text_channel_id):
                transition = await self.service.add_corrections(
                    text_channel_id=text_channel_id,
                    reading_message_id=reading_message_id,
                    corrector_id=member.id,
                    corrector_display_name=member.display_name,
                    items=items,
                    source=CorrectionSource.BUTTON,
                    validator=self._validate_reading_render,
                )
                await self._refresh_reading(transition.state)
            await interaction.followup.send(transition.notice, ephemeral=True)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("correction submission failed")
            await self._send_error(interaction, self._generic_error())

    async def handle_pass(self, interaction: discord.Interaction) -> None:
        try:
            pair, member = self._interaction_context(interaction)
            self._require_matching_voice(member, pair)
            if interaction.message is None:
                raise self._stale_turn()
            await interaction.response.defer()
            async with self._ui_lock_for(pair.text_channel_id):
                transition = await self.service.pass_turn(
                    text_channel_id=pair.text_channel_id,
                    actor_id=member.id,
                    source_message_id=interaction.message.id,
                )
                await self._apply_transition(transition)
            await interaction.followup.send(transition.notice, ephemeral=True)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("pass-turn action failed")
            await self._send_error(interaction, self._generic_error())

    async def handle_skip_vote(self, interaction: discord.Interaction) -> None:
        """Record a fixed-threshold AFK-skip vote from a queued listener."""
        try:
            pair, member = self._interaction_context(interaction)
            self._require_matching_voice(member, pair)
            if interaction.message is None:
                raise self._stale_turn()
            await interaction.response.defer()
            async with self._ui_lock_for(pair.text_channel_id):
                transition = await self.service.vote_to_skip(
                    text_channel_id=pair.text_channel_id,
                    voter_id=member.id,
                    source_message_id=interaction.message.id,
                )
                if transition.advanced:
                    await self._apply_transition(transition)
            await interaction.followup.send(transition.notice, ephemeral=True)
        except SessionError as error:
            await self._send_error(interaction, error.user_message)
        except Exception:
            LOGGER.exception("skip-AFK vote failed")
            await self._send_error(interaction, self._generic_error())

    async def handle_reply(self, message: discord.Message) -> None:
        if message.author.bot or message.reference is None:
            return
        if (
            message.guild is None
            or message.guild.id != self.config.guild_id
            or not isinstance(message.author, discord.Member)
        ):
            return
        pair = self.config.pair_for_text_channel(message.channel.id)
        if pair is None:
            return
        try:
            self._require_matching_voice(message.author, pair)
        except SessionError:
            return
        referenced_id = message.reference.message_id
        if referenced_id is None:
            return
        state = await self.service.find_by_reading_message(referenced_id)
        if state is None or state.text_channel_id != message.channel.id:
            return
        try:
            items = parse_correction_lines(message.content)
            async with self._ui_lock_for(state.text_channel_id):
                transition = await self.service.add_corrections(
                    text_channel_id=state.text_channel_id,
                    reading_message_id=referenced_id,
                    corrector_id=message.author.id,
                    corrector_display_name=message.author.display_name,
                    items=items,
                    source=CorrectionSource.REPLY,
                    validator=self._validate_reading_render,
                )
                await self._refresh_reading(transition.state)
        except SessionError:
            LOGGER.debug(
                "ignored invalid correction reply from user %s",
                message.author.id,
            )
        except Exception:
            LOGGER.exception("failed to process correction reply")

    async def handle_voice_departure(
        self,
        member: discord.Member | discord.Object,
        voice_id: int,
    ) -> None:
        pair = self.config.pair_for_voice_channel(voice_id)
        if pair is None:
            return
        try:
            async with self._ui_lock_for(pair.text_channel_id):
                state = await self.service.get_session(pair.text_channel_id)
                if state is None or member.id not in state.members:
                    return
                transition = await self.service.leave(
                    text_channel_id=pair.text_channel_id,
                    user_id=member.id,
                )
                await self._apply_transition(transition)
        except SessionError:
            LOGGER.debug("voice departure raced with queue removal", exc_info=True)
        except Exception:
            LOGGER.exception("failed to process voice departure")

    async def reconcile_voice_membership(self) -> bool:
        """Remove persisted queue entries that are no longer in paired voice."""
        guild = self.bot.get_guild(self.config.guild_id)
        if guild is None:
            return False
        for pair in self.config.channel_pairs:
            state = await self.service.get_session(pair.text_channel_id)
            if state is None:
                continue
            absent_user_ids: list[int] = []
            for user_id in state.queue:
                member = guild.get_member(user_id)
                current_voice_id = (
                    None
                    if member is None
                    or member.voice is None
                    or member.voice.channel is None
                    else member.voice.channel.id
                )
                if current_voice_id != pair.voice_channel_id:
                    absent_user_ids.append(user_id)

            # Remove waiting participants first. If the current reader is also
            # absent, removing them last avoids transiently activating and
            # pinging another member who is about to be removed too.
            current_id = state.current_user_id
            absent_user_ids.sort(key=lambda user_id: user_id == current_id)
            for user_id in absent_user_ids:
                member = guild.get_member(user_id)
                if (
                    member is not None
                    and member.voice is not None
                    and member.voice.channel is not None
                    and member.voice.channel.id == pair.voice_channel_id
                ):
                    continue
                await self.handle_voice_departure(
                    member or discord.Object(id=user_id),
                    pair.voice_channel_id,
                )
        return True

    async def reconcile_session_messages(self) -> bool:
        """Restore canonical panels and active controls after a restart."""
        complete = True
        for pair in self.config.channel_pairs:
            try:
                async with self._ui_lock_for(pair.text_channel_id):
                    state = await self.service.get_session(pair.text_channel_id)
                    if state is None:
                        continue
                    await self._refresh_queue(state)
                    if state.phase is SessionPhase.SELECTING:
                        await self._ensure_picker(state)
                    elif state.phase is SessionPhase.READING:
                        reading = state.active_reading
                        if reading is None:
                            continue
                        if not await self._refresh_reading(state):
                            recovered = await self.service.rollback_reading(
                                text_channel_id=state.text_channel_id,
                                reader_id=reading.reader_id,
                            )
                            await self._ensure_picker(recovered)
            except Exception:
                complete = False
                LOGGER.exception(
                    "failed to reconcile reading controls for %s",
                    pair.name,
                )
        return complete

    async def _publish_reading(self, transition: Transition) -> None:
        """Publish a prepared reading, then attach its Discord ID to state."""
        reading = transition.state.active_reading
        if reading is None:
            raise SessionError(
                "missing_reading",
                "No se pudo preparar el texto. / The reading could not be prepared.",
            )
        channel = await self._text_channel(transition.state.text_channel_id)
        content = build_reading_content(reading)
        embed = build_corrections_embed(reading)
        message = await channel.send(
            content=content,
            embed=embed,
            view=ReadingView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        try:
            await self.service.set_reading_message(
                text_channel_id=transition.state.text_channel_id,
                reader_id=reading.reader_id,
                message_id=message.id,
            )
        except Exception:
            # The reading is visible but not authoritative. Remove its controls
            # so it cannot masquerade as a live turn while rollback restores
            # the picker.
            try:
                await message.edit(view=None)
            except discord.HTTPException:
                LOGGER.exception(
                    "failed to retire unpublished reading message %s",
                    message.id,
                )
            raise

    async def _apply_transition(self, transition: Transition) -> None:
        publication_error: Exception | None = None
        try:
            await self._retire_message(
                transition.state.text_channel_id,
                transition.retired_picker_message_id,
            )
            await self._retire_message(
                transition.state.text_channel_id,
                transition.retired_reading_message_id,
            )
            await self._refresh_queue(
                transition.state,
                repost=transition.repost_queue,
            )
        except Exception as error:
            # A queue edit should not prevent the newly committed reader from
            # receiving their picker. Surface the edit failure afterward.
            publication_error = error
            LOGGER.exception("failed to refresh transition messages")
        if transition.activated_reader_id is not None:
            try:
                await self._send_picker(transition.state)
            except Exception:
                recovered = await self.service.pause_unpublished_selection(
                    text_channel_id=transition.state.text_channel_id,
                    reader_id=transition.activated_reader_id,
                )
                await self._refresh_queue(recovered)
                raise
        if publication_error is not None:
            raise publication_error

    async def _refresh_queue(
        self,
        state: SessionState,
        *,
        repost: bool = False,
    ) -> discord.Message:
        """Create, edit, or deliberately repost a room's queue panel."""
        channel = await self._text_channel(state.text_channel_id)
        message: discord.Message | None = None
        previous_message_id = state.queue_message_id
        if not repost and previous_message_id is not None:
            try:
                message = await channel.fetch_message(previous_message_id)
            except discord.NotFound:
                message = None
        if message is None:
            message = await channel.send(
                embed=self._queue_embed(state),
                view=QueueView(self),
                allowed_mentions=discord.AllowedMentions.none(),
            )
            await self._persist_queue_message(state, message)
            if repost and previous_message_id is not None:
                await self._retire_message(
                    state.text_channel_id,
                    previous_message_id,
                )
            return message
        await message.edit(
            embed=self._queue_embed(state),
            view=QueueView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return message

    async def _publish_queue_interaction(
        self,
        interaction: discord.Interaction,
        state: SessionState,
    ) -> discord.InteractionMessage:
        """Use the command's public response as a fresh queue panel."""
        previous_message_id = state.queue_message_id
        message = await interaction.edit_original_response(
            embed=self._queue_embed(state),
            view=QueueView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        await self._persist_queue_message(state, message)
        if previous_message_id is not None and previous_message_id != message.id:
            await self._retire_message(state.text_channel_id, previous_message_id)
        return message

    async def _persist_queue_message(
        self,
        state: SessionState,
        message: discord.Message,
    ) -> None:
        try:
            await self.service.set_queue_message(
                state.text_channel_id,
                message.id,
            )
        except Exception:
            try:
                await message.edit(view=None)
            except discord.HTTPException:
                LOGGER.exception(
                    "failed to retire unpublished queue message %s",
                    message.id,
                )
            raise

    async def _send_picker(self, state: SessionState) -> None:
        member = state.current_member
        if member is None or state.phase is not SessionPhase.SELECTING:
            return
        channel = await self._text_channel(state.text_channel_id)
        message = await channel.send(
            content=f"<@{member.user_id}>",
            embed=build_picker_embed(member.display_name),
            view=TextPickerView(self),
            allowed_mentions=discord.AllowedMentions(
                everyone=False,
                roles=False,
                users=[discord.Object(id=member.user_id)],
                replied_user=False,
            ),
        )
        try:
            await self.service.set_picker_message(
                text_channel_id=state.text_channel_id,
                reader_id=member.user_id,
                message_id=message.id,
            )
        except Exception:
            try:
                await message.edit(view=None)
            except discord.HTTPException:
                LOGGER.exception(
                    "failed to retire unpublished picker message %s",
                    message.id,
                )
            raise

    async def _ensure_picker(self, state: SessionState) -> None:
        """Restore an existing picker or publish a replacement if it vanished."""
        member = state.current_member
        if member is None or state.phase is not SessionPhase.SELECTING:
            return
        channel = await self._text_channel(state.text_channel_id)
        message: discord.Message | None = None
        if state.picker_message_id is not None:
            try:
                message = await channel.fetch_message(state.picker_message_id)
            except discord.NotFound:
                message = None
        if message is None:
            await self._send_picker(state)
            return
        await message.edit(
            content=f"<@{member.user_id}>",
            embed=build_picker_embed(member.display_name),
            view=TextPickerView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _refresh_reading(self, state: SessionState) -> bool:
        reading = state.active_reading
        if reading is None or reading.message_id is None:
            return False
        channel = await self._text_channel(state.text_channel_id)
        try:
            message = await channel.fetch_message(reading.message_id)
        except discord.NotFound:
            LOGGER.warning("active reading message %s no longer exists", reading.message_id)
            return False
        await message.edit(
            content=build_reading_content(reading),
            embed=build_corrections_embed(reading),
            view=ReadingView(self),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        return True

    async def _retire_message(
        self,
        text_channel_id: int,
        message_id: int | None,
    ) -> None:
        if message_id is None:
            return
        channel = await self._text_channel(text_channel_id)
        try:
            message = await channel.fetch_message(message_id)
            await message.edit(view=None)
        except discord.HTTPException:
            LOGGER.debug("could not retire message %s", message_id)

    async def _rollback_after_publish_failure(
        self,
        interaction: discord.Interaction,
        *,
        text_channel_id: int | None = None,
        picker_message_id: int | None = None,
    ) -> None:
        try:
            member = self._guild_member(interaction)
            channel_id = text_channel_id or interaction.channel_id
            if channel_id is not None:
                async with self._ui_lock_for(channel_id):
                    state = await self.service.rollback_reading(
                        text_channel_id=channel_id,
                        reader_id=member.id,
                        picker_message_id=picker_message_id,
                    )
                    await self._refresh_queue(state)
        except Exception:
            LOGGER.exception("failed to roll back reading state")
        await self._send_error(interaction, self._generic_error())

    def _interaction_context(
        self, interaction: discord.Interaction
    ) -> tuple[ChannelPairConfig, discord.Member]:
        if interaction.guild_id != self.config.guild_id:
            raise SessionError(
                "wrong_guild",
                "Este bot no está configurado para este servidor. / "
                "This bot is not configured for this server.",
            )
        if interaction.channel_id is None:
            raise SessionError(
                "missing_channel",
                "Usa este control en un canal de lectura. / "
                "Use this control in a reading channel.",
            )
        pair = self.config.pair_for_text_channel(interaction.channel_id)
        if pair is None:
            raise SessionError(
                "wrong_channel",
                "Usa el canal de texto correspondiente a tu canal de voz. / "
                "Use the text channel matching your voice channel.",
            )
        return pair, self._guild_member(interaction)

    @staticmethod
    def _guild_member(interaction: discord.Interaction) -> discord.Member:
        if not isinstance(interaction.user, discord.Member):
            raise SessionError(
                "guild_only",
                "Esta acción solo funciona dentro del servidor. / "
                "This action only works inside the server.",
            )
        return interaction.user

    @staticmethod
    def _require_matching_voice(
        member: discord.Member,
        pair: ChannelPairConfig,
    ) -> None:
        voice_channel_id = (
            None
            if member.voice is None or member.voice.channel is None
            else member.voice.channel.id
        )
        if voice_channel_id != pair.voice_channel_id:
            raise SessionError(
                "wrong_voice",
                "Únete al canal de voz correspondiente primero. / "
                "Join the matching voice channel first.",
            )

    async def _text_channel(self, channel_id: int) -> discord.TextChannel:
        channel = self.bot.get_channel(channel_id)
        if channel is None:
            channel = await self.bot.fetch_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            raise RuntimeError(f"configured text channel {channel_id} is unavailable")
        return channel

    def _queue_embed(self, state: SessionState) -> discord.Embed:
        return build_queue_embed(
            state,
            bug_contact_user_id=self.config.bug_contact_user_id,
            text_contact_user_id=self.config.text_contact_user_id,
        )

    @staticmethod
    def _validate_reading_render(reading: ActiveReading) -> None:
        """Raise before persistence if a correction would exceed Discord limits."""
        build_reading_content(reading)
        build_corrections_embed(reading)

    @staticmethod
    def _stale_turn() -> SessionError:
        return SessionError(
            "stale_turn",
            "Ese turno ya no está activo. / That turn is no longer active.",
        )

    async def _send_error(
        self,
        interaction: discord.Interaction,
        message: str,
    ) -> None:
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            LOGGER.exception("failed to deliver interaction error")

    @staticmethod
    def _generic_error() -> str:
        return (
            "Ocurrió un error inesperado; inténtalo de nuevo. / "
            "An unexpected error occurred; please try again."
        )
