"""Persistent Discord component views and turn-input modals."""

from __future__ import annotations

from typing import TYPE_CHECKING

import discord

from .models import Language

if TYPE_CHECKING:
    from .controller import LecturaController


class QueueView(discord.ui.View):
    """Persistent queue controls plus a provisional confirmed start action."""

    def __init__(self, controller: LecturaController) -> None:
        super().__init__(timeout=None)
        self.controller = controller

    @discord.ui.button(
        label="Unirse / Enter",
        style=discord.ButtonStyle.success,
        custom_id="enter_queue",
        row=0,
    )
    async def enter_queue(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QueueView],
    ) -> None:
        await self.controller.handle_queue_action(interaction, "enter_queue")

    @discord.ui.button(
        label="Salir / Leave",
        style=discord.ButtonStyle.danger,
        custom_id="leave_queue",
        row=0,
    )
    async def leave_queue(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QueueView],
    ) -> None:
        await self.controller.handle_queue_action(interaction, "leave_queue")

    @discord.ui.button(
        label="Instrucciones / Instructions",
        style=discord.ButtonStyle.secondary,
        custom_id="instructions",
        row=0,
    )
    async def instructions(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QueueView],
    ) -> None:
        await self.controller.handle_queue_action(interaction, "instructions")

    @discord.ui.button(
        label="Comenzar Lectura / Start Reading",
        style=discord.ButtonStyle.primary,
        custom_id="start_reading",
        row=0,
    )
    async def start_reading(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[QueueView],
    ) -> None:
        await self.controller.handle_queue_action(interaction, "start_reading")


