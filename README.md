# Voice Agent V0 — Multilingual AI Receptionist (Sharma Dental Care demo)

A browser-based realtime AI receptionist, "Aisha," for a fictional dental clinic
(**Sharma Dental Care**). You talk to her through your computer microphone — no
telephone number, no Twilio/Exotel. She understands English, Hindi, and
Hinglish (natural code-switching), answers questions using the clinic's real
seeded knowledge, checks fake-but-real appointment availability, books
appointments into SQLite, and produces a structured post-call summary.

This is a **V0 local demo**, built to evolve into a production SaaS later. It
intentionally has no Docker, Postgres, Redis, Kafka, or cloud deployment —
just Python, SQLite, and a small Next.js frontend.

> ⚠️ **Not production-ready.** Fake business/customer data only. No auth, no
> encryption-at-rest review, no HIPAA/GDPR/compliance review has been done.
> A real deployment handling real patient data would need a full
> privacy/security/compliance review first.

---

## 1. What this project does

1. You click **Start Conversation** in the browser and grant microphone access.
2. Your mic audio streams to **LiveKit** (realtime audio/session transport).
3. **Sarvam AI Saaras** (`livekit-plugins-sarvam`, realtime WebSocket STT)
   transcribes your speech, in `codemix` mode for natural Hindi/English
   code-switching.
4. The transcript goes to an **LLM** (OpenAI by default, behind a provider
   abstraction) which decides what to say and which tools to call.
5. The LLM's reply streams to **Sarvam Bulbul** streaming TTS
   (`livekit-plugins-sarvam`) and plays back through your speakers via LiveKit.
6. You can **interrupt Aisha mid-sentence**. Turn-taking uses LiveKit's
   audio end-of-turn model on top of Silero VAD. Interruptions use LiveKit's
   adaptive detector (it ignores backchannels like "haan"/"hmm") on
   noise-cancelled audio, and Aisha resumes after a false interruption.
   Booking is protected: it can't be interrupted mid-write, and it's
   idempotent if retried. See "Latency and interruption tuning" below.
7. Aisha can call real tools: `check_availability`, `book_appointment`,
   `get_business_hours`, `get_service_price` — all backed by SQLite, never
   invented.
8. Every call and every finalized (non-partial) message is persisted.
9. When the call ends, a second LLM call extracts strict structured JSON
   (intent, customer info, appointment details, outcome, summary) validated
   with Pydantic before being stored.
10. The frontend shows a live transcript, agent state (listening/thinking/
    speaking), and the final call result — clean enough to screen-record.

---

## 2. Architecture

```
   Browser (mic)                                  Browser (speaker)
        |                                                 ^
        v                                                 |
  +-----------------------------  LiveKit room  -----------------------------+
  |                                                                          |
  |   Sarvam Saaras realtime STT  --->  Agent + LLM  --->  Sarvam Bulbul TTS |
  |     (livekit-plugins-sarvam)      (provider-        (livekit-plugins-    |
  |                                   abstracted,              sarvam)       |
  |                                   OpenAI by default)                    |
  |                                        |                                |
  |                                        v                                |
  |                                Tools -> SQLite                          |
  |                          (check_availability, book_appointment,         |
  |                           get_business_hours, get_service_price)        |
  +--------------------------------------------------------------------------+
                                        |
                                        v
                      Post-call extraction (2nd LLM call)
                                        |
                                        v
                    SQLite: calls, call_messages, appointments,
                            call_extractions, businesses, customers
                                        ^
                                        |
                          FastAPI (app/main.py) <---- Next.js frontend
                     (token minting + read-only call/appointment API)
```

Two backend processes run independently:

- **`app/main.py`** — FastAPI HTTP API. Mints LiveKit room tokens for the
  browser and exposes read endpoints (calls, transcripts, appointments,
  extractions) for the demo UI. No realtime audio here.
- **`app/agent/agent.py`** — the LiveKit Agents *worker*. This is the actual
  voice pipeline: it joins a LiveKit room whenever the frontend creates one
  and runs the STT → LLM → TTS pipeline for that call.

### Backend module layout

