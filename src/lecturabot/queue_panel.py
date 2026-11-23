"""The public queue and compact text picker as a Discord Components V2 card."""

from __future__ import annotations

from functools import partial
from typing import TYPE_CHECKING

import discord
from discord import ui

from .models import ChannelMode, Language, Level, MemberState, SessionPhase, SessionState
from .rendering import format_average

if TYPE_CHECKING:
    from .controller import LecturaController


CATALOG_LEVELS = {
    Language.SPANISH: ("Español", ("Principiante", "Intermedio", "Avanzado")),
    Language.ENGLISH: ("English", ("Beginner", "Intermediate", "Advanced")),
}


def _stats(member: MemberState) -> str:
    turns = "turno / turn" if member.turns == 1 else "turnos / turns"
    stats = f"{member.turns} {turns}"
    if member.average_seconds is not None:
        stats += f" · {format_average(member.average_seconds)} prom. / avg."
    return stats


def _name(value: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


class QueuePanel(ui.LayoutView):
    """One room panel. A state-free instance registers all persistent routes."""

    def __init__(
        self, controller: LecturaController, state: SessionState | None = None
    ) -> None:
        super().__init__(timeout=None)
        self.controller = controller
        card = ui.Container(ui.TextDisplay("## Cola de lectura / Reading queue"))
        self.add_item(card)
        phase = state.phase if state is not None else None
        member = state.current_member if state is not None else None
        if member is None and state is not None:
            card.add_item(ui.TextDisplay(
                "**Esperando para empezar / Waiting to start**\n"
                "Entra al canal de voz y pulsa **Unirse**. / Join voice, then press **Join**."
            ))
        elif member is not None and state is not None:
            reading = state.active_reading
            label = "Ahora leyendo / Reading now" if reading else "Eligiendo texto / Choosing a text"
            # Keep the reader mention for the one turn-start notification.
            detail = f"**{label}**\n<@{member.user_id}>"
            if reading:
                language, levels = CATALOG_LEVELS[reading.language]
                if reading.level is None:
                    language = _name(reading.custom_language_label or language)
                    language += " · Texto propio / Own text"
                else:
                    language += " · " + levels[list(Level).index(reading.level)]
                detail += f"\n{language} · <t:{reading.started_at}:R>"
            else:
                detail += "\n-# " + _stats(member)
            card.add_item(ui.TextDisplay(detail))

        active_phases = (SessionPhase.SELECTING, SessionPhase.READING)
        for active_phase in active_phases:
            if phase is None or phase is active_phase:
                card.add_item(ui.ActionRow(self._turn_button(active_phase, vote=False)))

        if phase is None or phase is SessionPhase.SELECTING:
            card.add_item(ui.Separator())
            picker = "### Elige un texto / Pick a text"
            if member is not None:
                name = _name(member.display_name)
                picker += f"\n-# Turno de {name} / {name}’s turn"
            card.add_item(ui.TextDisplay(picker))
            card.add_item(ui.ActionRow(self._button(
                "queue_custom_text", "Tu propio texto / Use your own text",
                controller.open_custom_text_modal, discord.ButtonStyle.primary,
            )))
            if state is None or state.channel_mode is ChannelMode.STANDARD:
                select = ui.Select(
                    custom_id="queue_catalog",
                    placeholder="Catálogo / Choose a catalog passage",
                    options=[
                        discord.SelectOption(label=f"{name} · {level}", value=f"{language}:{index}")
                        for language, (name, levels) in CATALOG_LEVELS.items()
                        for index, level in enumerate(levels)
                    ],
                )
                select.callback = partial(self._catalog, select)
                card.add_item(ui.ActionRow(select))

        if state is not None:
            card.add_item(ui.Separator())
            start = state.current_index
            ids = state.queue if start is None else state.queue[start + 1:] + state.queue[:start]
            label = "En cola / In the queue" if start is None else "Siguientes / Up next"
            lines = [f"### {label}"]
            for rank, user_id in enumerate(ids, 1 if start is None else 2):
                entry = state.members[user_id]
                lines.append(f"{rank} · **{_name(entry.display_name)}** · {_stats(entry)}")
            if not ids:
                lines.append("Nadie más en cola. / No other readers queued." if member else "Cola vacía. / Queue is empty.")
            card.add_item(ui.TextDisplay("\n".join(lines)))

        actions = [
            self._queue_button("enter_queue", "Unirse / Join", discord.ButtonStyle.success),
            self._queue_button("leave_queue", "Salir / Leave"),
            self._queue_button("instructions", "? Ayuda / Help"),
        ]
        if phase is None or phase is SessionPhase.WAITING:
            actions.append(self._queue_button(
                "start_reading", "Comenzar / Start", discord.ButtonStyle.primary,
                disabled=state is not None and len(state.queue) < controller.service.minimum_participants,
            ))
        card.add_item(ui.ActionRow(*actions))
        for active_phase in active_phases:
            if phase is None or phase is active_phase:
                count = len(state.skip_votes) if state is not None else 0
                card.add_item(ui.ActionRow(self._turn_button(active_phase, vote=True, count=count)))

    @staticmethod
    def _button(custom_id, label, callback, style=discord.ButtonStyle.secondary, *, disabled=False):
        button = ui.Button(label=label, custom_id=custom_id, style=style, disabled=disabled)
        button.callback = callback
        return button

    def _queue_button(self, action, label, style=discord.ButtonStyle.secondary, *, disabled=False):
        async def callback(interaction: discord.Interaction) -> None:
            await self.controller.handle_queue_action(interaction, action)

        return self._button(action, label, callback, style, disabled=disabled)

    def _turn_button(self, phase: SessionPhase, *, vote: bool, count: int = 0):
        if vote:
            label = "Votar para saltar el turno / Vote to skip the turn"
            if count:
                label += f" · {count}/{self.controller.service.skip_vote_threshold}"
            return self._button(
                f"queue_vote_{phase}", label,
                partial(self.controller.handle_skip_vote, panel_phase=phase),
            )
        return self._button(
            f"queue_pass_{phase}", "Pasar turno / Pass turn",
            partial(self.controller.handle_pass, panel_phase=phase),
        )

    async def _catalog(self, select: ui.Select, interaction: discord.Interaction) -> None:
        try:
            language_value, level_value = select.values[0].split(":")
            language = Language(language_value)
            level_index = int(level_value)
        except (IndexError, ValueError):
            await interaction.response.send_message(
                "Elige un texto del catálogo. / Choose a catalog passage.", ephemeral=True
            )
            return
        await self.controller.handle_catalog_selection(
            interaction, language=language, level_index=level_index
        )
