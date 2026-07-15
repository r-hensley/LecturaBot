# LecturaBot proof of concept

LecturaBot is a professional proof of concept for a bilingual Discord reading-session assistant. It manages isolated voice/text channel queues, reader turns, a bundled offline English-Spanish text catalog, user-supplied texts, pronunciation corrections, AFK skip votes, and reading-time statistics.

## Documentation

- [User guide / Guía de usuario](docs/user-guide.md) — instructions for people participating in reading sessions
- [Expected operation](EXPECTED_OPERATION.md) — behavioral requirements and evidence
- [Expected embed metadata](EXPECTED_EMBED_METADATA.md) — message and component templates for developers

The implementation follows the observed behavior in [EXPECTED_OPERATION.md](EXPECTED_OPERATION.md) and the exact captured embed/component contract in [EXPECTED_EMBED_METADATA.md](EXPECTED_EMBED_METADATA.md).

## What is implemented

- Temporary `/lecturatest` application command while the original bot remains active
- One independent session per configured voice/text channel pair
- Voice-channel validation and automatic removal after leaving voice
- Persistent, stale-safe queue, picker, and reading controls
- Single-participant sessions, with the same rotation flow used for groups
- Beginner, Intermediate, and Advanced catalog choices backed by 1,026 bundled English and Spanish passages
- Custom-text modal, including a language label in the Other Languages channel
- Correction modal and reply-to-reading correction capture, with newline or
  top-level-comma separated entries
- Exact-first, conservative fuzzy typo matching for case-insensitive,
  all-occurrence highlights that preserve the source casing
- Free-form correction annotations, including parenthesized sentences and
  custom emojis, remain listed even when no source highlight is found
- Grouped correction attribution, with later cross-corrector duplicates struck through
- Current-reader-only pass and a separate, fixed three-vote AFK skip action
- Per-guild/user completed-turn totals and average reading time, reset after six
  hours without a normally completed reading
- Numbered queue panels in upcoming-turn order, republished on request and for new reader turns
- SQLite-backed catalog, statistics, and versioned active-session snapshots
- Startup reconciliation of voice membership and persisted Discord controls
- Restart-safe, per-reader catalog no-repeat history for each room session
- Per-room serialization across state changes and Discord message publication
- Strict TOML configuration with the Discord token accepted only from an environment variable

## POC decisions where the source behavior is unresolved

- The server guide confirms a **Start Reading** action, but it was absent from the captured queue component dump. This POC appends a provisional `Comenzar Lectura / Start Reading` button with `custom_id="start_reading"` while leaving all observed controls unchanged.
- `/lecturatest` publishes a fresh public queue panel in the channel and maintains one active queue panel per channel pair. Starting or advancing to a reader publishes a fresh numbered panel so the current order and updated statistics remain visible; routine queue and voice departures update the active panel in place without resurfacing it. Superseded panels are retired. The planned final command names remain `/queue` and `/cola` after the original bot is retired.
- A session can start and continue with one queued voice participant. It ends only when the queue becomes empty.
- Reading time runs from publication of the reading text until a normal
  current-reader pass. That completion starts or extends only that user's
  six-hour statistics window. After six hours without another normal
  completion, `turns` returns to `0` and the average returns to `n/a` on the
  next queue refresh. Joining, leaving, selection-only passes, AFK skips, and
  other users' turns do not extend the window.
- **Pasar turno / Pass Turn** is available only to the current reader. A separate **Saltar turno ausente / Skip AFK Turn** action requires three unique votes from queued, non-current readers; the threshold is never reduced when fewer voters are available.
- Correction counts de-duplicate the normalized match target when one exists,
  otherwise the submitted text. Attribution groups retain each corrector's
  entry, but a later duplicate from another corrector is rendered as
  `~~struck through~~` to show that it was discarded.
- Correction submissions split at newlines and commas outside parentheses.
  Parentheses preserve one complete annotation even when it contains commas,
  a sentence, or a custom emoji; for example, `(stress :peepoPray:)` remains
  one displayed correction. Every parsed entry is listed whether or not it
  appears in the reading. Matching only controls highlighting: the bot tries an
  exact match first, then a conservative fuzzy match for likely typos, and
  leaves uncertain or unmatched comments unhighlighted. Emojis remain ordinary
  annotation text rather than aliases for words.
- Native-language correction eligibility remains a community rule; the POC does not yet enforce language roles.
- Correctors do not need to enter the reader queue, but they must be present in the matching voice channel.
- Queue panels are capped at 25 participants. A reading stores at most 20 correction entries and 1,400 raw correction characters so Discord's message/embed limits cannot strand an active turn.
- Custom texts are turn-local and are not added to the reusable catalog.
- Catalog no-repeat history is tracked independently for each reader during one
  room session. It survives a bot restart and a temporary leave/rejoin while
  the session remains active. An empty queue ends the session and resets that
  history. Exhausting a language/level is strict: the bot directs that reader
  to another level or a custom text instead of repeating a passage.

