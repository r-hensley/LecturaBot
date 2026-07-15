from __future__ import annotations

import discord
import pytest

from lecturabot.models import (
    ActiveReading,
    CorrectionSource,
    Language,
    Level,
    MemberState,
    SessionPhase,
    SessionState,
)
from lecturabot.rendering import (
    PICKER_DESCRIPTION,
    QUEUE_TITLE,
    build_corrections_embed,
    build_instructions_embed,
    build_picker_embed,
    build_queue_embed,
    build_reading_content,
    highlight_body,
)
from lecturabot.views import (
    CorrectionModal,
    CustomTextModal,
    QueueView,
    ReadingView,
    TextPickerView,
)


def test_instructions_allow_a_single_participant_to_start() -> None:
    description = build_instructions_embed().description

    assert description is not None
    assert "puedes empezar solo" in description
    assert "you can start by yourself" in description


def test_active_and_empty_queue_embeds_match_metadata_contract() -> None:
    active = SessionState(
        session_id=11996,
        guild_id=1,
        text_channel_id=101,
        voice_channel_id=201,
        phase=SessionPhase.SELECTING,
        queue=[100, 200],
        members={
            100: MemberState(100, "New Reader"),
            200: MemberState(200, "Current", turns=4, total_seconds=1_248),
        },
        current_index=1,
        turn_started_at=1_720_000_000,
    )

    embed = build_queue_embed(
        active,
        bot_status_contact_user_id=900,
        issue_contact_user_id=901,
    )

    assert embed.title == QUEUE_TITLE
    assert embed.description == (
        "**-- Cola / Queue --**\n"
        "__**1. --> <@200> <--** | turns: *4* | avg reading time: *05:12*__\n"
        "**2.** <@100> | turns: *0* | avg reading time: *n/a*\n"
        "\n"
        "Turno actual comenzó / Current turn started <t:1720000000:R>\n"
        "Bot not working? → <@900>\n"
        "Found a bug or text issue? → <@901>"
    )
    assert embed.footer.text == "id: 11996"
    assert embed.colour is None
    assert not embed.fields

    empty = SessionState(12000, 1, 101, 201)
    empty_embed = build_queue_embed(
        empty,
        bot_status_contact_user_id=900,
        issue_contact_user_id=901,
    )
    assert empty_embed.title == QUEUE_TITLE
    assert empty_embed.description == (
        "**-- Cola / Queue --**\n"
        "Vacío / Empty\n"
        "\n"
        "Bot not working? → <@900>\n"
        "Found a bug or text issue? → <@901>"
    )
    assert empty_embed.footer.text is None


def test_waiting_queue_positions_follow_join_order() -> None:
    waiting = SessionState(
        session_id=12001,
        guild_id=1,
        text_channel_id=101,
        voice_channel_id=201,
        queue=[100, 200],
        members={
            100: MemberState(100, "First"),
            200: MemberState(200, "Second", turns=1, total_seconds=65),
        },
    )

    embed = build_queue_embed(
        waiting,
        bot_status_contact_user_id=900,
        issue_contact_user_id=901,
    )

    assert embed.description == (
        "**-- Cola / Queue --**\n"
        "**1.** <@100> | turns: *0* | avg reading time: *n/a*\n"
        "**2.** <@200> | turns: *1* | avg reading time: *01:05*\n"
        "\n"
        "Bot not working? → <@900>\n"
        "Found a bug or text issue? → <@901>"
    )


def test_picker_and_reading_render_exact_text_and_highlights() -> None:
    picker = build_picker_embed("María ✨")
    assert picker.title == "María ✨ - Elige un texto / Pick a text to read"
    assert picker.description == PICKER_DESCRIPTION
    assert picker.colour is None

    reading = ActiveReading(
        reader_id=10,
        reader_display_name="María",
        language=Language.ENGLISH,
        level=Level.INTERMEDIATE,
        body="However, she was abducted. ABDUCTED! Missing words stay plain.",
        started_at=100,
        expected_emotion="Realization",
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="Alex",
        items=["however", "abducted"],
        source=CorrectionSource.BUTTON,
    )
    reading.add_corrections(
        corrector_id=30,
        corrector_display_name="Sam",
        items=["abducted", "not present"],
        source=CorrectionSource.REPLY,
    )

    assert highlight_body(reading.body, reading.correction_texts) == (
        "__**However**__, she was __**abducted**__. __**ABDUCTED**__! "
        "Missing words stay plain."
    )
    assert build_reading_content(reading) == (
        "## María - Reading - English - Level Intermediate\n"
        "Expected Emotion: Realization\n"
        "__**However**__, she was __**abducted**__. __**ABDUCTED**__! "
        "Missing words stay plain.\n"
        "** **"
    )

    corrections = build_corrections_embed(reading)
    assert corrections.title == "Correcciones / Corrections : 3"
    assert corrections.description == (
        "<@20> suggests:\n"
        "**however**\n"
        "**abducted**\n"
        "\n"
        "<@30> suggests:\n"
        "~~abducted~~\n"
        "**not present**"
    )
    assert corrections.colour is None


