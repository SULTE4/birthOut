<div align="center">

# BirthOut

**Export a WhatsApp group chat. Review what it found. Every birthday lands in Google Calendar.**

A local web app that finds birthday evidence in exported WhatsApp chats (English, Russian, Kazakh),<br>
asks you to approve every candidate, and syncs the approved ones to a dedicated Google Calendar.

![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-local%20web%20app-009688?logo=fastapi&logoColor=white)
![Google Calendar](https://img.shields.io/badge/Google%20Calendar-sync-4285F4?logo=googlecalendar&logoColor=white)
![Ollama](https://img.shields.io/badge/Ollama-optional-000000?logo=ollama&logoColor=white)
![Tests](https://img.shields.io/badge/tests-pytest-0A9EDC?logo=pytest&logoColor=white)

</div>

---

## Why

Your class or family group chat already knows everyone's birthday: every year it fills up with "Happy birthday Aigerim!" messages. BirthOut reads an export of that chat, figures out who was congratulated on which day, and turns it into calendar events, so next year you are the one who remembers.

## How it works

```
 WhatsApp  ──►  Export chat (without media)  ──►  .txt / .zip
                                                     │
                                                     ▼
 ┌──────────────────────────────────────────────────────────────┐
 │ BirthOut  (http://127.0.0.1:8765, local only)                │
 │                                                              │
 │  importers/whatsapp   Android + iPhone formats, day/month    │
 │         │                                                    │
 │         ▼                                                    │
 │  extractors/rules     explicit dates, "today/tomorrow",      │
 │         │             congratulations from 2+ senders,       │
 │         │             one person thanking the group          │
 │         ▼                                                    │
 │  extractors/ollama    (optional) double-checks unsure ones   │
 │         │                                                    │
 │         ▼                                                    │
 │  review page          approve / reject / merge aliases       │
 └─────────┬────────────────────────────────────────────────────┘
           │  only approved birthdays, idempotent
           ▼
 Google Calendar  ──►  "BirthOut Birthdays" calendar, yearly events
```

## Features

- **Finds birthdays three ways.** Explicit dates, named congratulations from at least two people, and "thank you all" replies after a wave of wishes.
- **Three languages.** English, Russian and Kazakh messages, from Android and iPhone exports.
- **You decide.** Every candidate needs your approval. Decisions can be undone, and merges handle nicknames ("Aika" = "Aigerim").
- **Resumable reviews.** Progress is autosaved after every action and survives restarts.
- **Safe calendar sync.** Uses only the `calendar.app.created` scope and its own calendar. Re-syncing never creates duplicates.
- **Private by design.** Runs on `127.0.0.1`. Full chat text stays in memory and is never written to disk or logs. OAuth tokens go to the OS keyring.
- **No AI required.** The rule-based extractor works offline. Ollama is an optional second opinion for uncertain cases.

## Quick start

Python 3.11 or newer is required.

```bash
python -m venv .venv
```

Activate the environment:

- Linux/macOS: `source .venv/bin/activate`
- Windows PowerShell: `.venv\Scripts\Activate.ps1`

Then install and run:

```bash
python -m pip install -e .
birthout
```

BirthOut opens `http://127.0.0.1:8765`. It binds only to the local machine. Application data is stored in the operating system's standard per-user data directory. Set `BIRTHOUT_DATA_DIR` to override it.

## Export a WhatsApp group

On the phone, open the group, choose **Export chat**, and select **Without media**. Transfer the resulting `.txt` or `.zip` file to the computer and import it in BirthOut. If every date is numerically ambiguous, explicitly choose the export's day/month order in the import form.

BirthOut proposes candidates from:

- Explicit birthday dates and relative terms such as today/tomorrow.
- Named birthday congratulations from at least two distinct senders.
- Multiple generic congratulations followed by one identifiable participant thanking the group.

Thank-you inference is chronological: at least two different people must congratulate first, then exactly one non-congratulator may respond with thanks during the remainder of that chat date. BirthOut uses that responder's exported group-member name rather than trying to reconstruct WhatsApp reply relations, which are not present in normal text exports.

Every candidate must be approved before synchronization.

### Nicknames and aliases

When different names resolve to the same month and day, BirthOut shows them as a possible merge group. A shared date is not treated as identity proof: select only the names whose source evidence refers to the same person, choose the canonical calendar name, and confirm the merge. Conflicting explicit birth years cannot be merged. Merges can be undone before approval, and aliases remain only in the current in-memory review session.

Review results are ordered by source-evidence count and then confidence. Name fields offer detected aliases and every sender name from the group as suggestions. The field always remains editable after selecting a suggestion; there is no separate manual-name mode.

Approving or rejecting a candidate moves it below all pending candidates. Either decision can be undone from the candidate card. Undoing an approval also restores the previous local birthday record, or removes the new record, as long as it has not yet been synchronized to Google Calendar. Synchronized events are intentionally not deleted by review-page undo.

Review progress is autosaved locally after every import, merge, approval, rejection, undo, and successful synchronization. Saved reviews survive application restarts and can be resumed or deleted from the home page. Deleting a review does not delete approved birthdays, synchronized events, or Google credentials.

## Google Calendar setup

1. Create or select a project in Google Cloud Console.
2. Enable **Google Calendar API**.
3. Configure the OAuth consent screen and add your Google account as a test user if the app remains in testing mode.
4. Create an OAuth client with application type **Desktop app**.
5. Download its JSON and upload it on the BirthOut home page or directly from an existing review.
6. Approve birthdays and select **Sync approved to Google**. The system browser opens for consent.

BirthOut requests `calendar.app.created`, creates a dedicated **BirthOut Birthdays** calendar, and manages only that calendar. OAuth tokens are stored through the operating-system keyring. The OAuth client JSON is stored with user-only file permissions where supported.

## Optional Ollama

The deterministic extractor does not require AI or a GPU. For extra validation of uncertain results:

```bash
ollama pull qwen3:4b
ollama serve
```

Leave the URL as `http://127.0.0.1:11434`, or enter the private-LAN URL of another computer running Ollama. Ollama has no API authentication by default: do not expose its port directly to the internet. Only short evidence windows for uncertain candidates are sent to the configured server.

## Privacy

Full raw chat messages remain in process memory and are not written to SQLite or application logs. To make reviews resumable, BirthOut stores candidate details, participant-name suggestions, decisions, and only the evidence excerpts displayed on the review page.

## Development

```bash
python -m pip install -e '.[dev]'
pytest
```

Tests use synthetic chats and mocked/local data; no real chat export or Google credentials are required.

```
src/birthout/
├── main.py, app.py        # entry point, FastAPI routes
├── importers/whatsapp.py  # export parsing -> ChatMessage
├── extractors/rules.py    # deterministic birthday detection
├── extractors/ollama.py   # optional LLM validation
├── sessions.py            # review sessions, merges, undo
├── storage.py             # SQLite, per-user data dir
├── calendar.py            # Google Calendar sync
├── templates/, static/    # review UI
tests/                     # pytest, synthetic chats
```

## Limitations

- WhatsApp exported text only; no live WhatsApp account or group access.
- Telegram import is not implemented yet, but importers normalize into `ChatMessage` so it can be added independently.
- March 1 handling for a February 29 birthday means an annual March 1 observance, including leap years.
- Saved reviews contain displayed evidence excerpts but never the complete imported chat.

## Roadmap

- [ ] Telegram import (importers already normalize into `ChatMessage`)
