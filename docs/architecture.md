# Architecture

Status: draft for PRD 00 (lab pilot), 2026-10-06. Living document: update it when a decision changes.

## Constraints from the PRD

- One server machine and any number of GPU machines, each running Docker Desktop (D3). Windows hosts are expected. Development happens on a Mac.
- Students use only a browser. They never reach a GPU machine (R2.6).
- No student message reaches a model, and no reply reaches a student, without being stored and checked (R6.1).
- Checks keep working with no GPU machine up (R6.2).
- Works with the internet unplugged once installed (R1.3).
- OSI-licensed components and openly licensed models only. A school IT person can read the code in an afternoon (vision principle 6).
- One class of 30 students. One live lesson at a time (A3).

## Shape

```
student browsers (phone, tablet, Chromebook, lab PC)      teacher browser
        |                 HTTPS on the school LAN                |
        v                                                        v
+-------------------- server: compose.yml ------------------------+
| caddy      TLS, the only published ports (443, 80 -> 443)        |
| app        Python: student chat, teacher console, admin,         |
|            auth, logging, guard pipeline, worker registry,       |
|            routing to workers. SQLite in a volume.               |
| guard      llama-server on CPU, Qwen3Guard-Gen-0.6B. Internal    |
|            network only.                                         |
+------------------------------------------------------------------+
        |  HTTP + per-worker API key, LAN
        v
+------------- each GPU machine: worker/compose.yml ---------------+
| llm        llama-server (CUDA image on Windows/Linux NVIDIA,     |
|            CPU image on a Mac), one chat model                   |
| agent      heartbeats to the server every 15 s                   |
+------------------------------------------------------------------+
```

Two compose files, one per machine role. Both build from this repository, so installing is `git clone` then `docker compose up -d`. Published images come later and do not change the layout.

## Runtime and libraries

| Part | Choice | Why |
| --- | --- | --- |
| App language | Python 3.14 | Readable by a school IT person. Mature async HTTP. |
| Web framework | Starlette + uvicorn | Routing, SSE streaming and static files with few dependencies. FastAPI adds validation and docs we do not need. |
| Outbound HTTP | httpx | Async streaming to llama-server. |
| Database | SQLite through the stdlib `sqlite3`, WAL mode | One file to back up. 30 students is far inside its write capacity. No ORM. |
| Password hashing | stdlib `hashlib.scrypt` | No extra dependency. |
| Front end | Plain HTML, CSS and JavaScript ES modules, served as static files. No build step. | Anyone can read and change it. No Node toolchain in the image. |
| TLS | Caddy 2.11 with `tls internal` | Automatic local certificate authority, no internet needed. |
| Model server | llama.cpp `llama-server`, official `ghcr.io/ggml-org/llama.cpp` images (`server` for CPU, `server-cuda` for NVIDIA), pinned to a build tag | MIT, OpenAI-compatible, parallel slots, GGUF fits 8-24 GB cards, runs on CPU for dev. |
| Guard model | Qwen3Guard-Gen-0.6B, community GGUF `mradermacher/Qwen3Guard-Gen-0.6B-GGUF` Q8_0, pinned by file hash, on a CPU llama-server | Apache-2.0, small enough for CPU, covers the R6.3 categories. Qwen publishes no GGUF, so the hash pin is how we know which weights we run. |
| Chat model defaults | Gemma 4 from the official `ggml-org` GGUFs: 12B-it on 16 GB, E4B-it on 8 GB, E2B-it on CPU for the Mac | Apache-2.0 with official GGUFs. Qwen3 is superseded by Qwen3.5, which has only community GGUFs at the sizes we need. |

Versions at time of writing: Python 3.14.8, Starlette 1.7.0, uvicorn 0.54.0, httpx 0.28.1, Caddy 2.11.7, llama.cpp build b11433. Pinned versions go in `pyproject.toml`, `compose.yml` (images pinned by tag and digest) and `worker/compose.yml`. Upgrading means changing those pins in a release.

Three runtime Python dependencies: starlette, uvicorn, httpx. Tests add pytest and pytest-asyncio.

## Repository layout

