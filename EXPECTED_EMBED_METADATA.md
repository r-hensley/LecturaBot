# LecturaBot Expected Embed Metadata and `discord.py` Templates

**Status:** Discovery draft 0.2
**Last updated:** 2026-07-11
**Target library:** `discord.py` 2.7.1, matching the configured local Python environment
**Companion document:** [Expected operation and required features](EXPECTED_OPERATION.md)

This document records the observable Discord message, embed, and component metadata exported from the reading channel. Sample user IDs, message IDs, timestamps, and session IDs are evidence values only; implementations must substitute live state or configuration. Sections labeled **Observed original-bot metadata** preserve the export contract; sections labeled **Clone enhancement** describe later requirements from live POC testing and must not be mistaken for captured original-bot evidence.

The channel dump contains 20 messages. Six bot messages contain all observed embeds and components. They establish three core UI families:

1. Queue/session panel, with active and empty variants
2. Text-selection prompt
3. Active reading message with correction summary

## 1. Global metadata conventions

All observed bot artifacts share these properties:

- Exactly one `rich` embed per bot message
- No embed color; in `discord.py`, leave `colour`/`color` unset
- No embed fields, URL, author block, thumbnail, image, or timestamp
- No attachments
- Components are buttons only; no select menus were captured
- No button has a URL or emoji
- Every captured button has `disabled=False`
- Bilingual labels put Spanish first on shared controls
- Discord mentions use `<@{user_id}>`
- Relative times use `<t:{unix_timestamp}:R>`

The exported `type: "rich"` and `flags: 0` values are Discord payload metadata and normally do not need to be passed to `discord.Embed`. Likewise, exported numeric component IDs such as `1`, `2`, and `3` are not the bot's routing contract. The stable application-facing values are the explicit string `custom_id` values.

### `discord.py` style mapping

| Raw style | Export name | `discord.py` value | Appearance |
| ---: | --- | --- | --- |
| `1` | `primary` | `discord.ButtonStyle.primary` | Blurple |
| `2` | `secondary` | `discord.ButtonStyle.secondary` | Gray |
| `3` | `success` | `discord.ButtonStyle.success` | Green |
| `4` | `danger` | `discord.ButtonStyle.danger` | Red |

Rows in the templates below are zero-indexed for the `row=` argument used by `discord.ui.Button`.

## 2. Complete observed button contract

Labels, ordering, capitalization, styles, and `custom_id` values in this table
are exact **observed original-bot metadata**. The clone-only additions are kept
in a separate table below so the evidence is not rewritten retroactively.

| View | Row | Order | Label | Style | `custom_id` |
| --- | ---: | ---: | --- | --- | --- |
| Queue | 0 | 1 | `Unirse / Enter` | Success | `enter_queue` |
| Queue | 0 | 2 | `Salir / Leave` | Danger | `leave_queue` |
| Queue | 0 | 3 | `Instrucciones / Instructions` | Secondary | `instructions` |
| Active reading | 0 | 1 | `Poner Correcciones / Submit Corrections` | Success | `submit_correction` |
| Active reading | 0 | 2 | `Pasar turno / Pass Turn` | Danger | `pass_reading` |
| Text picker | 0 | 1 | `Español Principiante` | Success | `find_reading0_0` |
| Text picker | 0 | 2 | `Español Intermedio` | Success | `find_reading0_1` |
| Text picker | 0 | 3 | `Español Avanzado` | Success | `find_reading0_2` |
| Text picker | 0 | 4 | `Tu propio texto / Your own text - Español` | Primary | `submit_reading0` |
| Text picker | 1 | 1 | `English Beginner` | Success | `find_reading1_0` |
| Text picker | 1 | 2 | `English Intermediate` | Success | `find_reading1_1` |
| Text picker | 1 | 3 | `English Advanced` | Success | `find_reading1_2` |
| Text picker | 1 | 4 | `Tu propio texto / Your own text - English` | Primary | `submit_reading1` |
| Text picker | 2 | 1 | `Pasar Turno / Pass Turn` | Danger | `pass_select` |

The picker IDs encode language and level:

