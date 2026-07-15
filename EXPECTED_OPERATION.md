# LecturaBot Expected Operation and Required Features

**Status:** Discovery draft 0.6
**Last updated:** 2026-07-13

This is a living specification assembled from user explanations, copied channel output, screenshots, and later clarifications. It describes expected behavior, not a chosen technical design. When the evidence is incomplete, the uncertainty is recorded rather than silently converted into a requirement.

The exact Discord embed, message-content, and button contract is documented separately in [Expected Embed Metadata and `discord.py` Templates](EXPECTED_EMBED_METADATA.md).

## Evidence labels

- **Confirmed:** Explicitly described in the supplied instructions or by the project owner.
- **Observed:** Visible in one or more channel examples, but the intended rule may still need confirmation.
- **Inferred:** Strongly suggested by the examples, but not yet demonstrated directly.
- **Open:** Not enough information yet.
- **Clone requirement:** Behavior requested for the authorized reimplementation
  after live POC testing. It may deliberately extend behavior that was absent
  or inconclusive in the original-bot evidence.

## 1. Purpose

LecturaBot supports an English-Spanish language-exchange server's group reading sessions.

During a session:

1. Learners take turns reading a text aloud in a voice channel.
2. The bot maintains the reader queue and identifies the current reader.
3. The current reader chooses a text at an appropriate language and difficulty, or submits a custom text.
4. Native speakers listen and submit pronunciation corrections.
5. The reader reviews the corrections and explicitly ends their turn.
6. The bot advances the queue to the next reader.

The same session may contain English learners and Spanish learners. A participant can be a learner for one language and a native corrector for the other.

The six normal reading-channel pairs are limited by server policy to spoken and written English or Spanish. A separate **Other Languages** voice/text pair supports other target languages through the bot's custom-text option.

## 2. Participant roles

- **Current reader:** The queued participant whose turn is active.
- **Waiting reader:** A queued participant waiting for a future turn. Waiting readers may also submit corrections.
- **Corrector:** A participant listening to the current reader and submitting corrections in the corrector's native language.
- **Session participant:** Anyone represented in the active reading queue.

The supplied instructions ask people to correct only readers who are reading in the corrector's native language. Whether the bot enforces this or it is only a community rule is still open.

## 3. Expected session flow

1. A user joins one of the six standard reading voice channels, or the separate **Other Languages** voice channel.
2. The user opens that voice channel's corresponding text channel. The voice and text channel numbers must match.
3. The user types `/queue` in English or `/cola` in Spanish to interact with the bot.
4. The user enters the queue through **Unirse / Enter** and remains in the associated voice channel.
5. At least two people must be present before a reading session can start. The guide recommends mentioning the self-assignable `@Sesión de Lectura` role to find another participant.
6. When the minimum is met and participants are ready, they use **Start Reading** / **Comenzar Lectura**.
7. A queued user waits for their turn and may submit corrections for the current reader.
8. When the user's turn begins, the bot mentions them and presents text-selection options.
9. The reader chooses a stored text level, supplies their own text, or reads from another source. Bot-assisted use in **Other Languages** requires the custom-text choice.
10. The bot publishes the selected text and associates subsequent corrections with it when the text is supplied to the bot.
11. The reader reads the text aloud in the voice channel.
12. Native speakers submit corrections with **Add Corrections** or by replying directly to the reading post. Reply-associated corrections can be reflected or highlighted in that text's bot output.
13. The reader goes through all corrections.
14. The reader presses the red **Pass Turn** button to finish.
15. The bot records the completed turn, updates timing statistics, and activates the next eligible reader.
16. The bot publishes a fresh, numbered queue panel for the new turn so the
    rotation and updated statistics remain visible.

If nobody remains, the queue displays **Vacío / Empty** and that room session
ends. A later entrant begins a new session with reset session-scoped catalog
history.