class TextPickerView(discord.ui.View):
    """Exact three-row Spanish/English text picker captured from the bot."""

    def __init__(self, controller: LecturaController) -> None:
        super().__init__(timeout=None)
        self.controller = controller

    async def _catalog(
        self,
        interaction: discord.Interaction,
        language: Language,
        level_index: int,
    ) -> None:
        await self.controller.handle_catalog_selection(
            interaction,
            language=language,
            level_index=level_index,
        )

    @discord.ui.button(
        label="Español Principiante",
        style=discord.ButtonStyle.success,
        custom_id="find_reading0_0",
        row=0,
    )
    async def spanish_beginner(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self._catalog(interaction, Language.SPANISH, 0)

    @discord.ui.button(
        label="Español Intermedio",
        style=discord.ButtonStyle.success,
        custom_id="find_reading0_1",
        row=0,
    )
    async def spanish_intermediate(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self._catalog(interaction, Language.SPANISH, 1)

    @discord.ui.button(
        label="Español Avanzado",
        style=discord.ButtonStyle.success,
        custom_id="find_reading0_2",
        row=0,
    )
    async def spanish_advanced(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self._catalog(interaction, Language.SPANISH, 2)

    @discord.ui.button(
        label="Tu propio texto / Your own text - Español",
        style=discord.ButtonStyle.primary,
        custom_id="submit_reading0",
        row=0,
    )
    async def custom_spanish(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self.controller.open_custom_text_modal(
            interaction,
            language=Language.SPANISH,
        )

    @discord.ui.button(
        label="English Beginner",
        style=discord.ButtonStyle.success,
        custom_id="find_reading1_0",
        row=1,
    )
    async def english_beginner(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self._catalog(interaction, Language.ENGLISH, 0)

    @discord.ui.button(
        label="English Intermediate",
        style=discord.ButtonStyle.success,
        custom_id="find_reading1_1",
        row=1,
    )
    async def english_intermediate(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self._catalog(interaction, Language.ENGLISH, 1)

    @discord.ui.button(
        label="English Advanced",
        style=discord.ButtonStyle.success,
        custom_id="find_reading1_2",
        row=1,
    )
    async def english_advanced(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self._catalog(interaction, Language.ENGLISH, 2)

    @discord.ui.button(
        label="Tu propio texto / Your own text - English",
        style=discord.ButtonStyle.primary,
        custom_id="submit_reading1",
        row=1,
    )
    async def custom_english(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self.controller.open_custom_text_modal(
            interaction,
            language=Language.ENGLISH,
        )

    @discord.ui.button(
        label="Pasar Turno / Pass Turn",
        style=discord.ButtonStyle.danger,
        custom_id="pass_select",
        row=2,
    )
    async def pass_selection(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[TextPickerView],
    ) -> None:
        await self.controller.handle_pass(interaction)


class ReadingView(discord.ui.View):
    """Controls attached to an active reading and correction summary."""

    def __init__(self, controller: LecturaController) -> None:
        super().__init__(timeout=None)
        self.controller = controller

    @discord.ui.button(
        label="Poner Correcciones / Submit Corrections",
        style=discord.ButtonStyle.success,
        custom_id="submit_correction",
        row=0,
    )
    async def submit_correction(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ReadingView],
    ) -> None:
        await self.controller.open_correction_modal(interaction)

    @discord.ui.button(
        label="Pasar turno / Pass Turn",
        style=discord.ButtonStyle.danger,
        custom_id="pass_reading",
        row=0,
    )
    async def pass_reading(
        self,
        interaction: discord.Interaction,
        _: discord.ui.Button[ReadingView],
    ) -> None:
        await self.controller.handle_pass(interaction)


class CustomTextModal(discord.ui.Modal):
    """Collect a turn-local custom text and optional other-language label."""

    def __init__(
        self,
        controller: LecturaController,
        *,
        text_channel_id: int,
        picker_message_id: int,
        language: Language,
        ask_language: bool,
        max_text_length: int,
    ) -> None:
        super().__init__(
            title="Tu propio texto / Your own text",
            # Modal IDs are registered globally by discord.py. Including the
            # picker ID prevents same-language forms opened in two rooms from
            # replacing one another's callback state.
            custom_id=f"custom_text_modal:{language.value}:{picker_message_id}",
        )
        self.controller = controller
        self.text_channel_id = text_channel_id
        self.picker_message_id = picker_message_id
        self.language = language
        self.language_input: discord.ui.TextInput | None = None

        if ask_language:
            self.language_input = discord.ui.TextInput(
                custom_id="custom_language",
                placeholder="e.g. Français, 日本語, Deutsch",
                min_length=2,
                max_length=40,
            )
            self.add_item(
                discord.ui.Label(
                    text="Idioma / Language",
                    component=self.language_input,
                )
            )

        self.body_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            custom_id="custom_text",
            placeholder="Pega aquí el texto que vas a leer. / Paste your text here.",
            min_length=1,
            max_length=max_text_length,
        )
        self.add_item(
            discord.ui.Label(
                text="Texto / Text",
                component=self.body_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        language_label = (
            None if self.language_input is None else self.language_input.value
        )
        await self.controller.submit_custom_text(
            interaction,
            text_channel_id=self.text_channel_id,
            picker_message_id=self.picker_message_id,
            language=self.language,
            body=self.body_input.value,
            custom_language_label=language_label,
        )


class CorrectionModal(discord.ui.Modal):
    """Collect newline-delimited pronunciation corrections."""

    def __init__(
        self,
        controller: LecturaController,
        *,
        text_channel_id: int,
        reading_message_id: int,
        opener_interaction_id: int,
    ) -> None:
        super().__init__(
            title="Correcciones / Corrections",
            # Multiple listeners can correct the same reading concurrently.
            # The opener interaction makes each active modal route unique.
            custom_id=(
                f"correction_modal:{reading_message_id}:"
                f"{opener_interaction_id}"
            ),
        )
        self.controller = controller
        self.text_channel_id = text_channel_id
        self.reading_message_id = reading_message_id
        self.corrections_input = discord.ui.TextInput(
            style=discord.TextStyle.paragraph,
            custom_id="correction_lines",
            placeholder=(
                "Una palabra o frase por línea. / One word or phrase per line."
            ),
            min_length=1,
            max_length=1_000,
        )
        self.add_item(
            discord.ui.Label(
                text="Correcciones / Corrections",
                component=self.corrections_input,
            )
        )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.controller.submit_corrections(
            interaction,
            text_channel_id=self.text_channel_id,
            reading_message_id=self.reading_message_id,
            raw_items=self.corrections_input.value,
        )