def test_spanish_reading_localizes_expected_emotion_label() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Inés",
        language=Language.SPANISH,
        level=Level.INTERMEDIATE,
        body="Pudo recorrer el barrio sin problemas.",
        started_at=100,
        expected_emotion="Alivio",
    )

    assert build_reading_content(reading) == (
        "## Inés - Lectura - Español - Nivel Intermedio\n"
        "Emoción esperada: Alivio\n"
        "Pudo recorrer el barrio sin problemas.\n"
        "** **"
    )


def test_duplicate_correction_uses_strikethrough_without_bold() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.ENGLISH,
        level=Level.BEGINNER,
        body="New York",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="First",
        items=["New York"],
        source=CorrectionSource.BUTTON,
    )
    reading.add_corrections(
        corrector_id=30,
        corrector_display_name="Second",
        items=["new york"],
        source=CorrectionSource.REPLY,
    )

    embed = build_corrections_embed(reading)
    assert embed.title == "Correcciones / Corrections : 1"
    assert embed.description == (
        "<@20> suggests:\n"
        "**New York**\n\n"
        "<@30> suggests:\n"
        "~~new york~~"
    )
    assert "**~~new york~~**" not in embed.description


def test_duplicate_correction_does_not_strike_parenthetical_comment() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.ENGLISH,
        level=Level.BEGINNER,
        body="Houston",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="First",
        items=["Houston"],
        match_texts=["Houston"],
        source=CorrectionSource.BUTTON,
    )
    reading.add_corrections(
        corrector_id=30,
        corrector_display_name="Second",
        items=["Houston (not Ustin)"],
        match_texts=["Houston"],
        source=CorrectionSource.REPLY,
    )

    embed = build_corrections_embed(reading)
    assert embed.description == (
        "<@20> suggests:\n"
        "**Houston**\n\n"
        "<@30> suggests:\n"
        "~~Houston~~ (not Ustin)"
    )


def test_annotations_and_custom_emojis_are_preserved_while_targets_highlight() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.ENGLISH,
        level=Level.BEGINNER,
        body="We produce an apple.",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="Listener",
        items=[
            "produce (noun)",
            "(apple <:peepo_Pray:922638020035883058>)",
            "(venga venga, tú puedes! :whatCat:)",
        ],
        match_texts=["produce", "apple", None],
        source=CorrectionSource.BUTTON,
    )

    assert build_reading_content(reading) == (
        "## Reader - Reading - English - Level Beginner\n"
        "We __**produce**__ an __**apple**__.\n"
        "** **"
    )
    embed = build_corrections_embed(reading)
    assert embed.title == "Correcciones / Corrections : 3"
    assert embed.description == (
        "<@20> suggests:\n"
        "**produce** (noun)\n"
        "(apple <:peepo_Pray:922638020035883058>)\n"
        "(venga venga, tú puedes! :whatCat:)"
    )


def test_trailing_parenthetical_correction_annotation_is_not_bold() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.SPANISH,
        level=Level.BEGINNER,
        body="Biblioteca Sábado",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="Listener",
        items=["Biblioteca (estrés)", "Sábado (acento)"],
        match_texts=["Biblioteca", "Sábado"],
        source=CorrectionSource.BUTTON,
    )

    embed = build_corrections_embed(reading)
    assert embed.description == (
        "<@20> suggests:\n"
        "**Biblioteca** (estrés)\n"
        "**Sábado** (acento)"
    )


