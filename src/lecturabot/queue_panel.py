"""Discord Components V2 panels for the room queue and passage picker."""

from __future__ import annotations

from functools import partial
import re
from typing import TYPE_CHECKING

import discord
from discord import ui

from .models import ChannelMode, Language, Level, MemberState, SessionPhase, SessionState
from .rendering import format_average

if TYPE_CHECKING:
    from .bot import LecturaBot
    from .controller import LecturaController


GUIDE_URL = "https://discord.com/channels/243838819743432704/1525628752074113127/1574529773168754809"
CATALOG_LEVELS = {
    Language.SPANISH: ("Español", ("Principiante", "Intermedio", "Avanzado")),
    Language.ENGLISH: ("English", ("Beginner", "Intermediate", "Advanced")),
}
CATALOG_STYLES = {
    Language.SPANISH: ("📗", discord.ButtonStyle.success),
    Language.ENGLISH: ("📘", discord.ButtonStyle.primary),
}


def _stats(member: MemberState) -> str:
    turns = "turno / turn" if member.turns == 1 else "turnos / turns"
    stats = f"{member.turns} {turns}"
    if member.average_seconds is not None:
        stats += f" · {format_average(member.average_seconds)} prom. / avg."
    return stats


def _name(value: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


class QueueSkipButton(
    ui.DynamicItem[ui.Button],
    template=r"queue_skip:(?P<session_id>\d+):(?P<turn_number>\d+):(?P<phase>selecting|reading)",
):
    """Bind the shared skip button to the turn shown on a persistent queue."""

    def __init__(
        self, session_id: int, turn_number: int, phase: SessionPhase, label: str | None,
    ) -> None:
        self.session_id = session_id
        self.turn_number = turn_number
        self.phase = phase
        super().__init__(ui.Button(
            label=label,
            custom_id=f"queue_skip:{session_id}:{turn_number}:{phase.value}",
        ))

    @classmethod
    async def from_custom_id(
        cls, interaction: discord.Interaction, item: ui.Button, match: re.Match[str], /,
    ) -> QueueSkipButton:
        return cls(
            int(match["session_id"]),
            int(match["turn_number"]),
            SessionPhase(match["phase"]),
            item.label,
        )

    async def callback(self, interaction: discord.Interaction[LecturaBot]) -> None:
        await interaction.client.controller.handle_skip_turn(
            interaction, session_id=self.session_id,
            turn_number=self.turn_number, phase=self.phase,
        )


class QueuePanel(ui.LayoutView):
    """Render either message. A state-free instance registers the shared routes."""

    def __init__(
        self,
        controller: LecturaController,
        state: SessionState | None = None,
        *,
        includes_picker: bool = False,
        picker_only: bool = False,
    ) -> None:
        super().__init__(timeout=None)
        self.controller = controller
        selecting = state is not None and state.phase is SessionPhase.SELECTING
        reader = state.current_user_id if state is not None else None
        if (picker_only or includes_picker) and selecting and reader is not None:
            self.add_item(ui.TextDisplay(
                f"<@{reader}> · **Elige un texto / Pick a text**"
            ))
        card = ui.Container()
        self.add_item(card)
        if not picker_only:
            self._add_queue(card, state)
        if state is None or picker_only or (includes_picker and selecting):
            if not picker_only:
                card.add_item(ui.Separator())
            self._add_picker(card, state)

    def _add_queue(self, card: ui.Container, state: SessionState | None) -> None:
        card.add_item(ui.TextDisplay("## Cola de lectura / Reading queue"))
        member = state.current_member if state is not None else None
        if state is not None:
            if member is None:
                card.add_item(ui.TextDisplay(
                    "**Esperando para empezar / Waiting to start**\n"
                    "Entra al canal de voz y pulsa **Unirse**. / Join voice, then press **Join**."
                ))
            else:
                card.add_item(ui.TextDisplay(self._current_reader(state, member)))
            start = state.current_index
            ids = state.queue if start is None else state.queue[start + 1:] + state.queue[:start]
            lines = [
                f"{rank}. <@{user_id}> · {_stats(state.members[user_id])}"
                for rank, user_id in enumerate(ids, 1 if start is None else 2)
            ]
            if lines:
                card.add_item(ui.Separator())
                card.add_item(ui.TextDisplay("\n".join(lines)))
            elif member is None:
                card.add_item(ui.TextDisplay("Cola vacía. / Queue is empty."))
            if member is not None:
                count = len(state.skip_votes)
                threshold = self.controller.service.skip_vote_threshold
                label = "Saltar turno / Skip Turn"
                if count:
                    label += f" · {count}/{threshold}"
                card.add_item(ui.ActionRow(QueueSkipButton(
                    state.session_id, state.turn_number, state.phase, label,
                )))

        card.add_item(ui.Separator())
        actions = [
            self._queue_button("enter_queue", "Unirse / Join", discord.ButtonStyle.success),
            self._queue_button("leave_queue", "Salir / Leave", discord.ButtonStyle.danger),
        ]
        if state is None or state.phase is SessionPhase.WAITING:
            actions.append(self._queue_button(
                "start_reading", "Comenzar / Start", discord.ButtonStyle.primary,
                disabled=state is not None and len(state.queue) < self.controller.service.minimum_participants,
            ))
        card.add_item(ui.ActionRow(*actions))
        card.add_item(ui.TextDisplay(f"-# [Cómo participar / How to join]({GUIDE_URL})"))

    def _add_picker(self, card: ui.Container, state: SessionState | None) -> None:
        if state is None or state.channel_mode is ChannelMode.STANDARD:
            for language, (name, levels) in CATALOG_LEVELS.items():
                emoji, style = CATALOG_STYLES[language]
                card.add_item(ui.TextDisplay(f"-# {emoji} {name}"))
                card.add_item(ui.ActionRow(*(
                    self._button(
                        f"queue_catalog:{language.value}:{index}", level,
                        partial(self.controller.handle_catalog_selection, language=language, level_index=index),
                        style,
                    )
                    for index, level in enumerate(levels)
                )))
        card.add_item(ui.ActionRow(self._button(
            "queue_custom_text", "Tu propio texto / Use your own text",
            self.controller.open_custom_text_modal,
        )))

    @staticmethod
    def _current_reader(state: SessionState, member: MemberState) -> str:
        reading = state.active_reading
        label = "Ahora leyendo / Reading now" if reading else "Eligiendo texto / Choosing a text"
        detail = f"**{label}**\n1. <@{member.user_id}>"
        if reading is None:
            return detail + f"\n-# {_stats(member)}"
        language, levels = CATALOG_LEVELS[reading.language]
        if reading.level is None:
            language = _name(reading.custom_language_label or language)
            language += " · Texto propio / Own text"
        else:
            language += " · " + levels[list(Level).index(reading.level)]
        return f"{detail}\n{language} · <t:{reading.started_at}:R>"

    @staticmethod
    def _button(custom_id, label, callback, style=discord.ButtonStyle.secondary, *, disabled=False):
        button = ui.Button(label=label, custom_id=custom_id, style=style, disabled=disabled)
        button.callback = callback
        return button

    def _queue_button(self, action, label, style, *, disabled=False):
        async def callback(interaction: discord.Interaction) -> None:
            await self.controller.handle_queue_action(interaction, action)

        return self._button(action, label, callback, style, disabled=disabled)