```
backend/
  app/
    agent/          # LiveKit worker: entrypoint, system prompt, call lifecycle persistence
    speech/          # (reserved) — Sarvam STT/TTS are used directly via livekit-plugins-sarvam;
                      #   this package is where you'd wrap a custom provider later
    llm/             # LLM provider abstraction (OpenAI implemented; add more here)
    tools/           # Deterministic, testable tool implementations (appointments, customers)
    business/        # KnowledgeProvider abstraction (DB-backed now, RAG-backed later)
    database/        # SQLAlchemy models, engine/session, seed script
    postcall/        # Post-call structured extraction + Pydantic schema
    api/              # FastAPI routes
    config.py        # Typed settings (pydantic-settings)
    main.py          # FastAPI app
  data/              # SQLite file lives here (gitignored)
  tests/             # pytest suite for business logic
```

### Interfaces built for future migration

| Today (V0)                                   | Interface                | Future                                  |
|-----------------------------------------------|---------------------------|------------------------------------------|
| SQLite                                        | SQLAlchemy models          | PostgreSQL (change `DATABASE_URL` + Alembic) |
| Hardcoded/DB business knowledge                | `KnowledgeProvider` (`app/business/service.py`) | `VectorKnowledgeProvider` (RAG/pgvector) |
| Browser + LiveKit                              | LiveKit room/session       | Twilio/Exotel/SIP trunk into LiveKit     |
| Local worker process                           | LiveKit Agents worker      | Cloud-deployed worker fleet              |
| Single seeded business                         | `business_id` FK everywhere | Multi-tenant SaaS                      |
| Fake `AppointmentSlot` table                   | `tools/appointments.py`    | Google Calendar / CRM API                |
| One `LLMProvider` (OpenAI)                     | `app/llm/base.py`          | Anthropic/Gemini/local model             |

---

## 3. Setup (from a clean machine)

### Prerequisites

- **Python 3.11+** — <https://www.python.org/downloads/>
- **Node.js LTS + npm** — <https://nodejs.org/>
- **Git**

Check versions:

```bash
git --version
python --version   # or python3 --version
node --version
npm --version
```

### Clone and bootstrap

```bash
git clone <this-repo-url> voice-agent-v0
cd voice-agent-v0
```