The POC temporarily exposes this workflow through `/lecturatest` so it can run
beside the original bot without colliding with `/queue` or `/cola`. The command
publishes a fresh public queue panel in the invoking text channel rather than
returning an ephemeral link. The latter remain the planned final command names
after the original bot is retired.

## 4. Queue and voice-channel behavior

### Confirmed requirements

- The bot interaction is invoked with `/queue` or its Spanish equivalent `/cola` in the text channel corresponding to the user's reading voice channel.
- The server provides six numbered English-Spanish reading voice channels, each paired with one numbered text channel.
- At least two people are required before reading can start.
- Participants use **Start Reading** / **Comenzar Lectura** once they are ready.
- Users can enter the queue with a button.
- Users can leave the queue.
- Leaving loses the user's position; rejoining does not restore the old position.
- Leaving the associated voice channel also removes the user from the queue and loses their position.
- The current reader is mentioned when their turn begins.
- The current reader manually ends a normal turn with **Pass Turn**.
- Other participants can vote to skip a reader who is absent or AFK.
- Only a small number of votes is normally needed to skip, but the exact threshold is not known.

### Observed queue display

The queue status is bilingual and follows this general shape:

```text
Sesión de Lectura / Reading Session | Español-English
-- Cola / Queue --
@Reader A | turns: 7 | avg reading time: 06:39
--> @Reader B <-- | turns: 4 | avg reading time: 05:12
@Reader C | turns: n/a | avg reading time: n/a

Turno actual comenzó / Current turn started 4 hours ago
if bugs: ping @ConfiguredBugContact
if text problem: ping @ConfiguredTextContact
id: 11996
```

Observed behavior includes:

- The active reader is wrapped in `-->` and `<--` markers.
- Queue order remains displayed as a list while the active marker moves between readers.
- Readers can disappear from and later re-enter the queue.
- First-time readers display `n/a` for both completed turns and average reading time.
- A stable numeric ID is displayed throughout the sampled session.
- The status includes separately configurable contacts for bot bugs and text problems.

### Clone requirements from live POC testing

- **Testing support contacts:** The clone replaces the original support copy
  with two explicit routes: `Bot not working?` pings the configured bot-status
  contact, while `Found a bug or text issue?` pings the configured issue-review
  contact. These contacts remain independently configurable.
- **Pass Turn authorization:** **Pasar turno / Pass Turn** is a current-reader
  action only. A waiting reader must not be allowed to advance the turn with
  that control.
- **Separate AFK voting:** Text-picker and active-reading messages provide a
  separate bilingual **Saltar turno ausente / Skip AFK Turn** action. Only a
  queued participant other than the current reader may vote. Three unique
  votes are required, duplicate votes do not count, and the threshold is never
  reduced because fewer eligible voters are present.
- **Queue departures:** Every voluntary queue departure or automatic removal
  after leaving the paired voice channel publishes a fresh queue panel. If the
  departing participant is the current reader, the turn advances to the next
  reader when the minimum-participant rule permits; otherwise the session
  pauses in its documented waiting state.
- **Turn transitions:** Starting the rotation, a normal current-reader pass,
  and a successful AFK skip all publish a fresh queue panel for the new turn.
  The superseded panel must no longer accept state-changing interactions.
- **Upcoming-turn positions:** An active queue is displayed in rotation order,
  beginning with the current reader as position `1`; position `2` is the next
  reader, followed by the remaining upcoming readers. This numbering is a
  clone enhancement and was not present in the captured original-bot panel.
- **Visible statistics:** Every numbered member row includes `turns` and
  `avg reading time`. A normal completed reading updates those values before
  the next turn's fresh queue panel is published. Each participant has an
  independent six-hour inactivity window for those statistics; it is separate
  from the room-session lifetime described below.
- **Room-session lifetime:** A room session remains active while at least one
  participant remains queued, including while it is paused below the
  two-participant minimum. Its state survives a bot restart. A temporary
  leave/rejoin does not erase that user's session-scoped catalog history unless
  the departure makes the queue empty. An empty queue ends and resets the room
  session.