```
compose.yml               server stack
Caddyfile
Dockerfile                app image
pyproject.toml
app/
  main.py                 routes and startup
  db.py                   connection, migrations, queries
  schema/001_init.sql     numbered SQL migrations
  auth.py                 staff login, student join, sessions, roles
  chat.py                 the turn pipeline: store, guard, route, stream
  guard.py                Qwen3Guard call, verdict parsing, category mapping
  workers.py              registry, heartbeat, routing
  live.py                 in-process event bus for SSE
  admin.py                backup, restore, retention, export, audit
  static/                 student.html, teacher.html, admin.html, css, js
worker/
  compose.yml             llama-server + agent
  agent.py                heartbeat loop, stdlib only
tests/
docs/
LICENSES.md               every component and default model with its licence
```

## Components

### app

One uvicorn process with one worker. The live event bus is in memory, so a second process would split it.

ponytail: single process. If one process can't keep up with 30 students, move the event bus to SQLite polling or Redis first.

Three web surfaces, all served by the app:

- `/` student chat. Join, model picker, conversation list, chat.
- `/teach` teacher console. Session controls, live class grid, transcripts, flags, usage summary.
- `/admin` tech teacher. Workers and join command, models on/off, guard actions, safeguarding message and contact, retention, staff accounts, backup, audit log.

JSON API under `/api/`. Live updates over Server-Sent Events. SSE goes one way, works through Caddy with no extra configuration, and reconnects on its own.

### guard

A second llama-server container on the server's internal compose network, CPU only, with `-np` slots so several checks run at once. The app calls it with the Qwen3Guard prompt format and parses the reply: `Safety: Safe|Unsafe|Controversial`, then `Categories:`, plus a `Refusal:` line on reply checks. Prompt and reply checks use different category lists (Jailbreak is prompt-only). The embedded chat template is checked against the official one in a test, because Qwen3 GGUFs have shipped with template problems. Weights are downloaded once into a volume at install.

### worker

`worker/compose.yml` runs two containers:

- `llm`: llama-server with one chat model, `--api-key` set to a key the agent generates on first start, `-np 4` slots by default. The port is published on the LAN so the server can reach it.
- `agent`: about 50 lines of stdlib Python. Every 15 seconds it reads llama-server's `/health`, `/v1/models` and `/slots` (with the API key), then POSTs to the server's `/api/workers/heartbeat` with the join token, its own address, its API key, its models and its free slots.

The join command the admin console shows is one line that sets `SERVER_URL` and `JOIN_TOKEN` and runs `docker compose -f worker/compose.yml up -d`. On Windows it is the PowerShell form of the same line.

GPU access: the `nvidia` compose profile requests the device with `gpus: all`. On Windows that needs Docker Desktop with the WSL2 backend and a current NVIDIA driver on Windows itself, nothing installed inside WSL. On a Mac, Docker cannot use the Apple GPU, so the `cpu` profile runs the CPU image with Gemma 4 E2B. That is for development only. Docker Model Runner can use the Mac GPU but has no API key, so it is not a worker option.

ponytail: one model per worker. A machine that should offer two models runs the worker stack twice on different ports. llama-server's router mode (`--models-dir`, `--models-max`) can serve several from one process; switch to it once a machine needs to swap models on demand, since swapping mid-lesson stalls the class.

## Turn pipeline

Every student message goes through `chat.py`:

1. Check the student's session is open, not paused, and under the message limit. Refuse if not.
2. Store the message.
3. Guard the prompt. Store the verdict. If the category action blocks, store the block, publish to the teacher, and return the block text: the school's message for self-harm, a plain explanation otherwise. Flag if the action flags.
4. Pick a live worker serving the chosen model with the most free slots. If none, return a "no model available" message and store that.
5. Send the class instructions, the conversation so far and the message to the worker, streaming.
6. Release the reply in segments. Buffer tokens to the end of a sentence or 300 characters, guard the reply so far together with the prompt, then send the segment to the student. If a segment trips the guard, stop the generation, store everything generated, and replace what follows with the block text. Text already shown stays shown, and the flag records that.
7. Store the full reply and publish the turn to the teacher's live view.

A turn is stored before anything leaves the server. A failure at any step is stored with the turn, so the teacher can see it.

Step 6 is how R4.3 (streamed reply) and R6.1 (reply checked first) both hold. The cost is a guard call per segment on CPU. The load test (acceptance 12) measures it. If the guard is the bottleneck, segments get longer first, and the guard moves to a GPU worker only after that, keeping the CPU guard as the fallback for R6.2.

Category mapping from Qwen3Guard to R6.3:

