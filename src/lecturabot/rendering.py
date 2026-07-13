"""Pure Discord message and embed rendering for the observed UI contract."""

from __future__ import annotations

import re

import discord

from .corrections import correction_pattern
from .models import ActiveReading, Language, Level, SessionState


QUEUE_TITLE = "Sesión de Lectura / Reading Session | Español-English"
PICKER_DESCRIPTION = (
    "Usa una opción abajo para elegir un texto. Si quieres practicar un texto "
    "en inglés, elige un botón con la etiqueta en inglés. / Use an option below "
    "to pick a text to read. If you want to practice an English text, you'd "
    "choose a button with the English label."
)
NO_CORRECTIONS = "Aún no hay correcciones. / No corrections yet."
MAX_MESSAGE_CONTENT = 2_000
MAX_EMBED_DESCRIPTION = 4_096
_CUSTOM_EMOJI_TOKEN = re.compile(r"<a?:[A-Za-z0-9_]+:[0-9]+>")


class RenderError(ValueError):
    """Raised when rendered Discord content exceeds a platform limit."""


def format_average(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def build_queue_embed(
    state: SessionState,
    *,
    bot_status_contact_user_id: int,
    issue_contact_user_id: int,
) -> discord.Embed:
    lines = ["**-- Cola / Queue --**"]
    if not state.queue:
        lines.extend(
            [
                "Vacío / Empty",
                "",
                f"Bot not working? → <@{bot_status_contact_user_id}>",
                f"Found a bug or text issue? → <@{issue_contact_user_id}>",
            ]
        )
        return discord.Embed(title=QUEUE_TITLE, description="\n".join(lines))

    # Queue positions are turn-relative: 1 is the current reader, 2 is next,
    # and so on. The durable queue itself remains in join order, so rotating
    # this display does not change fairness or the state machine.
    display_queue = state.queue
    if state.current_index is not None:
        display_queue = (
            state.queue[state.current_index :] + state.queue[: state.current_index]
        )

    current_user_id = state.current_user_id
    for position, user_id in enumerate(display_queue, start=1):
        member = state.members[user_id]
        turns = "n/a" if member.turns == 0 else str(member.turns)
        average = format_average(member.average_seconds)
        if user_id == current_user_id:
            lines.append(
                f"__**{position}. --> <@{user_id}> <--** | turns: *{turns}* "
                f"| avg reading time: *{average}*__"
            )
        else:
            lines.append(
                f"**{position}.** <@{user_id}> | turns: *{turns}* "
                f"| avg reading time: *{average}*"
            )

    lines.append("")
    if state.turn_started_at is not None:
        lines.append(
            "Turno actual comenzó / Current turn started "
            f"<t:{state.turn_started_at}:R>"
        )
    lines.extend(
        [
            f"Bot not working? → <@{bot_status_contact_user_id}>",
            f"Found a bug or text issue? → <@{issue_contact_user_id}>",
        ]
    )
    description = "\n".join(lines)
    if len(description) > MAX_EMBED_DESCRIPTION:
        raise RenderError(
            f"queue summary is {len(description)} characters; "
            f"Discord allows {MAX_EMBED_DESCRIPTION}"
        )
    embed = discord.Embed(title=QUEUE_TITLE, description=description)
    embed.set_footer(text=f"id: {state.session_id}")
    return embed


def build_picker_embed(reader_display_name: str) -> discord.Embed:
    safe_name = discord.utils.escape_markdown(reader_display_name)
    return discord.Embed(
        title=f"{safe_name} - Elige un texto / Pick a text to read",
        description=PICKER_DESCRIPTION,
    )


def _escape_user_text(value: str) -> str:
    return discord.utils.escape_mentions(discord.utils.escape_markdown(value))


def _escape_correction_text(value: str) -> str:
    """Escape user markup while preserving valid Discord custom emoji tokens."""
    rendered: list[str] = []
    last_end = 0
    for match in _CUSTOM_EMOJI_TOKEN.finditer(value):
        rendered.append(_escape_user_text(value[last_end : match.start()]))
        rendered.append(match.group(0))
        last_end = match.end()
    rendered.append(_escape_user_text(value[last_end:]))
    return "".join(rendered)


def highlight_body(body: str, corrections: list[str]) -> str:
    """Highlight literal corrections longest-first without nesting markup.

    Rendering always starts from the immutable source body. Matches are
    case-insensitive, preserve source casing, and cover every occurrence.
    """
    unique: dict[str, str] = {}
    for value in corrections:
        trimmed = " ".join(value.split())
        if trimmed:
            unique.setdefault(trimmed.casefold(), trimmed)
    if not unique:
        return _escape_user_text(body)

    ordered = sorted(unique.values(), key=len, reverse=True)
    combined = "|".join(f"(?:{correction_pattern(item)})" for item in ordered)
    matcher = re.compile(combined, flags=re.IGNORECASE)

    rendered: list[str] = []
    last_end = 0
    for match in matcher.finditer(body):
        rendered.append(_escape_user_text(body[last_end : match.start()]))
        rendered.append(f"__**{_escape_user_text(match.group(0))}**__")
        last_end = match.end()
    rendered.append(_escape_user_text(body[last_end:]))
    return "".join(rendered)


def _reading_heading(reading: ActiveReading) -> str:
    reader = discord.utils.escape_markdown(reading.reader_display_name)
    if reading.custom_language_label:
        language = discord.utils.escape_markdown(reading.custom_language_label)
        return (
            f"{reader} - Lectura / Reading - {language} "
            "- Texto propio / Own text"
        )
    if reading.language is Language.SPANISH:
        if reading.level is None:
            return f"{reader} - Lectura - Español - Texto propio"
        levels = {
            Level.BEGINNER: "Principiante",
            Level.INTERMEDIATE: "Intermedio",
            Level.ADVANCED: "Avanzado",
        }
        return f"{reader} - Lectura - Español - Nivel {levels[reading.level]}"
    if reading.level is None:
        return f"{reader} - Reading - English - Own text"
    levels = {
        Level.BEGINNER: "Beginner",
        Level.INTERMEDIATE: "Intermediate",
        Level.ADVANCED: "Advanced",
    }
    return f"{reader} - Reading - English - Level {levels[reading.level]}"


def build_reading_content(reading: ActiveReading) -> str:
    """Render one safe reading post and enforce Discord's content limit."""
    lines = [f"## {_reading_heading(reading)}"]
    if reading.expected_emotion:
        lines.append(
            f"Expected Emotion: {_escape_user_text(reading.expected_emotion)}"
        )
    lines.extend(
        [
            highlight_body(reading.body, reading.correction_texts),
            "** **",
        ]
    )
    content = "\n".join(lines)
    if len(content) > MAX_MESSAGE_CONTENT:
        raise RenderError(
            f"reading content is {len(content)} characters; "
            f"Discord allows {MAX_MESSAGE_CONTENT}"
        )
    return content


def build_corrections_embed(reading: ActiveReading) -> discord.Embed:
    blocks: list[str] = []
    for group in reading.correction_groups:
        items = "\n".join(
            (
                f"~~{_escape_correction_text(entry.text)}~~"
                if entry.discarded
                else f"**{_escape_correction_text(entry.text)}**"
            )
            for entry in group.entries
        )
        blocks.append(f"<@{group.user_id}> suggests:\n{items}")
    description = "\n\n".join(blocks) if blocks else NO_CORRECTIONS
    if len(description) > MAX_EMBED_DESCRIPTION:
        raise RenderError(
            f"correction summary is {len(description)} characters; "
            f"Discord allows {MAX_EMBED_DESCRIPTION}"
        )
    return discord.Embed(
        title=f"Correcciones / Corrections : {reading.correction_count}",
        description=description,
    )


def build_instructions_embed() -> discord.Embed:
    return discord.Embed(
        title="Instrucciones / Instructions",
        description=(
            "1. Únete al canal de voz correspondiente y entra en la cola. / "
            "Join the matching voice channel and enter the queue.\n"
            "2. Se necesitan al menos dos personas; después pulsa "
            "**Comenzar Lectura / Start Reading**. / At least two people are "
            "required; then press **Start Reading**.\n"
            "3. Cuando sea tu turno, elige un texto o proporciona el tuyo. / "
            "When it is your turn, choose a text or provide your own.\n"
            "4. Los demás pueden enviar correcciones mientras escuchan. / "
            "Others can submit corrections while listening.\n"
            "5. Solo el lector actual puede pulsar **Pasar turno / Pass "
            "Turn** después de revisar las correcciones. / Only the current "
            "reader can pass after reviewing the corrections.\n"
            "6. Si el lector está ausente, tres participantes distintos en "
            "la cola pueden pulsar **Saltar turno ausente / Skip AFK Turn**. "
            "/ If the reader is AFK, three different queued participants can "
            "press **Skip AFK Turn**."
        ),
    )