### Original-bot queue behavior still to confirm

- Whether the rotation is strictly FIFO, round-robin, or has exceptions.
- Where a newly joined or rejoined user is inserted.
- Whether the bot technically rejects `/queue`, `/cola`, or **Unirse / Enter** when the user is not in the matching voice channel; the documented user flow requires joining voice first.
- What the original bot did when the current reader voluntarily left the queue
  or voice channel. The clone behavior is specified above.
- Whether the original status was one edited message or a new status message
  on every transition. The clone must publish fresh panels as specified above.
- Whether the original `/queue` created a new session, retrieved a persistent
  channel-specific session, or only posted controls for existing state. The
  clone's room-session lifetime is specified above.
- How the original bot closed or expired an abandoned or empty session. The
  clone ends its room session as soon as the queue becomes empty.
- Whether all six channel pairs can run sessions simultaneously. The channel layout implies that their state must at least be isolated from one another.
- What happens if **Start Reading** is pressed with fewer than two eligible participants.
- Whether every participant must press **Start Reading**, only one participant starts the session, or the control means something turn-specific.
- The original bot's exact vote threshold and whether it changed with queue
  size. The clone uses a fixed threshold of three unique votes.
- The original bot's voter eligibility and vote-reset rules. The clone's voter
  eligibility is specified above; clone votes reset with the turn.
- The original control name for skipping another reader. The Spanish
  instructions refer to **Pasar turno**, while the English instructions refer
  to **Skip Turn**; the clone uses the distinct bilingual label **Saltar turno
  ausente / Skip AFK Turn**.

## 5. Text selection and delivery

### Confirmed requirements