```text
find_reading{language_index}_{level_index}

language_index:
  0 = Spanish
  1 = English

level_index:
  0 = Beginner / Principiante
  1 = Intermediate / Intermedio
  2 = Advanced / Avanzado

submit_reading{language_index}
```

`pass_select` and `pass_reading` are intentionally distinct routes for abandoning the selection phase and ending an active reading.

### 2.1 Clone-only component extensions

These controls are required by the clone but were not present in the captured
component export. **Pasar Turno / Pass Turn** remains in its observed location
and is authorized only for the current reader. AFK voting uses a visibly and
logically separate secondary button.

| View | Row | Order | Label | Style | `custom_id` | Requirement source |
| --- | ---: | ---: | --- | --- | --- | --- |
| Queue | 0 | 4 | `Comenzar Lectura / Start Reading` | Primary | `start_reading` | Server guide; provisional POC addition |
| Active reading | 0 | 3 | `Saltar turno ausente / Skip AFK Turn` | Secondary | `skip_afk_reading` | Live POC testing |
| Text picker | 2 | 2 | `Saltar turno ausente / Skip AFK Turn` | Secondary | `skip_afk_select` | Live POC testing |

The two AFK `custom_id` values are deliberately distinct because picker and
reading controls are separately registered persistent views. Both route to the
same domain action: record one vote from a queued, non-current reader. The
third unique vote advances the turn; the threshold remains three even when
fewer eligible voters are present. Duplicate votes, votes from the current
reader, and votes from people outside the queue are rejected.

## 3. Queue/session panel

### 3.1 Active queue metadata

| Property | Expected value |
| --- | --- |
| Message content | Empty string / no content |
| Embed title | `Sesión de Lectura / Reading Session \| Español-English` |
| Embed description | Queue heading, member rows, current-turn time, and support contacts |
| Embed footer | `id: {session_id}` |
| Embed color | Unset |
| View | Queue controls, one action row |

Description template:

```md
**-- Cola / Queue --**
{queue_member_rows}

Turno actual comenzó / Current turn started <t:{turn_started_unix}:R>
if bugs: ping <@{bug_contact_user_id}>
if text problem: ping <@{text_contact_user_id}>
```

Normal member row:

```md
<@{user_id}> | turns: *{turn_count_or_n/a}* | avg reading time: *{MM:SS_or_n/a}*
```

Current-reader row:

```md
__**--> <@{user_id}> <--** | turns: *{turn_count_or_n/a}* | avg reading time: *{MM:SS_or_n/a}*__
```

The current-reader row keeps the arrow and mention bold while underlining the entire row. The current reader is not necessarily the first displayed member.

### 3.2 Empty queue metadata

| Property | Expected value |
| --- | --- |
| Message content | Empty string / no content |
| Embed title | Same queue title |
| Embed description | Queue heading, `Vacío / Empty`, and support contacts |
| Embed footer | Absent |
| Current-turn line | Absent |
| View | Same queue controls |

Description template:

```md
**-- Cola / Queue --**
Vacío / Empty

if bugs: ping <@{bug_contact_user_id}>
if text problem: ping <@{text_contact_user_id}>
```

The dump contains several distinct queue-message IDs. It does not conclusively establish whether every state change creates a new message, whether `/queue` posts a fresh snapshot, or whether some queue messages are also edited in place.

### 3.3 Clone enhancement: numbered fresh queue panels

The clone rotates the *displayed* active queue into upcoming-turn order without
changing the durable join order. The current reader is always display position
`1`, the next reader is `2`, and numbering continues through the rotation.

Numbered current-reader row:

```md
__**1. --> <@{current_user_id}> <--** | turns: *{turn_count_or_n/a}* | avg reading time: *{MM:SS_or_n/a}*__
```

Numbered upcoming-reader row:

```md
**{upcoming_position}.** <@{user_id}> | turns: *{turn_count_or_n/a}* | avg reading time: *{MM:SS_or_n/a}*
```

The member statistics are mandatory on every active row. After a normal
completed reading, the completed-turn count and average are persisted before
rendering the next panel, so that new panel immediately shows the updated
values.

The clone publishes a fresh queue message when:

