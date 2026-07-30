# LecturaBot

> **Original authors:** pip (Discord user ID `126922582208282624`) and
> yovax (Discord user ID `438520750362853376`).

LecturaBot is a bilingual Discord bot for managing group reading sessions in
English, Spanish, and other languages. It coordinates reader queues, presents
practice texts, collects pronunciation corrections, handles skipped turns, and
tracks reading statistics across multiple voice/text channel pairs.

## Features

- Independent reading sessions for multiple configured channel pairs
- Bilingual queue and reading controls
- Voice-channel validation and automatic queue cleanup
- Beginner, Intermediate, and Advanced text selection
- Offline catalog of 1,936 English and Spanish passages
- Custom texts for English, Spanish, and other languages
- Pronunciation corrections submitted through a modal or message reply
- Exact and conservative fuzzy highlighting of corrected words
- Grouped correction attribution and duplicate detection
- Automatic continuation messages when a correction summary becomes full
- Current-reader pass controls and three-vote AFK skipping
- Per-user completed-turn counts and average reading time
- Persistent sessions, statistics, controls, and catalog history in SQLite
- Restart recovery and reconciliation with current Discord voice membership

## Documentation

- [User guide / Guía de usuario](docs/user-guide.md) — instructions for
  participating in reading sessions
- [Privacy policy](PRIVACY.md) — what data LecturaBot stores and how to request
  its deletion

## Requirements

- Python 3.12 or newer
- A Discord application with a bot token
- The `bot` and `applications.commands` OAuth scopes
- The following channel permissions:
  - View Channel
  - Send Messages
  - Embed Links
  - Read Message History
  - Use Application Commands
- The following gateway intents enabled in the Discord Developer Portal:
  - Server Members Intent
  - Message Content Intent

Message Content Intent is required for corrections submitted as replies.
Corrections submitted through the button and modal do not depend on message
content access.

## Installation

From an activated Python 3.12+ virtual environment, install the project in
editable mode:

```bash
python -m pip install -e .
```

## Configuration

Copy the example configuration:

```bash
cp config.example.toml config.toml
```

Replace the placeholder Discord IDs in each `[[channel_pairs]]` entry.
Every text-channel ID and voice-channel ID must be unique.

The two support contacts are configured separately:

- `bot_status_contact_user_id` — shown for **Bot not working?**
- `issue_contact_user_id` — shown for **Found a bug or text issue?**

`config.toml` is ignored by Git. The Discord token is accepted only through the
environment:

```bash
export LECTURABOT_TOKEN='your-token-here'
```

To load a configuration file from another location:

```bash
export LECTURABOT_CONFIG=/absolute/path/to/config.toml
```

Set `dev_guild_id` in `config.toml` to synchronize application commands to one
development server immediately. If it is omitted, commands are synchronized
globally and may take longer to appear.

## Running the bot

Run from the repository checkout:

```bash
python -m lecturabot
```

The bot initializes its SQLite database, loads the packaged reading catalog,
restores active sessions, registers persistent Discord controls, and
synchronizes its application commands during startup.

## Reading catalog

LecturaBot uses a packaged catalog and does not contact Google Docs while
running.

- `src/lecturabot/data/catalog.json` is the generated runtime catalog.
- `sources/expanded_spreadsheet_readings.json` is a deterministic snapshot of
  the expanded community spreadsheet.
- `sources/legacy_catalog_2026-07-11.json` preserves complete passages that are
  missing or truncated in the spreadsheet.
- `sources/legacy_retired_readings_2026-07-20.json` prevents previously retired
  passages from being reintroduced.
- `sources/catalog_build_report.json` records source hashes, reconciliation
  counts, held-language inventory, and the generated catalog hash.

Equivalent and near-equivalent passages use the newer spreadsheet wording.
The build retains the fuller or corrected legacy wording only for explicitly
reviewed non-equivalent cases. French and Portuguese entries are preserved in
the source inventory but are not activated until the picker and level model
support them completely.

Verify that the generated catalog matches its source:

```bash
python scripts/build_catalog.py --check
```

At startup, the packaged catalog is authoritative: current passages are
inserted or re-enabled, and database rows absent from the catalog are disabled
but retained as history.

## Testing

Run the offline test suite:

```bash
python -m pytest
```

The tests cover configuration, session state transitions, persistence,
rendering, correction matching, and Discord component metadata without
connecting to Discord.

## Project structure

```text
src/lecturabot/
  bot.py          Discord bot, commands, and gateway listeners
  config.py       TOML and environment configuration
  controller.py   Discord interactions and authorization
  models.py       Session, queue, reading, and correction state
  rendering.py    Messages, embeds, and correction highlighting
  repository.py   SQLite persistence and catalog access
  service.py      Session state machine and domain rules
  views.py        Persistent controls and input modals

src/lecturabot/data/
  catalog.json

scripts/
  build_catalog.py
  extract_spreadsheet_catalog.py

sources/
  catalog_build_report.json
  expanded_spreadsheet_readings.json
  google_doc_readings.txt
  legacy_catalog_2026-07-11.json
  legacy_retired_readings_2026-07-20.json
```

## Operational notes

- Active queue panels and reading controls are persisted across restarts.
- Users who leave the associated voice channel are removed from the queue.
- A room session ends when its queue becomes empty.
- Custom texts apply only to the current turn and are not added to the catalog.
- Catalog passages do not repeat for the same reader during an active room
  session.
- Correction submissions allow up to 20 entries of 100 characters each. When
  the current correction summary fills, the bot freezes it and continues the
  same turn in a new active reading message without losing earlier corrections.
- SQLite is appropriate for the bot's current single-process deployment.
  Back up the database before schema or persistence changes.
