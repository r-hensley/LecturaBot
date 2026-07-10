# LecturaBot proof of concept

LecturaBot is a professional proof of concept for a bilingual Discord reading-session assistant. It manages isolated voice/text channel queues, reader turns, a small English-Spanish text catalog, user-supplied texts, pronunciation corrections, AFK skip votes, and reading-time statistics.

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
- Explicit two-participant start gate
- Beginner, Intermediate, and Advanced catalog choices in English and Spanish
- Custom-text modal, including a language label in the Other Languages channel
- Correction modal and reply-to-reading correction capture
- Case-insensitive, all-occurrence highlights that preserve the source casing
- Grouped correction attribution and exact-string correction counts
- Current-reader pass and non-current AFK skip voting
- Guild/user completed-turn totals and average reading time
- SQLite-backed catalog, statistics, and versioned active-session snapshots
- Startup reconciliation of voice membership and persisted Discord controls
- Per-room serialization across state changes and Discord message publication
- Strict TOML configuration with the Discord token accepted only from an environment variable

## POC decisions where the source behavior is unresolved

- The server guide confirms a **Start Reading** action, but it was absent from the captured queue component dump. This POC appends a provisional `Comenzar Lectura / Start Reading` button with `custom_id="start_reading"` while leaving all observed controls unchanged.
- `/lecturatest` maintains one canonical queue panel per channel pair and edits it after mutations instead of intentionally posting a new queue message every time. The planned final command names remain `/queue` and `/cola` after the original bot is retired.
- A session pauses when fewer than two queued voice participants remain. It must be started again after the second participant returns.
- Reading time runs from publication of the reading text until a normal current-reader pass. Selection time, skipped turns, and disconnected turns do not affect statistics.
- The configured skip-vote threshold defaults to two in code and is reduced to the number of eligible non-current queued readers.
- Correction counts de-duplicate exact trimmed strings. Attribution groups still retain each corrector's submitted entry.
- Native-language correction eligibility remains a community rule; the POC does not yet enforce language roles.
- Correctors do not need to enter the reader queue, but they must be present in the matching voice channel.
- Queue panels are capped at 25 participants. A reading stores at most 20 correction entries and 1,400 raw correction characters so Discord's message/embed limits cannot strand an active turn.
- Custom texts are turn-local and are not added to the reusable catalog.

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

Message Content Intent is required for reply-based corrections. The correction button and modal still work independently.

## Configuration

Copy the example and replace every placeholder Discord ID:

```bash
cp config.example.toml config.toml
```

`config.toml` is ignored by Git. The token is deliberately not accepted from TOML:

```bash
export LECTURABOT_TOKEN='your-token-here'
```

For quick slash-command updates during development, set `dev_guild_id` in `config.toml`. Without it, commands are synced globally and may take longer to appear.

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
  Small original seed catalog used only for the POC
```

## Production work deliberately deferred

- Native-language role enforcement
- Catalog administration and moderated imports
- Multi-process coordination beyond one bot process
- A fully asynchronous production database adapter; this POC uses short local `sqlite3` transactions behind an async repository interface
- Full audit/event tables instead of JSON session snapshots
- Notification-role management
- Google Docs integration
- Final copy and layout for interaction errors and modal fields