- the reading rotation starts;
- the current reader passes normally;
- three unique AFK votes force a skip; or
- any queued participant leaves voluntarily or is removed after leaving the
  paired voice channel.

If the current reader leaves, the same publication follows advancement to the
next reader when the session can continue, or reflects the paused state when it
cannot. After the new panel is successfully published and registered as the
active queue message, the superseded panel's view is retired so its controls
cannot mutate live state.

This numbered layout and publication lifecycle are **clone enhancements** from
live testing. They do not change the unnumbered original-bot evidence template
or prove how the original bot managed messages internally.

### 3.4 Observed `discord.py` queue embed builder

```python
from dataclasses import dataclass

import discord


QUEUE_TITLE = "Sesión de Lectura / Reading Session | Español-English"


@dataclass(frozen=True)
class QueueMemberView:
    user_id: int
    turns: int | None
    average_seconds: int | None
    is_current: bool = False


def format_turns(turns: int | None) -> str:
    return "n/a" if turns is None else str(turns)


def format_average(seconds: int | None) -> str:
    if seconds is None:
        return "n/a"
    minutes, remaining_seconds = divmod(seconds, 60)
    return f"{minutes:02d}:{remaining_seconds:02d}"


def format_queue_member(member: QueueMemberView) -> str:
    turns = format_turns(member.turns)
    average = format_average(member.average_seconds)
    if member.is_current:
        return (
            f"__**--> <@{member.user_id}> <--** | turns: *{turns}* "
            f"| avg reading time: *{average}*__"
        )
    return (
        f"<@{member.user_id}> | turns: *{turns}* "
        f"| avg reading time: *{average}*"
    )


def build_queue_embed(
    *,
    members: list[QueueMemberView],
    session_id: int | None,
    turn_started_unix: int | None,
    bug_contact_user_id: int,
    text_contact_user_id: int,
) -> discord.Embed:
    lines = ["**-- Cola / Queue --**"]

    if not members:
        lines.extend(
            [
                "Vacío / Empty",
                "",
                f"if bugs: ping <@{bug_contact_user_id}>",
                f"if text problem: ping <@{text_contact_user_id}>",
            ]
        )
        return discord.Embed(title=QUEUE_TITLE, description="\n".join(lines))

    lines.extend(format_queue_member(member) for member in members)
    lines.append("")
    if turn_started_unix is not None:
        lines.append(
            "Turno actual comenzó / Current turn started "
            f"<t:{turn_started_unix}:R>"
        )
    lines.extend(
        [
            f"if bugs: ping <@{bug_contact_user_id}>",
            f"if text problem: ping <@{text_contact_user_id}>",
        ]
    )

    embed = discord.Embed(title=QUEUE_TITLE, description="\n".join(lines))
    if session_id is not None:
        embed.set_footer(text=f"id: {session_id}")
    return embed
```

The domain layer should require a session ID and turn-start time for a normal active queue. They are optional in this rendering template only so incomplete state can be handled deliberately rather than crashing message construction.

## 4. Text-selection prompt

### 4.1 Message and embed metadata

| Property | Expected value |
| --- | --- |
| Message content | `<@{current_reader_id}>` |
| Embed title | `{reader_display_name} - Elige un texto / Pick a text to read` |
| Embed description | Fixed bilingual instructions below |
| Embed footer | Absent |
| Embed color | Unset |
| View | Three action rows containing nine buttons |

Exact description:

```text
Usa una opción abajo para elegir un texto. Si quieres practicar un texto en inglés, elige un botón con la etiqueta en inglés. / Use an option below to pick a text to read. If you want to practice an English text, you'd choose a button with the English label.
```

### 4.2 `discord.py` picker embed builder