def test_correction_markdown_renders_but_mentions_remain_escaped() -> None:
    reading = ActiveReading(
        reader_id=10,
        reader_display_name="Reader",
        language=Language.SPANISH,
        level=Level.BEGINNER,
        body="Esperó con una mirada.",
        started_at=100,
    )
    reading.add_corrections(
        corrector_id=20,
        corrector_display_name="Listener",
        items=[
            "esper__ó__ (tilde)",
            "mira__d__a (**d**) @everyone <@123456789012345678>",
        ],
        match_texts=["Esperó", "mirada"],
        source=CorrectionSource.BUTTON,
    )

    embed = build_corrections_embed(reading)
    assert embed.description == (
        "<@20> suggests:\n"
        "**esper__ó__** (tilde)\n"
        "**mira__d__a (**d**) @\u200beveryone "
        "<@\u200b123456789012345678>**"
    )


def _button_contract(view: discord.ui.View) -> list[tuple[str, str, int, int]]:
    return [
        (str(item.label), str(item.custom_id), int(item.style), int(item.row or 0))
        for item in view.children
        if isinstance(item, discord.ui.Button)
    ]


@pytest.mark.asyncio
async def test_persistent_views_match_button_contract() -> None:
    controller = object()
    queue = QueueView(controller)  # type: ignore[arg-type]
    picker = TextPickerView(controller)  # type: ignore[arg-type]
    reading = ReadingView(controller)  # type: ignore[arg-type]

    assert queue.timeout is None
    assert _button_contract(queue) == [
        ("Unirse / Enter", "enter_queue", 3, 0),
        ("Salir / Leave", "leave_queue", 4, 0),
        ("Instrucciones / Instructions", "instructions", 2, 0),
        ("Comenzar Lectura / Start Reading", "start_reading", 1, 0),
    ]
    assert picker.timeout is None
    assert _button_contract(picker) == [
        ("Español Principiante", "find_reading0_0", 3, 0),
        ("Español Intermedio", "find_reading0_1", 3, 0),
        ("Español Avanzado", "find_reading0_2", 3, 0),
        (
            "Tu propio texto / Your own text - Español",
            "submit_reading0",
            1,
            0,
        ),
        ("English Beginner", "find_reading1_0", 3, 1),
        ("English Intermediate", "find_reading1_1", 3, 1),
        ("English Advanced", "find_reading1_2", 3, 1),
        (
            "Tu propio texto / Your own text - English",
            "submit_reading1",
            1,
            1,
        ),
        ("Pasar Turno / Pass Turn", "pass_select", 4, 2),
        (
            "Saltar turno ausente / Skip AFK Turn",
            "skip_afk_select",
            2,
            2,
        ),
    ]
    assert reading.timeout is None
    assert _button_contract(reading) == [
        ("Poner Correcciones / Submit Corrections", "submit_correction", 3, 0),
        ("Pasar turno / Pass Turn", "pass_reading", 4, 0),
        (
            "Saltar turno ausente / Skip AFK Turn",
            "skip_afk_reading",
            2,
            0,
        ),
    ]


@pytest.mark.asyncio
async def test_custom_text_modal_id_is_scoped_to_picker_message() -> None:
    controller = object()
    first = CustomTextModal(
        controller,  # type: ignore[arg-type]
        text_channel_id=101,
        picker_message_id=500,
        language=Language.ENGLISH,
        ask_language=False,
        max_text_length=1_600,
    )
    second = CustomTextModal(
        controller,  # type: ignore[arg-type]
        text_channel_id=102,
        picker_message_id=501,
        language=Language.ENGLISH,
        ask_language=False,
        max_text_length=1_600,
    )

    assert first.custom_id == "custom_text_modal:en:500"
    assert second.custom_id == "custom_text_modal:en:501"
    assert first.custom_id != second.custom_id


@pytest.mark.asyncio
async def test_correction_modal_id_is_scoped_to_opener_interaction() -> None:
    controller = object()
    first = CorrectionModal(
        controller,  # type: ignore[arg-type]
        text_channel_id=101,
        reading_message_id=700,
        opener_interaction_id=9_001,
    )
    second = CorrectionModal(
        controller,  # type: ignore[arg-type]
        text_channel_id=101,
        reading_message_id=700,
        opener_interaction_id=9_002,
    )

    assert first.custom_id == "correction_modal:700:9001"
    assert second.custom_id == "correction_modal:700:9002"
    assert first.custom_id != second.custom_id