| R6.3 category | Qwen3Guard category |
| --- | --- |
| Self-harm | Suicide & Self-Harm |
| Sexual content | Sexual Content or Sexual Acts |
| Violence | Violent |
| Hate and bullying | Unethical Acts |
| Dangerous activities | Non-violent Illegal Acts |
| Jailbreak | Jailbreak (prompts only) |
| Personal information | PII |

Qwen3Guard's "Controversial" verdict counts as unsafe for students. Categories Qwen3Guard reports that are not in the table (politically sensitive, copyright) are stored and ignored.

## Identity and access

Staff:

- Roles `admin` and `teacher`. An admin can also teach.
- Username and password, scrypt-hashed. Admins create teacher accounts.
- First run: the app prints a one-time setup code to its log. The first visit to `/admin` asks for it and creates the admin. This stops whoever on the LAN reaches the page first from taking it over.

Students (D1):

- The teacher starts a lesson session for a class. The app generates a six-digit code, unique among open sessions.
- A student enters the code and a display name. The app creates a student record for that session and sets a session cookie.
- The teacher sees each name appear on the class grid and can rename or remove a student. A removed student's cookie stops working.
- Opening and closing: a session is `open`, `paused` or `closed`. Pause keeps students signed in but stops sending (R4.6). Close ends the session, the code stops working and student cookies expire. A closed session's transcripts stay readable by the teacher.
- A student record lasts one session. "Past conversations in this class" (R4.4) means past conversations in this session. Linking a student across lessons needs accounts, which are a later increment.

Cookies: random 32-byte tokens stored in a `auth_sessions` table, `HttpOnly`, `Secure`, `SameSite=Lax`. Every state-changing request needs the cookie and a matching CSRF header from the page.

Join codes can be guessed: 30 tries per minute per IP address on the join endpoint. Six digits against a handful of open sessions makes guessing slow enough.

## Worker trust

- The admin console shows the join token and can rotate it. Rotating it drops every worker until each is rejoined.
- A heartbeat without the current token is rejected. A removed worker is put on a deny list by worker ID, so its heartbeats are rejected even with the token.
- The server sends the worker's API key on every model request. A student device that reaches the worker port without the key is refused (acceptance 5). The network docs add a firewall rule allowing only the server's address.
- Server-to-worker traffic is plain HTTP on the school LAN. Sharing GPUs between schools (vision, later increment) needs that link encrypted, for example a WireGuard tunnel between the sites. Nothing else in the design assumes the worker is on the same network.
- Model requests already carry no identity: the app sends only the class instructions and the conversation text, never a student name, class name, school or user field. Keep it that way, because it is what lets a school's server anonymise traffic to another school's GPUs. Cross-school sharing adds a redaction step (for example Presidio) on requests bound for a remote worker.
- A worker missing three heartbeats (45 seconds) is marked down and gets no requests (R2.4: within a minute). A request that fails to connect marks the worker down at once and retries on another worker serving the same model.

## Data

SQLite file in the `data` volume. Tables:

| Table | Holds |
| --- | --- |
| `staff` | Staff accounts and roles. |
| `auth_sessions` | Cookie tokens for staff and students. |
| `classes` | Name, instructions (system prompt), message limit, owning teacher. |
| `lesson_sessions` | Class, join code, state, opened and closed times. |
| `students` | Display name, lesson session, removed flag. |
| `conversations` | Student, model, started time. |
| `messages` | Conversation, role, text, time, status (ok, blocked, error), worker used. |
| `flags` | Message, category, action taken, reviewed by, reviewed at. |
| `workers` | ID, address, API key, models, free slots, last heartbeat, removed. |
| `models` | Model name, enabled. |
| `settings` | Key/value: category actions, safeguarding message, safeguarding contact, retention days (30), join token. |
| `audit` | Who, what, when, for staff actions (R7.4). |

Migrations are numbered SQL files applied at startup and recorded in `PRAGMA user_version`.

Retention: an hourly task deletes conversations, messages and flags older than the retention setting, plus closed lesson sessions with no remaining messages (R7.2).

Backup: `docker compose exec app python -m app.admin backup` writes a single `.db` file using SQLite's online backup API, so the app keeps running. Restore stops the app, replaces the file, and starts it again. The admin page offers the same backup as a download. Model weights are not in the backup; they download again.

Export one student (R7.3): JSON of their conversations, messages and flags. Delete one student removes all of those rows. Both go in the audit log.