- The bot maintains a database or catalog of reading texts.
- A learner can select the text difficulty.
- The bot supports both English and Spanish reading practice.
- The current reader can submit a custom text instead of selecting a stored one.
- **Your Own Text** / **Mi propio texto** is the required bot option when reading a language other than English or Spanish.
- A reader may bring a text from any outside source.
- The community guide links a shared Google document as an additional place to find texts: [Reading-text resource](https://docs.google.com/document/d/1O2KZYIn1S5xcWHAOvSo3bN2Wx-f-D1qKd9mMW6U5DhM/edit).
- Participants use **Start Reading** / **Comenzar Lectura** before choosing a text level.
- The text picker is only offered when it is the reader's turn.

### Observed selection prompt

```text
@Reader
Reader - Elige un texto / Pick a text to read
Usa una opción abajo para elegir un texto. Si quieres practicar un texto en
inglés, elige un botón con la etiqueta en inglés. / Use an option below to pick
a text to read. If you want to practice an English text, you'd choose a button
with the English label.
```

The prompt has button options, but their complete labels and layout were not present in the copied text.

### Observed reading-post formats

English:

```text
Reader - Reading - English - Level Intermediate
Expected Emotion: Realization
[reading text]
```

Spanish:

```text
Reader - Lectura - Español - Nivel Principiante
[reading text]
```

Observed content behavior includes:

- Three catalog difficulty buttons per language: **Beginner / Principiante**, **Intermediate / Intermedio**, and **Advanced / Avanzado**.
- A language-localized heading for the selected reading.
- Some texts include optional performance metadata such as `Expected Emotion: Anger` or `Expected Emotion: Realization`.
- Other texts omit expected-emotion metadata.

### Shared text document

The linked **Texts for Sesión de Lectura** document is an external bilingual reading library with these sections:

| Language | Categories in the document |
| --- | --- |
| English | Easy, Medium, Hard, Super Hard, SFW Halloween |
| Spanish | Fácil, Intermedio, Difícil, Super Difícil, SFW Halloween |

Several Batch 1 bot readings also occur in the document. The clone vendors a
plain-text snapshot and generates 1,014 validated catalog records from it. The
running bot uses only the generated local JSON and never depends on Google Docs
availability.

| Document category | Clone catalog level |
| --- | --- |
| Easy / Fácil | Beginner / Principiante |
| Medium / Intermedio | Intermediate / Intermedio |
| Hard / Difícil | Advanced / Avanzado |
| Super Hard / Super Difícil | Advanced / Avanzado |
| SFW Halloween | Advanced / Avanzado |

The original 12 POC passages remain a separate seed, giving the packaged catalog
1,026 total passages. Startup inserts both seed files idempotently into SQLite,
then disables catalog retirements so removals also apply to existing databases.

### Clone requirements from live POC testing

- Catalog repeat prevention is per reader, not global. One reader receiving a
  passage does not prevent another reader from receiving it.
- Within one room session, a reader must not receive the same catalog passage
  more than once. This history applies across languages and levels by catalog
  text identity and survives bot restarts and a temporary leave/rejoin.
- Pausing below two participants does not reset the history. The history resets
  only when the room queue becomes empty and the session ends.
- Exhaustion is strict. If that reader has used every passage in the selected
  language/level during the session, the bot must not clear or cycle the used
  set; it directs the reader to another level or a custom text instead.

### Text behavior still to confirm

- Whether language and difficulty are selected together or in separate steps.
- Modal contents, interaction responses, validation, and timeout behavior after a selection button is pressed.
- The exact relationship between **Start Reading** and the current-reader text picker.
- How a custom text is entered, validated, displayed, and moderated.
- Whether “read from any other source” means the text must be pasted through **Your Own Text**, or whether the reader may read off-platform without creating a bot reading post.
- Maximum and minimum text length.
- Whether custom texts become reusable catalog entries.
- How catalog texts are chosen within a level: random, sequential, weighted, or user-selected.
- Whether the original bot excluded recently used texts. The clone's
  session-scoped, per-reader rule is specified above.
- What metadata is stored for a catalog text beyond language, level, body, and optional expected emotion.
- How users report an unsuitable or broken text.

## 6. Corrections

### Confirmed requirements

- Participants may submit corrections while waiting for their own turn.
- Corrections should only be supplied by someone native in the language being read.
- Corrections have two documented submission paths: **Add Corrections** and a direct reply to the reading-text message.
- A participant can reply to the reading message so their corrections are shown or highlighted on the selected text.
- The reader is expected to review all corrections before passing the turn.

The supplied Spanish guide misspells the control as **Poner Correciones**. The exported live button reads **Poner Correcciones / Submit Corrections**; the live button is the expected implementation copy.

### Observed correction summary

Bot reading posts can include a bilingual correction section:

```text
Correcciones / Corrections : 8
@Corrector A suggests:
realization
stadium
interesting

@Corrector B suggests:
fifty thousand
filled
```

Observed behavior includes:

- Corrections are attributed to the submitting user.
- A user may submit multiple correction entries.
- An entry can be a word, a multiword phrase, or a notation containing alternatives such as `walk / walked`.
- The summary preserves each corrector's own entries, including an entry also suggested by another person.
- The displayed total appears to count unique correction strings across correctors. Identical entries such as `serious` contribute once to the total even when shown under two correctors. Small textual differences, including spelling or punctuation, appear capable of producing separate entries. This counting rule is inferred from the examples and needs direct confirmation.
- Free-form pronunciation explanations and phonetic approximations also appear as ordinary human chat messages after the bot summary. There is no evidence yet that the bot parses or stores those follow-up messages.

### Clone requirements from live POC testing

#### Duplicate corrections

The first normalized occurrence of a correction remains accepted. If a
different corrector later submits the same normalized word or phrase, the
later entry remains visible under that corrector for attribution and review,
but is marked discarded with Discord strikethrough syntax:

```md
<@{later_corrector_user_id}> suggests:
~~{duplicate_correction}~~
```

The discarded duplicate does not add another unique correction to the displayed
count and does not create an additional text highlight. This strikethrough is a
clone enhancement requested after live testing, not metadata observed in the
original channel export.

#### Parsing, matching, and validation

- Both correction paths accept entries separated by a newline or by a
  top-level comma. Empty entries, including one created by a trailing comma,
  are ignored. A comma inside parentheses remains part of its entry.
- A direct reply to the active reading post is parsed and submitted as
  corrections for that reading; it is not merely ordinary conversation.
- Parentheses preserve one complete correction item, even when the contents
  include commas, a full explanatory sentence, or a custom emoji. For example,
  `(stress :peepoPray:)` is stored and displayed as one correction. The emoji
  is annotation text, not an abbreviation or word alias.
- A trailing parenthetical label also remains in the displayed correction. For
  example, `produce (noun)` stays exactly as submitted while `produce` can be
  matched and highlighted in the reading.
- Every parsed correction item is stored and rendered even if it does not occur
  in the reading. Match success controls highlighting only; it does not control
  acceptance of the item or of the rest of its submitted batch.
- Highlight matching tries an exact, case-insensitive source match first. If
  that fails, it may use a conservative fuzzy match for a likely typo. A fuzzy
  result must be sufficiently strong and unambiguous; otherwise the correction
  remains listed without any source highlight.
- Existing per-entry, per-reading character/count limits and normalized
  duplicate handling remain in force independently of source matching.

### Original-bot correction behavior still to confirm

- The original modal's fields, limits, validation, and response copy. The clone
  input, limits, and highlight-matching behavior are specified above.
- How the original bot parsed reply text and handled entries it could not
  highlight.
- Whether original matching was accent-sensitive, fuzzy, or token-based.
- How the original handled overlapping phrases or competing match targets.
- Whether a corrector can edit or delete a submitted correction.
- When correction submission closes.
- Whether the reader can pass before reviewing every correction or the instruction is advisory.
- Whether native-language eligibility is enforced through server roles, user settings, or not enforced technically.
- Whether correction history is retained after a session ends.

## 7. Turns and timing statistics

### Observed behavior

- Each participant has a completed-turn count.
- Each participant has an average reading time formatted as `MM:SS`.
- Both fields show `n/a` until the participant completes a turn.
- When a reader finishes, their turn count increases and their average changes.
- The status includes a relative `Current turn started` time.

### Clone requirements from live POC testing

- Completed-turn totals and average reading time are always included on active
  numbered queue rows. Users without an active completed-reading statistics
  window show `turns: 0` and `avg reading time: n/a`.
- A normally completed reading is recorded before the next turn's fresh queue
  panel is rendered, so the just-finished reader's `turns` and
  `avg reading time` values are immediately visible there.
- Statistics are keyed independently per server and user. A normal completion
  starts or extends that user's window for 21,600 seconds from the completion.
  The window remains active while less than six hours have elapsed since the
  last normal completion; at exactly six hours it is expired.
- Expiration resets both the completed-turn count and accumulated reading time,
  so the next queue refresh renders `turns: 0` and `avg reading time: n/a`. The
  user's next normal completion starts a fresh window at `turns: 1`, with its
  duration as the new average.
- AFK skips, voice/queue departures, and turns passed before a reading is
  published do not count as completed readings and do not extend the window.
  Joining, starting a queue, and other participants' completions also do not
  extend it. Different users therefore expire independently.
- Statistics are reconciled when the queue is next accessed or refreshed. An
  already-posted Discord panel does not edit itself at the exact expiry time.
- Legacy lifetime totals without a trustworthy last-completion timestamp start
  fresh when this behavior is deployed.
- Reading time begins when the selected passage is published and ends when the
  current reader normally passes the turn. Selection time, AFK skips,
  departures, and unpublished turns do not contribute.

### Original-bot timing behavior still to confirm

- Follow-up feedback says the original bot's completed-turn count operated
  "day by day," but its exact calendar boundary, timezone, and whether the
  average reset with the count remain unknown. The clone uses the independent
  six-hour inactivity windows specified above.
- Rounding rules and behavior for readings longer than 59 minutes.
- Whether the relative start time in the sample is intended behavior. It remained several hours old across multiple reader changes, despite being labeled as the current turn's start, so it may represent a session start or an existing bot defect.

## 8. Localization and presentation

- Core workflow output is bilingual Spanish-English.
- `/queue` and `/cola` are the documented English and Spanish command forms.
- Queue headings, empty state, instructions, and correction headings are bilingual.
- Reading headings are localized to the language being practiced.
- Important buttons have localized labels, including **Start Reading** / **Comenzar Lectura**, **Pass Turn** / **Pasar Turno**, **Saltar turno ausente / Skip AFK Turn**, and **Your Own Text** / **Mi propio texto**.
- The pass-turn control is described as red.
- User mentions must use Discord mentions so readers receive a notification.
- User display names may contain spaces, Unicode characters, and decorative characters.
- Text content must preserve Spanish accents, typographic apostrophes, punctuation, and paragraph formatting.
- Bug-report and text-problem contacts should be configurable rather than hard-coded to the sampled accounts.

It is still open whether Discord command localization automatically chooses the command and button language, whether separate localized control messages are posted, or whether all core session output is always bilingual.

## 9. Server integration and channel topology

### Standard English-Spanish channel pairs

| Voice channel | Corresponding text channel |
| --- | --- |
| `📚￤Lectura 1` | `📚・lectura-text-1` |
| `📚￤Lectura 2` | `📚・lectura-text-2` |
| `📚￤Lectura 3` | `📚・lectura-text-3` |
| `📚￤Lectura 4` | `📚・lectura-text-4` |
| `📚￤Lectura 5` | `📚・lectura-text-5` |
| `📚￤Lectura 6` | `📚・lectura-text-6` |

The user must invoke and use the bot in the text channel paired with their current voice channel. Each pair therefore needs isolated queue, turn, reading, correction, and timing state.

### Other languages

- Other languages are restricted to `📙￤Other Languages` and its corresponding `📙・lectura-other-lang` text channel.
- In this channel, the bot can still manage the session, but the reader must select **Your Own Text** / **Mi propio texto** rather than use an English-Spanish catalog text.

### Community notification role and language policy

- `@Sesión de Lectura` is an optional, self-assignable role available through Discord's **Channels & Roles** interface.
- Participants are encouraged to mention this role and ask for a native speaker before starting, because at least two people are required.
- Only English and Spanish speech and messages are allowed in the six standard reading-channel pairs.
- The language restriction and role assignment appear to be server policy and Discord configuration, not necessarily behavior that this bot must enforce or manage.

## 10. Provisional data the behavior requires

This is a behavioral inventory, not a database-schema decision.

### Reading session

- Server association
- Configured voice/text channel pair
- Session language mode: standard English-Spanish or other-language custom text
- Session ID
- Minimum participant count
- Queue membership and order
- Upcoming-turn position, rendered with the current reader as `1`
- Current reader
- Current-turn state and timestamps
- Skip votes
- Session lifecycle state, including empty-queue termination
- Configured support contacts
- Optional reading-session notification role

### Participant state

- Discord user identity
- Queue position
- Catalog text identities already used by this reader in the current room
  session
- Completed-turn count in the participant's independent six-hour statistics
  window
- Accumulated and average turn time in that same window
- Possibly native and learning languages

### Reading text

- Language
- Difficulty
- Text body
- Optional expected emotion
- Catalog text versus user-supplied text
- Target language for a custom text
- Source category and seasonal classification, where applicable
- Enabled/disabled state and use history

### Active reading and corrections

- Reader and selected text
- Reading-post identity
- Turn timestamps and outcome
- Correction text
- Display text and optional derived match target; a missing target means the
  entry remains listed without a source highlight
- Corrector identity
- Submission order
- Accepted or discarded-duplicate status
- Link to the active reading
- Submission path: correction button or message reply
- Highlight or match information, if needed

## 11. Required feature inventory

The reimplementation is presently expected to need:

- A bilingual Discord reading-session interface.
- `/queue` and `/cola` slash-command entry points.
- Configurable, isolated voice/text channel pairs, including the six documented standard pairs and the separate other-language pair.
- A queue tied to matching voice-channel presence.
- A two-participant minimum and **Start Reading** action.
- Enter and leave actions, a red current-reader-only pass action, and a
  separate bilingual AFK-skip action requiring three unique queued,
  non-current votes.
- Automatic removal when a queued user leaves voice.
- Current-reader mentions and fresh queue panels after departures and turn
  changes.
- Numbered active queues in upcoming-turn order, with the current reader at
  position `1` and the next reader at position `2`.
- A Spanish-English text catalog with difficulty selection.
- Per-reader catalog no-repeat history lasting for one room session, persisted
  across restart and temporary leave/rejoin, with strict exhaustion rather than
  automatic recycling.
- A current-reader text picker.
- A custom-text submission path, including for languages outside English and Spanish.
- Reading-post rendering with optional metadata.
- **Add Corrections** and reply-associated correction capture, accepting
  newline and top-level-comma separated entries.
- Correction attribution, aggregation, counting, text highlighting, and
  strikethrough rendering of later cross-corrector duplicates, including
  free-form parenthesized annotations with sentences, commas, and custom emojis.
- Acceptance and display of every parsed correction item regardless of source
  matching, with exact-first, conservative fuzzy typo matching used only to
  choose source highlights.
- Visible completed-turn and average-time tracking in independent per-user
  six-hour inactivity windows, updated before the next turn panel is published.
- An empty-queue state.
- Configurable bot-bug and content-problem contacts.
- Safe handling of Unicode names and bilingual text.

The server also uses an optional `@Sesión de Lectura` notification role, a public reading-text document, and channel-level language rules. These are confirmed parts of the surrounding workflow, but it is not yet established that LecturaBot itself owns their management.

Restart recovery, administration tools, catalog editing, moderation, logging, and data-retention behavior are likely necessary for a production bot but have not yet been demonstrated as original-bot behavior.

## 12. Discovery questions for future evidence

The most useful future examples would show:

1. The complete response to `/queue` and `/cola`, with every button visible.
2. What **Start Reading** does, who can press it, and how the two-person minimum is enforced.
3. Joining, leaving, and automatic removal after leaving voice.
4. A complete text-selection interaction at every available difficulty.
5. How **Hard**, **Super Hard**, and Halloween texts appear in the bot picker.
6. The **Your Own Text** / **Mi propio texto** submission flow, including use in another language.
7. The original interface opened by **Add Corrections**.
8. An original-bot reply correction being submitted and the bot message
   changing afterward.
9. An original-bot highlighted correction inside a reading text.
10. A reader pressing **Pass Turn**.
11. Several people voting to skip an AFK reader.
12. Original-bot session shutdown or expiration after a queue becomes empty.
13. Behavior when the command is used from the wrong text channel or without joining voice.
14. Any administrator or text-database management commands.

## 13. Evidence log

### Batch 1: Raw reading-channel transcript

- Demonstrated one mixed English-Spanish reading session.
- Showed queue rotation, queue departures and re-entry, empty state, turn counts, average times, current-reader markers, session ID, support contacts, text-picker prompt, Beginner and Intermediate texts, optional expected emotions, and grouped correction summaries.
- Did not show the user-side commands or button interactions that produced most state changes.

### Batch 2: Bilingual instruction post

- Confirmed button-based queue entry, loss of position on queue or voice-channel departure, corrections while waiting, reply-associated highlighting, current-reader mention and selection, custom texts, manual passing after correction review, AFK skip voting, and the native-language correction policy.
- The accompanying local clipboard image was unavailable, so no visual-only button labels or layout details were added beyond the pasted text.

### Batch 3: Server onboarding and Staff Team reading-session guide

- The English-Spanish onboarding copy documents `/queue` and `/cola`, a minimum of two participants, **Pass Turn**, the optional `@Sesión de Lectura` role, and the regular-channel English-Spanish-only policy.
- A Staff Team guide dated 2025-12-22 documents the six matched voice/text channel pairs, **Start Reading** / **Comenzar Lectura**, level selection, outside texts, two correction methods, and the red pass-turn control.
- A further bot post documents the separate **Other Languages** channel pair and requires **Your Own Text** / **Mi propio texto** for bot-assisted reading in another language.
- The guide links a Google document as a supplemental text source.

### Batch 4: Inspection and offline integration of the linked text document

- Confirmed that it is an external English-Spanish reading library with Easy,
  Medium, Hard, Super Hard, and SFW Halloween sections. A later full audit
  extracted 1,014 unique passages into a validated local catalog snapshot so
  runtime operation does not depend on Google Docs.

### Batch 5: Bot embed and component export

- Confirmed active and empty queue embeds, the text-selection prompt, and the active-reading correction embed.
- Established exact button labels, styles, row order, and `custom_id` routing values.
- Confirmed Beginner, Intermediate, and Advanced catalog choices for both Spanish and English, plus language-specific custom-text actions.
- Confirmed that reading text is ordinary message content while correction aggregation is rendered in an attached embed.
- Detailed templates and `discord.py` mappings are maintained in the companion embed-metadata document.

### Batch 6: Live POC testing feedback

- Required a fresh queue panel after every queue departure and every turn
  transition, including automatic advancement when the current reader leaves.
- Required active queues to be numbered in upcoming-turn order, with the
  current reader as `1`, while retaining completed-turn and average-time data
  on every row.
- Clarified that **Pass Turn** is reserved for the current reader and introduced
  a distinct bilingual **Skip AFK Turn** control with a fixed threshold of
  three unique queued, non-current voters.
- Required a later correction duplicated by another corrector to remain
  attributed but render as `~~struck through~~` to indicate that it was
  discarded.
- Confirmed that newly completed reading statistics must be visible on the
  fresh panel published for the next turn.

### Batch 7: Correction parsing and catalog-repeat feedback

- Required replies to the active reading to submit comma- or newline-separated
  corrections, while preserving commas inside parentheses.
- Raised correction matching and annotation questions that were resolved by
  later live-use clarification in Batch 9.
- Required per-reader catalog no-repeat history for one room session, persisted
  across restart and temporary leave/rejoin, reset on an empty queue, with no
  automatic recycling after a reader exhausts a language/level.

### Batch 8: Statistics-window feedback

- Reported that a new queue retained completed-turn totals from the previous
  day; follow-up clarified that the concern was the statistics, not the
  upcoming-turn position numbers.
- Confirmed that the original bot presented turn totals "day by day."
- Chose independent per-server/user statistics windows for the clone: a normal
  completed reading starts or extends that user's window, and six hours without
  another normal completion resets both the count and average. Queue activity,
  AFK skips, and other users' turns do not extend it.

### Batch 9: Free-form correction and emoji clarification

- Clarified that a custom emoji is ordinary text inside a correction
  annotation, not an abbreviation for a fruit, animal, or other source word.
- Confirmed that parentheses can contain a complete explanation, including
  commas, a sentence, and an emoji, while remaining one correction item; for
  example, `(stress :peepoPray:)`.
- Required every parsed item to be accepted and shown even when the bot cannot
  find it in the reading. Matching now determines highlighting only.
- Added exact-first, conservative fuzzy matching so a likely typo can still
  highlight its clear source word; ambiguous or unmatched annotations remain
  visible without a highlight.
