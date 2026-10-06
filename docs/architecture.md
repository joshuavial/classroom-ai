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
| web        Next.js: student chat, teacher console, admin.        |
| app        Python API and gateway: auth, logging, guard          |
|            pipeline, worker registry, routing to workers, SSE.   |
| db         postgres, data in a volume. Internal network only.    |
| guard      llama-server on CPU, Qwen3Guard-Gen-0.6B. Internal    |
|            network only.                                         |
+------------------------------------------------------------------+
        |  HTTP + per-worker API key, LAN
        v
+------------------------ each GPU machine ------------------------+
| agent      the only port the server talks to. Checks the API     |
|            key, forwards to the model server, heartbeats every   |
|            15 s with the models it offers. Docker container.     |
| model      any OpenAI-compatible server the school prefers:      |
| server     llama-server, Ollama, vLLM, ... in Docker or native.  |
|            Reachable only by the agent.                          |
+------------------------------------------------------------------+
```

Two compose files, one per machine role. Both build from this repository, so installing is `git clone` then `docker compose up -d`. Published images come later and do not change the layout.

## Runtime and libraries

| Part | Choice | Why |
| --- | --- | --- |
| App language | Python 3.14 | Readable by a school IT person. Mature async HTTP. |
| Web framework | Starlette + uvicorn | Routing and SSE streaming with few dependencies. FastAPI adds validation and docs we do not need. |
| Outbound HTTP | httpx | Async streaming to workers. |
| Database | PostgreSQL 18, official `postgres` image (ADR-0015) | Several app processes can share it. `LISTEN`/`NOTIFY` carries live updates. Easy to query for reports. |
| Database driver | psycopg 3 with `psycopg-pool` (async pool) | LGPL-3.0. Plain SQL, no ORM. |
| Password hashing | stdlib `hashlib.scrypt` | No extra dependency. |
| Front end | Next.js (App Router, TypeScript) with React and plain CSS modules, built with `output: "standalone"` and run on Node.js 24 LTS in its own `web` image (ADR-0016) | Easier to build a pleasant teacher console, with component tests. MIT licensed. Holds no data and no secrets. |
| TLS | Caddy 2.11 with `tls internal` | Automatic local certificate authority, no internet needed. |
| Model server on workers | Any OpenAI-compatible server (ADR-0014). Documented recipes: llama.cpp `llama-server` in Docker, and Ollama installed natively | The school runs GPUs however suits its machines. The agent gives every backend the same interface and the same auth. |
| Guard model | Qwen3Guard-Gen-0.6B, community GGUF `mradermacher/Qwen3Guard-Gen-0.6B-GGUF` Q8_0, pinned by file hash, on a CPU llama-server | Apache-2.0, small enough for CPU, covers the R6.3 categories. Qwen publishes no GGUF, so the hash pin is how we know which weights we run. |
| Suggested chat models in the recipes | Gemma 4: 12B-it on 16 GB, E4B-it on 8 GB, E2B-it on CPU | Apache-2.0 with official GGUFs. Qwen3 is superseded by Qwen3.5, which has only community GGUFs at the sizes we need. |

Versions at time of writing: Python 3.14.8, Starlette 1.7.0, uvicorn 0.54.0, httpx 0.28.1, psycopg 3.3.6, psycopg-pool 3.3.3, PostgreSQL 18.6, Caddy 2.11.7, llama.cpp build b11433 (recipe and guard), Node.js 24.21.0, Next.js 16.4.0, React 19.3.0, TypeScript 7.0.2, Vitest 5.0.3, Playwright 1.63.0. Node.js 26 enters LTS on 2026-10-28; move to it in a release after that. Pinned versions go in `pyproject.toml`, `web/package.json` with its lockfile, `compose.yml` (images pinned by tag and digest) and the worker recipes. Upgrading means changing those pins in a release.

Five runtime Python dependencies: starlette, uvicorn, httpx, psycopg, psycopg-pool. Tests add pytest and pytest-asyncio.

Three runtime Node dependencies for `web`: next, react, react-dom. Development adds TypeScript, Vitest, Testing Library and Playwright.

## Repository layout

```
compose.yml               server stack
Caddyfile
Dockerfile                app image (Python API)
pyproject.toml
app/
  main.py                 routes and startup
  db.py                   Postgres connection pool, migrations, queries
  schema/001_init.sql     numbered SQL migrations, recorded in schema_migrations
  auth.py                 staff login, student join, sessions, roles
  chat.py                 the turn pipeline: store, guard, route, stream
  guard.py                Qwen3Guard call, verdict parsing, category mapping
  workers.py              registry, heartbeat, routing
  live.py                 LISTEN/NOTIFY fan-out to SSE clients
  admin.py                backup, restore (pg_dump, pg_restore), retention, export, audit