## Teacher console

The console is a plain web page, but the PRD expects a non-technical teacher to enjoy using it. Requirements on the front end:

- A session bar at the top: class name, the join code in large type, open/pause/close buttons, time since start.
- A grid of student cards: name, latest message, message count, a red border and badge when flagged. Cards update over SSE without the page jumping.
- A usage strip (D1): students active in the last five minutes, total messages, messages per student, model use, flags by category.
- Clicking a card opens the transcript beside the grid, with blocked messages and flag reasons shown inline and a "Mark reviewed" button.
- Works on a laptop and an iPad. Keyboard accessible. Colour is never the only signal for a flag.

Live view: the app publishes an event per stored message, flag, join and state change to the in-memory bus. Each teacher console holds one SSE connection filtered to its session. On reconnect it fetches current state over JSON, then resumes the stream.

## Failure handling

| Failure | Behaviour |
| --- | --- |
| No live worker for a model | Student sees "this model isn't available right now". Admin console shows the worker down. |
| Worker drops mid-reply | Partial reply stored and marked as an error. Student sees what was shown plus a retry message. |
| Guard container down | Fail closed. Students see "chat is unavailable", and the teacher and admin consoles show a red banner. No unchecked text goes either way. |
| Database write fails | The turn is refused. Nothing is sent to a model that isn't stored. |
| Server restarts | Students and teachers stay signed in (cookies are in SQLite). Open SSE streams reconnect. |

## Network and TLS

- Caddy publishes 443 and redirects 80. `SERVER_NAME` in `.env` sets the hostname (a local DNS name or the IP address).
- `tls internal` creates a local CA in the `caddy_data` volume. The admin page links to download the root certificate, and `docs/install.md` shows how to trust it on Windows, macOS, iOS, Android and ChromeOS. Until a device trusts it, the browser shows a warning (PRD risk). iOS needs the profile installed and then enabled under Certificate Trust Settings. Managed Chromebooks take it through the Google Admin console. Android trust in Chrome is unverified and is a pilot check.
- On the Mac in development, `http://localhost` skips TLS.
- The guard and the database are on the internal compose network and publish no ports.

## Tests

- Unit tests with pytest for verdict parsing, category mapping, routing choice, rate limits, retention and migrations.
- API tests drive the Starlette app in-process with httpx's ASGI transport against a temporary SQLite file. A fake worker and a fake guard are small Starlette apps that stream canned replies and verdicts, so tests need no models.
- One smoke test boots the real compose stack with the CPU worker and Gemma 4 E2B, joins a student, and checks a reply arrives and appears in the teacher stream. Run on demand on the Mac, not in every test run.
- Guard check: a fixed list of test prompts per R6.3 category run against the real guard (acceptance 7), on demand.
- Load test: `tests/load.py` simulates 30 students over the HTTP API and reports lost messages and time to first segment (acceptance 12). Run on the Windows GPU machine.
- Front end: checked by hand against `docs/manual-tests.md` on a phone, a tablet and a laptop. Browser automation waits until the pages settle.

## Operations

- Logs: the app logs JSON lines to stdout. Docker keeps them. Chat text never goes to logs, only to the database.
- Health: `/healthz` reports database, guard and worker counts. The admin page shows the same.
- Upgrade: `git pull && docker compose up -d --build`. Migrations run on start. Back up first; the docs say so.
- No telemetry leaves the server.

## Decisions

| Decision | Chosen | Over | Because |
| --- | --- | --- | --- |
| Gateway | Own app | LiteLLM | Routing plus guard hooks are a few hundred lines here. LiteLLM's needed features are partly enterprise-only, and its PyPI package was trojaned in March 2026. |
| Monitoring | SQLite + SSE page | Langfuse | Langfuse is six services and 16 GiB of RAM, with no classroom view. |
| Chat UI | Own pages | Open WebUI, LibreChat | Open WebUI is non-OSI from v0.6.6. LibreChat has no live teacher view and needs MongoDB. |
| Worker registry | Heartbeat to the app | Static config | The teacher adds a machine without editing files (R2.1-R2.2). |
| Reply checking | Segment-by-segment before release | Check after full reply | Keeps streaming (R4.3) without unchecked text (R6.1). |
| Student identity | Per-session record from a code and name | Accounts | D1. |
| Front end | No-build static files | React/Vue | Auditable, no toolchain, enough for three pages. |