## Prerequisites

- The configured local Python environment:

  ```bash
  /mnt/c/Users/ryry0/Documents/Python/.venv/bin/python
  ```

- A Discord application and bot token
- Bot scopes: `bot` and `applications.commands`
- Channel permissions: View Channel, Send Messages, Embed Links, Read Message History, and Use Application Commands
- Enable these gateway intents in the Discord developer portal:
  - Server Members Intent
  - Message Content Intent

Message Content Intent is required for reply-based corrections. The correction
button and modal still work independently and can return private, ephemeral
errors for submission limits or an invalid reading state.

## Configuration

Copy the example and replace every placeholder Discord ID:

```bash
cp config.example.toml config.toml
```

`config.toml` is ignored by Git. The token is deliberately not accepted from TOML:

```bash
export LECTURABOT_TOKEN='your-token-here'
```

The text/voice channel pairs are read from `config.toml`. Admins can change the
`[[channel_pairs]]` entries there, provided each text and voice channel ID is
unique, then restart the bot to apply the change.

Queue panels use `bot_status_contact_user_id` for **Bot not working?** and
`issue_contact_user_id` for **Found a bug or text issue?**. Both contacts are
configured independently in `config.toml`.

For quick slash-command updates during development, set `dev_guild_id` in `config.toml`. Without it, commands are synced globally and may take longer to appear.

## Reading catalog

The bot does not contact Google Docs while running. At startup it idempotently
seeds SQLite from two packaged files, then disables entries listed in the
packaged retirement file:

- `data/readings.json`: the original 12 POC passages
- `data/google_doc_readings.json`: 1,014 passages generated from the committed
  snapshot of the community's **Texts for Sesión de Lectura** document
- `data/retired_readings.json`: passages intentionally excluded from selection,
  including copies already present in an existing database

The raw export is kept at `sources/google_doc_readings.txt`. Its original Easy,
Medium, Hard, Super Hard, and SFW Halloween categories are retained as metadata.
Because the Discord picker has three levels, Easy maps to Beginner, Medium maps
to Intermediate, and Hard, Super Hard, and Halloween map to Advanced.

To verify that the generated catalog still matches the snapshot:

```bash
/mnt/c/Users/ryry0/Documents/Python/.venv/bin/python \
  scripts/build_google_doc_catalog.py --check
```

## Run

Run directly from the checkout without modifying the shared virtual environment:

```bash
PYTHONPATH=src \
  /mnt/c/Users/ryry0/Documents/Python/.venv/bin/python -m lecturabot
```

To use a configuration path other than `config.toml`:

```bash
export LECTURABOT_CONFIG=/absolute/path/to/config.toml
```

## Test

```bash
PYTHONPATH=src \
  /mnt/c/Users/ryry0/Documents/Python/.venv/bin/python -m pytest
```

The test suite is offline: it exercises configuration, state transitions, persistence, rendering, highlighting, and component metadata without connecting to Discord.

## Source layout

```text
src/lecturabot/
  bot.py          Discord bot, slash commands, and gateway listeners
  config.py       Strict TOML and environment configuration
  controller.py   Discord interaction adapter and authorization
  models.py       Session, reading, correction, and queue state
  rendering.py    Exact embeds, reading content, and highlighting
  repository.py   SQLite schema, catalog, snapshots, and statistics
  service.py      Locked state machine and domain rules
  views.py        Persistent views and input modals

src/lecturabot/data/readings.json
  Original 12-passage POC seed catalog

src/lecturabot/data/google_doc_readings.json
  Generated 1,014-passage offline Google Doc catalog

src/lecturabot/data/retired_readings.json
  Catalog passages disabled on startup

sources/google_doc_readings.txt
  Vendored plain-text Google Doc export

scripts/build_google_doc_catalog.py
  Validated source-to-catalog generator
```

## Remaining TODO and decisions

- **Add lightweight SQLite maintenance.** Keep SQLite for this bot's expected
  scale, but establish a safe periodic backup procedure and add schema
  migrations whenever the stored format changes.
- **Continue UX polish from live testing.** Refine bilingual interaction copy,
  modal labels, validation feedback, and component layout when concrete user
  feedback identifies an improvement.
- **Restore the final command names.** After the original bot is retired,
  replace temporary `/lecturatest` with `/queue` and `/cola` and remove the
  test command during the same deployment.

The following are not planned requirements: native-language role enforcement,
multi-process coordination, replacing SQLite with a server database, full
audit/event history, or bot-managed notification-role subscriptions. These can
be reconsidered only if future usage demonstrates a concrete need.