```python
import discord


TEXT_PICKER_DESCRIPTION = (
    "Usa una opción abajo para elegir un texto. Si quieres practicar un texto "
    "en inglés, elige un botón con la etiqueta en inglés. / Use an option below "
    "to pick a text to read. If you want to practice an English text, you'd "
    "choose a button with the English label."
)


def build_text_picker_embed(reader_display_name: str) -> discord.Embed:
    return discord.Embed(
        title=f"{reader_display_name} - Elige un texto / Pick a text to read",
        description=TEXT_PICKER_DESCRIPTION,
    )


async def send_text_picker(
    destination: discord.abc.Messageable,
    *,
    current_reader_id: int,
    reader_display_name: str,
    view: discord.ui.View,
) -> None:
    await destination.send(
        content=f"<@{current_reader_id}>",
        embed=build_text_picker_embed(reader_display_name),
        view=view,
        allowed_mentions=discord.AllowedMentions(
            everyone=False,
            roles=False,
            users=True,
        ),
    )
```

The mention belongs in ordinary message content, not in the embed title. This ensures the selected reader can receive a Discord notification while the title remains readable.

## 5. Active reading and correction summary

The reading itself is ordinary message content. The correction summary is the embed attached to that same message.

### 5.1 Reading content metadata

English heading:

```md
## {reader_display_name} - Reading - English - Level {difficulty}
```

Spanish heading, established by the companion behavior evidence:

```md
## {reader_display_name} - Lectura - Español - Nivel {difficulty}
```

Full message-content template:

```md
## {localized_reading_heading}
{text_body_with_correction_highlights}
** **
```

The final `** **` is an observed blank bold spacer and should be retained for visual fidelity unless live testing shows it is unnecessary.

Matched source text is highlighted in place as:

```md
__**{matched_source_text}**__
```

Observed matching behavior:

- A correction for `abducted` highlighted every occurrence in the passage.
- A lowercase correction for `however` highlighted the capitalized source word `However`, indicating case-insensitive matching while preserving source casing.
- An unmatched or misspelled suggestion can remain in the correction summary without appearing as a source highlight.
- The dump does not prove fuzzy matching, accent normalization, whole-word rules, or how overlapping phrase matches are resolved.

### 5.2 Correction embed metadata

| Property | Expected value |
| --- | --- |
| Embed title | `Correcciones / Corrections : {correction_count}` |
| Embed description | Corrector groups separated by a blank line |
| Embed footer | Absent |
| Embed color | Unset |
| View | Active-reading controls, one action row |

Corrector-group template:

```md
<@{corrector_user_id}> suggests:
**{correction_item_1}**
**{correction_item_2}**

<@{next_corrector_user_id}> suggests:
**{correction_item_1}**
```

Each stored correction entry occupies one bold line. The correction count should be supplied by the domain logic so the renderer does not prematurely choose between total-submission and de-duplicated counting rules while that behavior remains under investigation.

### 5.3 Clone enhancement: discarded cross-corrector duplicates

The observed accepted-entry template remains bold. For the clone, when a
different corrector later submits the same normalized correction, preserve the
later attribution but render that entry with strikethrough only:

```md
<@{first_corrector_user_id}> suggests:
**{accepted_correction}**

<@{later_corrector_user_id}> suggests:
~~{discarded_duplicate}~~
```

Normalization for duplicate comparison is case-insensitive and collapses
surrounding or repeated whitespace. Store the discarded decision when the
correction is submitted so grouping entries by corrector cannot destroy the
chronological accepted/discarded decision. The correction title counts
normalized suggestions once, and only accepted entries contribute a distinct
highlight. Corrector-group rendering does not promise a single global display
order across groups.

This strikethrough treatment is a **clone enhancement**. The captured original
summary retained duplicate attribution but did not establish discarded-entry
markup.

### 5.4 Observed `discord.py` reading and correction builders

```python
from collections.abc import Iterable

import discord


READING_HEADING_TEMPLATES = {
    "es": "{reader} - Lectura - Español - Nivel {difficulty}",
    "en": "{reader} - Reading - English - Level {difficulty}",
}


def build_reading_content(
    *,
    reader_display_name: str,
    language_code: str,
    difficulty_label: str,
    highlighted_body: str,
) -> str:
    heading = READING_HEADING_TEMPLATES[language_code].format(
        reader=reader_display_name,
        difficulty=difficulty_label,
    )
    return f"## {heading}\n{highlighted_body}\n** **"


def build_corrections_embed(
    *,
    correction_count: int,
    groups: Iterable[tuple[int, list[str]]],
) -> discord.Embed:
    blocks: list[str] = []
    for corrector_user_id, items in groups:
        item_lines = "\n".join(f"**{item}**" for item in items)
        blocks.append(f"<@{corrector_user_id}> suggests:\n{item_lines}")

    return discord.Embed(
        title=f"Correcciones / Corrections : {correction_count}",
        description="\n\n".join(blocks),
    )
```