**Windows (PowerShell):**

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
```

**macOS / Linux:**

```bash
chmod +x scripts/*.sh
./scripts/setup.sh
```

This one command (verified working on this machine):

- creates `backend/.venv` (Python virtual environment)
- installs backend dependencies from `backend/pyproject.toml`
- creates `backend/data/` and `backend/logs/`
- copies `.env.example` → `.env` (repo root) **only if `.env` doesn't already exist**
- copies `frontend/.env.local.example` → `frontend/.env.local` (same rule)
- runs `npm install` in `frontend/`
- initializes the SQLite database and seeds Sharma Dental Care + 14 days of
  fake appointment availability
- runs the backend test suite

### Add your credentials

Open `.env` (repo root) and fill in:

```
LIVEKIT_URL=wss://YOUR_LIVEKIT_URL
LIVEKIT_API_KEY=YOUR_LIVEKIT_API_KEY
LIVEKIT_API_SECRET=YOUR_LIVEKIT_API_SECRET
SARVAM_API_KEY=YOUR_SARVAM_API_KEY
OPENAI_API_KEY=YOUR_OPENAI_API_KEY
```

- **LiveKit**: create a free project at <https://cloud.livekit.io> → Settings → Keys.
- **Sarvam AI**: get a key at <https://dashboard.sarvam.ai>.
- **OpenAI**: get a key at <https://platform.openai.com/api-keys>.

`frontend/.env.local` only needs `NEXT_PUBLIC_API_BASE_URL` (already defaults
to `http://localhost:8000`) and `NEXT_PUBLIC_DEFAULT_BUSINESS_ID` (already set
to `sharma-dental`) — no secrets live in the frontend.

The app detects placeholder credentials on its own and prints a clear message
(e.g. `Missing SARVAM_API_KEY. Add your Sarvam API key to .env.`) instead of
an obscure stack trace — both the FastAPI server and the agent worker log a
warning at startup listing exactly which variables are still placeholders.

### Run it (three terminals)

```powershell
# Terminal 1
scripts\dev-backend.ps1      # FastAPI on http://localhost:8000

# Terminal 2
scripts\dev-agent.ps1        # LiveKit Agents worker (Aisha)

# Terminal 3
scripts\dev-frontend.ps1     # Next.js on http://localhost:3000
```

(macOS/Linux: `./scripts/dev-backend.sh`, `./scripts/dev-agent.sh`,
`./scripts/dev-frontend.sh`.)

Open **http://localhost:3000**.

### Other useful scripts

| Script | Does |
|---|---|
| `scripts/setup.ps1` / `.sh` | Full one-shot bootstrap (safe to re-run) |
| `scripts/seed.ps1` / `.sh` | Re-seed demo business + regenerate the rolling 14-day availability window |
| `scripts/test.ps1` / `.sh` | Run the backend pytest suite |
| `scripts/dev-backend.ps1` / `.sh` | Start the FastAPI HTTP API |
| `scripts/dev-agent.ps1` / `.sh` | Start the LiveKit Agents worker |
| `scripts/dev-frontend.ps1` / `.sh` | Start the Next.js UI |

No `make` required (none of these need Docker/Redis/Postgres/etc., and `make`
isn't reliably available on Windows out of the box, so plain scripts are used
instead of a Makefile).

---

## 4. How to test the demo

With all three processes running and real credentials in `.env`:

1. Open http://localhost:3000, click **Start Conversation**, allow microphone access.
2. Try this suggested script:
   - *"Hi, I need a dental appointment."*
   - *"Actually Hindi mein baat kar sakte ho?"*
   - *"Kal afternoon mein kya available hai?"*
   - While Aisha is listing times, **interrupt her**: *"Actually kal nahi, Thursday."*
   - *"2 baje book kar do."*
   - Provide your name and phone number when asked.
   - Aisha should call `book_appointment`, then repeat back the confirmed
     date/time before you end the call.
3. Click **End Conversation**. Within a few seconds the **Call Result** panel
   shows the structured post-call extraction (customer, service, date, time,
   outcome, summary).

### Where to inspect everything

- **Live transcript & agent state**: in the browser UI as the call happens.
- **Debug log**: the collapsible "Debug log" panel in the UI (client-side
  connection/transcription/agent-state events). Deeper instrumentation —
  STT/LLM/TTS latency, tool calls and results, interruption/turn events — is
  printed by the **agent worker terminal** (Terminal 2), tagged
  `[call <id>] ...`. Look for lines like:
  ```
  [call ab12cd34] latency stt duration=0.412s
  [call ab12cd34] latency llm ttft=0.318s duration=0.901s
  [call ab12cd34] latency tts ttfb=0.220s duration=1.05s
  [call ab12cd34] latency end-of-turn delay=0.180s transcription_delay=0.090s
  [call ab12cd34] [tool] check_availability(2026-09-17, doctor=None) -> {...}
  [call ab12cd34] agent_state: speaking -> listening
  ```
- **SQLite database**: `backend/data/voice_agent.db`. Inspect with any SQLite
  browser, or:
  ```bash
  cd backend
  .venv/Scripts/python.exe -c "import sqlite3; c = sqlite3.connect('data/voice_agent.db'); print(c.execute('select id,status,outcome,summary from calls order by started_at desc limit 5').fetchall())"
  ```
- **Appointments**: `GET http://localhost:8000/api/appointments`
- **Calls + transcript**: `GET http://localhost:8000/api/calls` then
  `GET http://localhost:8000/api/calls/{call_id}`
- **Post-call extraction**: `GET http://localhost:8000/api/calls/{call_id}/extraction`
- **API docs (Swagger UI)**: http://localhost:8000/docs

---

## 5. Design notes / known V0 simplifications

- **TTS language is fixed per session** (`SARVAM_TTS_LANGUAGE`, default
  `hi-IN`). Bulbul speakers read code-mixed Hindi/English text naturally
  under one language setting, so this covers the English/Hindi/Hinglish demo
  well, but per-utterance language/voice switching is not implemented.
- **Latency and interruption tuning** (all set in `.env`, see `.env.example`):
  - `TURN_DETECTION=model` uses LiveKit's audio end-of-turn model (LiveKit
    Cloud inference, with a bundled local fallback). `vad` is silence-only.
  - `ENDPOINTING_MIN_DELAY` / `ENDPOINTING_MAX_DELAY` and
    `PREEMPTIVE_GENERATION` set how quickly Aisha starts replying.
  - `NOISE_CANCELLATION=bvc|nc|off` (bvc/nc need LiveKit Cloud),
    `INTERRUPTION_MODE=adaptive|vad`, `INTERRUPTION_MIN_DURATION`,
    `INTERRUPTION_MIN_WORDS` and `FALSE_INTERRUPTION_TIMEOUT` control barge-in.
  - `LLM_FALLBACK_PROVIDERS` / `LLM_ATTEMPT_TIMEOUT` fail over to another LLM
    mid-call.
  - `AGENT_IDLE_PROCESSES` keeps prewarmed worker processes ready so calls
    don't wait on a cold start.
  - The greeting is spoken directly, not LLM-generated, and its audio is
    cached in `backend/data/tts_cache` after the first call.
- **`next lint` was removed in Next.js 16** (the framework's own CLI no longer
  ships it); the frontend's static check is `npm run typecheck`
  (`tsc --noEmit`) instead of a lint step.
- **Single business, single tenant.** `business_id` foreign keys are already
  everywhere, but only one business (`sharma-dental`) is seeded.
- Post-call extraction runs as a plain async call when the LiveKit job shuts
  down — there's no background job queue (no Celery/Redis), which is
  appropriate at this scale and explicitly out of scope for V0.

---

## 6. Future roadmap (not implemented — by design)

These are documented, not built, to keep V0 minimal:

1. **Twilio/Exotel/SIP telephony** — LiveKit supports SIP trunking; a real
   phone number would connect to the same `AgentSession` pipeline, just via a
   different room-input source instead of a browser mic.
2. **PostgreSQL** — change `DATABASE_URL`; add Alembic migrations. Models
   already use portable SQLAlchemy types.
3. **pgvector / RAG** — implement `VectorKnowledgeProvider` alongside
   `DatabaseKnowledgeProvider` in `app/business/service.py`; swap which one
   `app/agent/agent.py` instantiates.
4. **PDF uploads / website ingestion** — feed a document pipeline into the
   same `VectorKnowledgeProvider`.
5. **Google Calendar** — replace `app/tools/appointments.py`'s SQLite-backed
   `check_availability`/`book_appointment` with Calendar API calls; keep the
   same function signatures so `app/agent/agent.py` doesn't change.
6. **HubSpot/Salesforce** — add a CRM sync step in `app/tools/customer.py`.
7. **Multiple businesses** — already schema-ready; add a business picker to
   the frontend and to the LiveKit room-name/token flow.
8. **Customer authentication** — add auth middleware to `app/api/routes.py`.
9. **Usage metering / Stripe billing** — track per-call/per-minute usage in a
   new table, wire to Stripe metered billing.
10. **Call recording storage** — `AgentSession`/LiveKit already support room
    recording/egress; enable and point at S3-compatible storage.
11. **Production monitoring** — the `metrics_collected` events already logged
    here are the seam; export them to Prometheus/OpenTelemetry (LiveKit
    Agents has built-in OTel support) instead of plain log lines.
12. **Background jobs** — if post-call extraction volume grows, move it from
    an inline shutdown callback to a proper queue.
13. **Cloud deployment** — containerize `app/main.py` and `app/agent/agent.py`
    separately; LiveKit Cloud can host the worker fleet directly.

---

## WHAT YOU NEED TO DO MANUALLY

1. Create/get a **LiveKit** project + API key/secret (<https://cloud.livekit.io>).
2. Create/get a **Sarvam AI** API key (<https://dashboard.sarvam.ai>).
3. Create/get an **OpenAI** API key (<https://platform.openai.com/api-keys>).
4. Paste all of the above into `.env` (repo root).
5. Run the setup script, then the three dev scripts (see Section 3).

Everything else — venv, dependencies, database schema, seed data, tests — is
already done for you by `scripts/setup.ps1` / `scripts/setup.sh`.