web/
  Dockerfile              web image, Next.js standalone output on Node
  package.json            pinned Next.js, React and test tools, with lockfile
  next.config.ts          output: "standalone"
  app/page.tsx            / student chat
  app/teach/page.tsx      /teach teacher console
  app/admin/page.tsx      /admin tech teacher
  lib/api.ts              JSON API calls with the CSRF header
  tests/                  Vitest and Testing Library component and page tests
  e2e/                    Playwright smoke against the compose stack
worker/
  agent.py                auth proxy + heartbeat
  Dockerfile              agent image
  compose.yml             agent only, pointed at an existing model server
  recipes/                llama-server compose (nvidia, cpu), Ollama notes
tests/
docs/
LICENSES.md               every component and default model with its licence
```

## Components

### app

uvicorn, one worker to start. Live events go through Postgres: the app issues `NOTIFY` on each stored message, flag, join and state change, and each process `LISTEN`s and fans the events out to its own SSE clients. A second process sees every event, so the app can add workers if one can't keep up.

The app serves only the JSON API under `/api/` and `/healthz`.

### web

The Next.js app in `web/` (ADR-0016). It holds no data and no secrets. Pages are client components that call the app's JSON API. Server-side rendering features stay minimal, and no server action talks to the database. Three web surfaces:

- `/` student chat. Join, model picker, conversation list, chat.
- `/teach` teacher console. Session controls, live class grid, transcripts, flags, usage summary.
- `/admin` tech teacher. Workers and join command, models on/off, guard actions, self-harm message, retention, staff accounts, backup, audit log.

Live updates come from the app over Server-Sent Events, read in the browser with `EventSource`. SSE goes one way, works through Caddy with no extra configuration, and reconnects on its own.

### guard

A second llama-server container on the server's internal compose network, CPU only, with `-np` slots so several checks run at once. The app calls it with the Qwen3Guard prompt format and parses the reply: `Safety: Safe|Unsafe|Controversial`, then `Categories:`, plus a `Refusal:` line on reply checks. Prompt and reply checks use different category lists (Jailbreak is prompt-only). The embedded chat template is checked against the official one in a test, because Qwen3 GGUFs have shipped with template problems. Weights are downloaded once into a volume at install.

### worker

A worker is any machine running an OpenAI-compatible model server plus the agent (ADR-0014). The project doesn't dictate how the model server runs.

The agent is a small Starlette app in its own Docker image (same dependencies as the server app). It does two jobs:

- Auth proxy. It publishes the worker's only LAN port, requires the worker's API key on every request, and forwards `/v1/chat/completions` and `/v1/models` to the model server, streaming. The model server listens only on localhost or a Docker network. This gives every backend the same auth, including Ollama, which has none of its own.
- Heartbeat. Every 15 seconds it reads `/v1/models` from the model server and POSTs to the server's `/api/workers/heartbeat` with the join token, its address, its API key, the models it offers and its capacity (`MAX_CONCURRENT`, default 4).

Configuration is four environment variables: `SERVER_URL`, `JOIN_TOKEN`, `BACKEND_URL` and optionally `MAX_CONCURRENT`. The agent generates its API key on first start and keeps it in a volume. The join command the admin console shows is one line that sets these and runs `docker compose -f worker/compose.yml up -d`, in bash and PowerShell forms.

Recipes for the model server, in `worker/recipes/`:

- llama-server in Docker: `nvidia` profile with `gpus: all` (Windows needs Docker Desktop on WSL2 and a current NVIDIA driver on Windows itself), `cpu` profile for anything else.
- Ollama installed natively: uses the GPU directly on Windows, Linux and the Mac (Metal). Set `OLLAMA_NUM_PARALLEL` to match `MAX_CONCURRENT`.

On the Mac, development uses native Ollama or llama-server with Metal behind the agent, which is faster than a CPU container.

Busyness: the server counts its own in-flight requests per worker and routes to the worker with the most spare capacity. That works for every backend, where llama-server's `/slots` would not.

## Turn pipeline

Every student message goes through `chat.py`:

1. Check the student's session is open, not paused, and under the message limit. Refuse if not.
2. Store the message.
3. Guard the prompt. Store the verdict. If the category action blocks, store the block, publish to the teacher, and return the block text: the school's message for self-harm, a plain explanation otherwise. Flag if the action flags.
4. Pick a live worker serving the chosen model with the most spare capacity (its `MAX_CONCURRENT` minus requests in flight). If none, return a "no model available" message and store that.
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
- The server sends the worker's API key on every model request. A student device that reaches the agent's port without the key is refused, and the model server itself is not on the LAN (acceptance 5). The network docs add a firewall rule allowing only the server's address.
- Server-to-worker traffic is plain HTTP on the school LAN. Sharing GPUs between schools (vision, later increment) needs that link encrypted, for example a WireGuard tunnel between the sites. Nothing else in the design assumes the worker is on the same network.
- Model requests already carry no identity: the app sends only the class instructions and the conversation text, never a student name, class name, school or user field. Keep it that way, because it is what lets a school's server anonymise traffic to another school's GPUs. Cross-school sharing adds a redaction step (for example Presidio) on requests bound for a remote worker.
- A worker missing three heartbeats (45 seconds) is marked down and gets no requests (R2.4: within a minute). A request that fails to connect marks the worker down at once and retries on another worker serving the same model.

## Data

PostgreSQL in the `db` service, data in the `pgdata` volume (ADR-0015). Credentials are in `.env`, generated at install. Tables:

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
| `workers` | ID, address, API key, models, capacity, last heartbeat, removed. |
| `models` | Model name, enabled. |
| `settings` | Key/value: category actions, self-harm message, retention days (30), join token. |
| `audit` | Who, what, when, for staff actions (R7.4). |
| `schema_migrations` | Applied migration numbers and when. |

Migrations are numbered SQL files in `app/schema/`, applied at startup and recorded in `schema_migrations`.

Retention: an hourly task deletes conversations, messages and flags older than the retention setting, plus closed lesson sessions with no remaining messages (R7.2).

Backup: `docker compose exec db pg_dump -Fc` writes a custom-format dump while the app keeps running. Restore stops the app, empties the database, runs `pg_restore` into it, and starts the app again. The admin page download runs the same dump. Model weights are not in the backup; they download again.

Export one student (R7.3): JSON of their conversations, messages and flags. Delete one student removes all of those rows. Both go in the audit log.

## Teacher console

The console is a plain web page, but the PRD expects a non-technical teacher to enjoy using it. Requirements on the front end:

- A session bar at the top: class name, the join code in large type, open/pause/close buttons, time since start.
- A grid of student cards: name, latest message, message count, a red border and badge when flagged. Cards update over SSE without the page jumping.
- A usage strip (D1): students active in the last five minutes, total messages, messages per student, model use, flags by category.
- Clicking a card opens the transcript beside the grid, with blocked messages and flag reasons shown inline and a "Mark reviewed" button.
- Works on a laptop and an iPad. Keyboard accessible. Colour is never the only signal for a flag.

Live view: the app publishes an event per stored message, flag, join and state change with `NOTIFY`. Each app process `LISTEN`s and passes the events to its SSE clients. Each teacher console holds one SSE connection filtered to its session. On reconnect it fetches current state over JSON, then resumes the stream.

## Failure handling

| Failure | Behaviour |
| --- | --- |
| No live worker for a model | Student sees "this model isn't available right now". Admin console shows the worker down. |
| Worker drops mid-reply | Partial reply stored and marked as an error. Student sees what was shown plus a retry message. |
| Guard container down | Fail closed. Students see "chat is unavailable", and the teacher and admin consoles show a red banner. No unchecked text goes either way. |
| Database write fails | The turn is refused. Nothing is sent to a model that isn't stored. |
| Database down | Fail closed. Turns are refused, students see "chat is unavailable", and `/healthz` reports the database down. The app reconnects when it is back. |
| Server restarts | Students and teachers stay signed in (cookies are in the database). Open SSE streams reconnect. |

## Network and TLS

- Caddy publishes 443 and redirects 80. `SERVER_NAME` in `.env` sets the hostname (a local DNS name or the IP address).
- `tls internal` creates a local CA in the `caddy_data` volume. The admin page links to download the root certificate, and `docs/install.md` shows how to trust it on Windows, macOS, iOS, Android and ChromeOS. Until a device trusts it, the browser shows a warning (PRD risk). iOS needs the profile installed and then enabled under Certificate Trust Settings. Managed Chromebooks take it through the Google Admin console. Android trust in Chrome is unverified and is a pilot check.
- Caddy routes `/api/*`, including the SSE streams, and `/healthz` to `app`, and everything else to `web`. Both share one origin, so the session cookie and the CSRF header work unchanged. Browsers never call the app on another origin, and neither `app` nor `web` publishes a port.
- On the Mac in development, `http://localhost` skips TLS.
- The guard and the database are on the internal compose network and publish no ports.

## Tests

- Unit tests with pytest for verdict parsing, category mapping, routing choice, rate limits, retention and migrations.
- API tests drive the Starlette app in-process with httpx's ASGI transport against a real Postgres. The test session starts one Postgres container in Docker and creates a disposable database for each test run. There is no in-process database. A fake worker and a fake guard are small Starlette apps that stream canned replies and verdicts, so tests need no models.
- One smoke test boots the real compose stack with an agent in front of a local model server running Gemma 4 E2B, joins a student, and checks a reply arrives and appears in the teacher stream. Run on demand on the Mac, not in every test run.
- Guard check: a fixed list of test prompts per R6.3 category run against the real guard (acceptance 7), on demand.
- Load test: `tests/load.py` simulates 30 students over the HTTP API and reports lost messages and time to first segment (acceptance 12). Run on the Windows GPU machine.
- Front end: component and page tests with Vitest and Testing Library, with the API mocked. A Playwright smoke in `web/e2e/` runs against the compose stack with the fake worker and fake guard: a student joins and chats, and the teacher sees it. Devices are still checked by hand against `docs/manual-tests.md` on a phone, a tablet and a laptop.

## Operations

- Logs: the app logs JSON lines to stdout. Docker keeps them. Chat text never goes to logs, only to the database.
- Health: `/healthz` reports database, guard and worker counts. The admin page shows the same.
- Upgrade: `git pull && docker compose up -d --build`, which rebuilds both the `app` and `web` images. Migrations run on start. Back up first with `pg_dump`; the docs say so.
- Postgres major upgrades are a dump and restore into the new version, done in a release with its own instructions.
- No telemetry leaves the server.

## Decisions

Recorded one per file in the ADR register, `docs/adr/README.md`. Add an ADR for any new decision or change to one.