The original output appears to interpolate correction text directly into Markdown. Production code should decide explicitly whether and how to escape user-controlled Markdown before storing or rendering it.

## 6. Reusable `discord.py` view template

The base tuples in the following template create the exact captured component
layout while keeping callback routing outside the metadata definitions. The
`CLONE_*` tuples append the separately identified clone extensions.

```python
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

import discord


ButtonHandler = Callable[[discord.Interaction, str], Awaitable[None]]


@dataclass(frozen=True)
class ButtonSpec:
    label: str
    custom_id: str
    style: discord.ButtonStyle
    row: int


QUEUE_BUTTONS = (
    ButtonSpec("Unirse / Enter", "enter_queue", discord.ButtonStyle.success, 0),
    ButtonSpec("Salir / Leave", "leave_queue", discord.ButtonStyle.danger, 0),
    ButtonSpec(
        "Instrucciones / Instructions",
        "instructions",
        discord.ButtonStyle.secondary,
        0,
    ),
)


READING_BUTTONS = (
    ButtonSpec(
        "Poner Correcciones / Submit Corrections",
        "submit_correction",
        discord.ButtonStyle.success,
        0,
    ),
    ButtonSpec(
        "Pasar turno / Pass Turn",
        "pass_reading",
        discord.ButtonStyle.danger,
        0,
    ),
)


PICKER_BUTTONS = (
    ButtonSpec(
        "Español Principiante",
        "find_reading0_0",
        discord.ButtonStyle.success,
        0,
    ),
    ButtonSpec(
        "Español Intermedio",
        "find_reading0_1",
        discord.ButtonStyle.success,
        0,
    ),
    ButtonSpec(
        "Español Avanzado",
        "find_reading0_2",
        discord.ButtonStyle.success,
        0,
    ),
    ButtonSpec(
        "Tu propio texto / Your own text - Español",
        "submit_reading0",
        discord.ButtonStyle.primary,
        0,
    ),
    ButtonSpec(
        "English Beginner",
        "find_reading1_0",
        discord.ButtonStyle.success,
        1,
    ),
    ButtonSpec(
        "English Intermediate",
        "find_reading1_1",
        discord.ButtonStyle.success,
        1,
    ),
    ButtonSpec(
        "English Advanced",
        "find_reading1_2",
        discord.ButtonStyle.success,
        1,
    ),
    ButtonSpec(
        "Tu propio texto / Your own text - English",
        "submit_reading1",
        discord.ButtonStyle.primary,
        1,
    ),
    ButtonSpec(
        "Pasar Turno / Pass Turn",
        "pass_select",
        discord.ButtonStyle.danger,
        2,
    ),
)


# These additions are requirements for the clone, not captured metadata.
CLONE_QUEUE_BUTTONS = QUEUE_BUTTONS + (
    ButtonSpec(
        "Comenzar Lectura / Start Reading",
        "start_reading",
        discord.ButtonStyle.primary,
        0,
    ),
)

CLONE_READING_BUTTONS = READING_BUTTONS + (
    ButtonSpec(
        "Saltar turno ausente / Skip AFK Turn",
        "skip_afk_reading",
        discord.ButtonStyle.secondary,
        0,
    ),
)

CLONE_PICKER_BUTTONS = PICKER_BUTTONS + (
    ButtonSpec(
        "Saltar turno ausente / Skip AFK Turn",
        "skip_afk_select",
        discord.ButtonStyle.secondary,
        2,
    ),
)


class RoutedButton(discord.ui.Button["MetadataView"]):
    def __init__(self, spec: ButtonSpec, handler: ButtonHandler) -> None:
        super().__init__(
            label=spec.label,
            custom_id=spec.custom_id,
            style=spec.style,
            disabled=False,
            row=spec.row,
        )
        self._handler = handler

    async def callback(self, interaction: discord.Interaction) -> None:
        assert self.custom_id is not None
        await self._handler(interaction, self.custom_id)


class MetadataView(discord.ui.View):
    def __init__(
        self,
        specs: Sequence[ButtonSpec],
        handler: ButtonHandler,
    ) -> None:
        # Explicit custom IDs plus timeout=None make this view persistent.
        super().__init__(timeout=None)
        for spec in specs:
            self.add_item(RoutedButton(spec, handler))
```

Typical construction:

```python
queue_view = MetadataView(CLONE_QUEUE_BUTTONS, handle_queue_button)
reading_view = MetadataView(CLONE_READING_BUTTONS, handle_reading_button)
picker_view = MetadataView(CLONE_PICKER_BUTTONS, handle_picker_button)
```

If the controls must survive bot restarts, register fresh persistent view instances during startup with `bot.add_view(...)`. Persistence is an implementation recommendation based on the long-lived controls; the dump itself does not reveal the original view timeout or restart-registration behavior.

## 7. State and authorization expectations around rendering

The message metadata does not encode who may activate a button. Callbacks must check live session state before mutating it.

At minimum, routing should distinguish:

- Queue membership changes: `enter_queue`, `leave_queue`
- Instruction display: `instructions`
- Text lookup by language and difficulty: `find_reading{language}_{level}`
- Custom-text submission by language: `submit_reading{language}`
- Correction submission: `submit_correction`
- Passing before a text is selected: `pass_select`
- Passing an active reading: `pass_reading`
- AFK skip voting from the picker: `skip_afk_select`
- AFK skip voting from an active reading: `skip_afk_reading`

The clone must validate matching voice-channel membership, active-session and
message identity, and current-reader identity for picker and pass actions.
`pass_select` and `pass_reading` reject everyone except the current reader. The
AFK routes reject the current reader, people outside the queue, and repeat
voters; they advance only on the third unique eligible vote. Stale-message and
duplicate-interaction protection is required for every state-changing route.
These checks come from the behavioral workflow, not from the embed payload.

## 8. Metadata not present in this dump

Do not invent these details until later evidence is supplied:

- The response produced by `Instrucciones / Instructions`
- Modal title, fields, placeholders, lengths, and validation for custom texts
- Modal or interaction schema for correction submission
- Ephemeral versus public interaction responses
- Loading, success, rejection, and error messages
- Disabled-button states during or after processing
- Empty correction-summary behavior
- Whether completed reading controls are removed, disabled, or left active
- View timeout and restart behavior in the original bot
- Exact message edit-versus-new-message lifecycle in the original bot. The
  clone's fresh-panel lifecycle is specified in section 3.3.
- Permission and authorization failure copy

## 9. Implementation fidelity checklist

- [ ] Keep reading text in message content and corrections in the embed.
- [ ] Ping the current reader in picker message content.
- [ ] Leave all observed embeds colorless.
- [ ] Preserve every observed button's exact label, capitalization, style,
      row, and `custom_id`; append clone-only controls according to section 2.1.
- [ ] Do not route on exported numeric component IDs.
- [ ] Use Discord mention and relative-timestamp syntax.
- [ ] Omit the queue footer and turn-start line when the queue is empty.
- [ ] Use a queue footer of `id: {session_id}` for active sessions.
- [ ] Preserve bold accepted correction entries and blank lines between
      correctors; render later cross-corrector duplicates as `~~struck through~~`.
- [ ] Preserve original source casing when applying underline-plus-bold highlights.
- [ ] Number active rows in upcoming-turn order: current reader `1`, next
      reader `2`, then the remainder of the rotation.
- [ ] Show `turns` and `avg reading time` on every active member row and render
      completed-reading updates in the next fresh panel.
- [ ] Publish a fresh queue panel after every queue departure and turn change,
      then retire the superseded panel's controls.
- [ ] Allow only the current reader to pass; require three unique queued,
      non-current votes through the separate AFK control to force a skip.
- [ ] Validate message and embed lengths before sending catalog or custom text.
- [ ] Reject stale or unauthorized component interactions in callbacks.
