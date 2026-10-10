# Architecture

Status: draft for PRD 00 (lab pilot), 2026-10-06. Living document: update it when a decision changes.

## Constraints from the PRD

- One server machine and any number of GPU machines, each running Docker Engine (D3, ADR-0003). Windows 11 hosts are expected, with Docker Engine inside WSL2. Development happens on a Mac.
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
| Database | PostgreSQL 18, official `postgres` image (ADR-0005) | Several app processes can share it. `LISTEN`/`NOTIFY` carries live updates. Easy to query for reports. |
| Database driver | psycopg 3 with `psycopg-pool` (async pool) | LGPL-3.0. Plain SQL, no ORM. |
| Password hashing | stdlib `hashlib.scrypt` | No extra dependency. |
| Front end | Next.js (App Router, TypeScript) with React and plain CSS modules, built with `output: "standalone"` and run on Node.js 24 LTS in its own `web` image (ADR-0006) | Easier to build a pleasant teacher console, with component tests. MIT licensed. Holds no data and no secrets. |
| TLS | Caddy 2.11 with `tls internal` | Automatic local certificate authority, no internet needed. |
| Model server on workers | Any OpenAI-compatible server (ADR-0007). Documented recipes: llama.cpp `llama-server` in Docker, and Ollama installed natively | The school runs GPUs however suits its machines. The agent gives every backend the same interface and the same auth. |
| Guard model | Qwen3Guard-Gen-0.6B, community GGUF `mradermacher/Qwen3Guard-Gen-0.6B-GGUF` Q8_0, pinned by file hash, on a CPU llama-server | Apache-2.0, small enough for CPU, covers the R6.3 categories. Qwen publishes no GGUF, so the hash pin is how we know which weights we run. Step 5 of the implementation plan, a bake-off against Qwen3Guard-Gen-4B and Shieldstral-1.0-3B, confirms or replaces this choice. |
| Suggested chat models in the recipes | Gemma 4: 12B-it on 16 GB, E4B-it on 8 GB, E2B-it on CPU | Apache-2.0 with official GGUFs. Qwen3 is superseded by Qwen3.5, which has only community GGUFs at the sizes we need. |

Versions at time of writing: Python 3.14.8, Starlette 1.7.0, uvicorn 0.54.0, httpx 0.28.1, psycopg 3.3.6, psycopg-pool 3.3.3, PostgreSQL 18.6, Caddy 2.11.7, llama.cpp build b11434 (recipe and guard; b11433 has no published image), Node.js 24.21.0, Next.js 16.4.0, React 19.3.0, TypeScript 7.0.2, Vitest 5.0.3, Playwright 1.63.0. Node.js 26 enters LTS on 2026-10-28; move to it in a release after that. Pinned versions go in `pyproject.toml`, `web/package.json` with its lockfile, `compose.yml` (images pinned by tag and digest) and the worker recipes. Upgrading means changing those pins in a release.

Five runtime Python dependencies: starlette, uvicorn, httpx, psycopg (with its binary wheel, which bundles libpq) and psycopg-pool. Tests add pytest 9.1.1 and pytest-asyncio 1.4.0. `uv` (0.12.13 in the app image) installs them from `uv.lock`.

Three runtime Node dependencies for `web`: next, react, react-dom. Development adds TypeScript, Vitest, Testing Library and Playwright.

## Repository layout

```
compose.yml               server stack
Caddyfile
Dockerfile                app image (Python API)
.dockerignore             allowlist: only pyproject.toml, uv.lock and app/ reach the app image
pyproject.toml            pinned Python dependencies, with uv.lock
scripts/init-env.sh       writes .env once: generated database credentials, and with a server name the HTTPS settings
compose.https.yml         adds port 443 for a school server; .env turns it on through COMPOSE_FILE
scripts/backup.sh         pg_dump of the database to a file
scripts/restore.sh        replaces the database with a backup, in one transaction
app/
  main.py                 routes and startup (create_app, and from_env for uvicorn --factory)
  db.py                   Postgres connection pool, migrations, queries
  schema/001_init.sql     numbered SQL migrations, recorded in schema_migrations
  auth.py                 staff login, student join, sessions, roles
  chat.py                 the turn pipeline: store, guard, route, stream
  guard.py                Qwen3Guard call, verdict parsing
  workers.py              registry, heartbeat, routing
  live.py                 LISTEN/NOTIFY fan-out to SSE clients
  records.py              retention, export and delete one student, audit log, backup download
web/
  Dockerfile              web image, Next.js standalone output on Node
  package.json            pinned Next.js, React and test tools, with lockfile
  next.config.ts          output: "standalone"
  app/page.tsx            / student chat
  app/teach/page.tsx      /teach teacher console
  app/teach/slips/page.tsx  /teach/slips printable code slips
  app/admin/page.tsx      /admin tech teacher
  lib/api.ts              JSON API calls with the CSRF header
  vitest.config.mts       Vitest with jsdom
  tests/                  Vitest and Testing Library component and page tests
  e2e/                    Playwright smoke against the compose stack
worker/
  agent.py                auth proxy + heartbeat
  Dockerfile              agent image
  compose.yml             agent only, pointed at an existing model server
  recipes/                llama-server compose (nvidia, cpu), Ollama notes
tests/
  conftest.py             test Postgres container, disposable database, ASGI client
  fakes.py                fake worker and fake guard (small Starlette apps)
docs/
LICENSES.md               every component and default model with its licence
```

## Components

### app

uvicorn, one worker to start. Live events go through Postgres: the app issues `NOTIFY` on each stored message, flag, join and state change, and each process `LISTEN`s and fans the events out to its own SSE clients. A second process sees every event, so the app can add workers if one can't keep up.

The app serves only the JSON API under `/api/` and `/healthz`.

### web

The Next.js app in `web/` (ADR-0006). It holds no data and no secrets. Pages are client components that call the app's JSON API. Server-side rendering features stay minimal, and no server action talks to the database. Three web surfaces:

- `/` student chat. Join, a header with the student's name and code, model picker, conversation list, chat.
- `/teach` teacher console. Models on and off, session controls, code roster, printable slips, live class grid, transcripts, flags, usage summary.
- `/admin` tech teacher. Workers and join command, models on/off, category actions and messages, retention, staff accounts, backup, audit log.

Live updates come from the app over Server-Sent Events, read in the browser with `EventSource`. SSE goes one way, works through Caddy with no extra configuration, and reconnects on its own.

### guard

A second llama-server container on the server's internal compose network, CPU only, with `-np` slots so several checks run at once. The app calls it with the Qwen3Guard prompt format and parses the reply: `Safety: Safe|Unsafe|Controversial`, then `Categories:`, plus a `Refusal:` line on reply checks. Prompt and reply checks use different category lists (Jailbreak is prompt-only). The embedded chat template is checked against the official one in a test, because Qwen3 GGUFs have shipped with template problems. Weights are downloaded once into a volume at install.

### worker

A worker is any machine running an OpenAI-compatible model server plus the agent (ADR-0007). The project doesn't dictate how the model server runs.

The agent is `worker/agent.py`, a small Starlette app in its own Docker image. `worker/requirements.txt` pins every package it installs at the version in `uv.lock`, so the image builds from `worker/` alone and runs what the tests ran; a test fails if the two drift. The agent's tests run with the app's (`uv run pytest` collects `worker/tests`). It does two jobs:

- Auth proxy. It publishes the worker's only LAN port, 8081, requires `Authorization: Bearer <API key>` on every request (compared in constant time, before the body is read), and forwards `/v1/chat/completions` and `/v1/models` to the model server, streaming chunk by chunk. The worker's key is never passed on to the model server. The model server listens only on localhost or a Docker network. This gives every backend the same auth, including Ollama, which has none of its own. A model server that cannot be reached gives 502, or 504 on a timeout. Once a reply has started, a model server failure or a gap of more than 120 seconds between chunks ends the stream early, and a client that goes away closes the request to the model server.
- Heartbeat. Every 15 seconds it reads `/v1/models` from the model server and POSTs to the server's `/api/workers/heartbeat` with the join token as a bearer token, its worker ID, its API key, the models it offers and its capacity (`MAX_CONCURRENT`, 1 to 64, default 4). If the model server does not return a valid model list, the agent skips that heartbeat, so the worker goes down rather than staying up with models it cannot serve.

Configuration is environment variables: `SERVER_URL`, `JOIN_TOKEN`, `BACKEND_URL` (default in `worker/compose.yml`: Ollama on the same machine), optionally `MAX_CONCURRENT`, optionally `WORKER_URL` (below), and `SERVER_CA`, the server's root certificate for checking `SERVER_URL` over HTTPS (`worker/compose.yml` mounts `worker/server-ca.crt` for it; the agent uses the system's trusted roots when the file is missing). On first start the agent generates a worker ID (a UUID) and a 32-byte API key and keeps them in `/data/identity.json` in a volume, readable by the agent only. A file that exists but cannot be read stops the agent rather than making a new identity, because a new ID would sidestep a removal.

The worker's address is not configured. The server takes it from where the heartbeat came from: the app publishes no port, so every request reaches it through Caddy, which overwrites `X-Forwarded-For` with the client's address. The server uses that header when it holds exactly one valid IP address, otherwise the socket peer, and calls the agent at `http://<address>:8081`. When that address is not one the server can reach, the agent sends `WORKER_URL` instead, which must be `http://<host>:8081` with no path, user or query, and must not resolve to a loopback, link-local, multicast or unspecified address. The usual case is a worker on the server's own machine: `WORKER_URL=http://host.docker.internal:8081`. A heartbeat for a known worker ID with a different API key is refused, since the agent keeps its ID and key together. Before a new worker, or a worker whose address changed, is stored, the server calls `/v1/models` at that address with the worker's key. That proves the server can reach it, and stops a token holder from pointing its entry at another worker's agent, which would refuse a key that is not its own. The join command the admin console shows is one line that sets these and runs `docker compose -f worker/compose.yml up -d --build`, in bash and PowerShell forms.

Recipes for the model server, in `worker/recipes/`:

- llama-server in Docker (`worker/recipes/compose.yml`, used together with `worker/compose.yml`): `nvidia` profile with an NVIDIA device reservation (on Windows: Docker Engine and the NVIDIA Container Toolkit inside WSL2, and a current NVIDIA driver on Windows itself), `cpu` profile for anything else.
- Ollama installed natively: uses the GPU directly on Windows, Linux and the Mac (Metal). Set `OLLAMA_NUM_PARALLEL` to match `MAX_CONCURRENT`. On Linux and WSL2 Ollama listens on the Docker bridge address, never `0.0.0.0`. `worker/recipes/README.md` has the commands.

On the Mac, development uses native Ollama or llama-server with Metal behind the agent, which is faster than a CPU container.

Busyness: the server counts its own in-flight requests per worker and routes to the worker with the most spare capacity. That works for every backend, where llama-server's `/slots` would not. Routing (`Router` in `app/workers.py`) picks, among live workers offering an enabled model, the one with the largest capacity minus requests in flight, the lowest worker ID breaking a tie, and none at all once every worker is full. It counts in-flight requests in the app process, which is correct while the app runs one uvicorn process; running a second process needs a shared counter first.

## Turn pipeline

Every student message goes through `chat.py`:

1. Check the student's session is open, not paused, and under the message limit. Refuse if not.
2. Store the message.
3. Guard the prompt. Store the verdict. If the category action blocks, store the block, publish to the teacher, and return the block text, which is the category's configured message. Flag if the action flags.
4. Pick a live worker serving the chosen model with the most spare capacity (its `MAX_CONCURRENT` minus requests in flight). If none, return a "no model available" message and store that.
5. Send the class instructions, the conversation so far and the message to the worker, streaming.
6. Release the reply in segments. Buffer tokens to the end of a sentence or 300 characters, guard the reply so far together with the prompt, then send the segment to the student. If a segment trips the guard, stop the generation, store everything generated, and replace what follows with the block text. Text already shown stays shown, and the flag records that.
7. Store the full reply and publish the turn to the teacher's live view.

A turn is stored before anything leaves the server. A failure at any step is stored with the turn, so the teacher can see it.

Step 6 is how R4.3 (streamed reply) and R6.1 (reply checked first) both hold. The cost is a guard call per segment on CPU. The load test (acceptance 12) measures it. If the guard is the bottleneck, segments get longer first, and the guard moves to a GPU worker only after that, keeping the CPU guard as the fallback for R6.2.

Categories come from the guard model, and each one is configured the same way: an action and a student-facing message, both set by the admin (R6.3-R6.5). Qwen3Guard's "Controversial" verdict counts as unsafe for students. Categories the admin does not act on are stored and ignored.

## Identity and access

Staff:

- Roles `admin` and `teacher`. An admin can also teach.
- Username and password, scrypt-hashed (N=2^17, r=8, p=1, stored as `scrypt$N$r$p$salt$key` so the cost can be raised later). Hashing runs in a worker thread, at most two at a time. A login for an unknown username still runs one hash, so timing does not reveal which usernames exist. Usernames are lower-cased, 1 to 64 characters from `a-z 0-9 . _ -`; passwords are 10 to 256 characters. Admins create teacher and admin accounts on `/admin`; each creation writes a `staff.create` audit row. There is no staff delete or password change yet.
- First run: while no admin exists, each app start makes sure a 12-character setup code is stored in `settings` and prints it to the log, grouped in fours (`ABCD-EFGH-JKLM`). A visitor to `/admin` or `/teach` with no session is sent to `/setup` while setup is needed, otherwise to `/login`. `/setup` asks for the code (spaces, dashes and case are ignored) and creates the admin, deletes the code and signs the admin in. Startup and setup share one advisory lock, so setup succeeds exactly once even under concurrent attempts, and a restart can never bring the code back. This stops whoever on the LAN reaches the page first from taking it over.
- Login and setup attempts are limited to 10 a minute per client address (429 after that). The limiter lives in the app process's memory. The client address comes from `X-Forwarded-For`, which is trustworthy only because Caddy (with no `trusted_proxies`) replaces any client-supplied value with the real peer and the app publishes no port; revisit this if anything else is put in front of the app.
- API: `GET /api/me`, `GET /api/setup` (whether setup is needed), `POST /api/setup`, `POST /api/login`, `POST /api/logout` (204 and both cookies cleared, with or without a session; like any signed-in request it needs the session's CSRF token), and admin-only `GET /api/staff` and `POST /api/staff`. Login and setup answer with the same `{"staff": {...}}` shape as `/api/me`. Request bodies over 64 KiB get 413 (Caddy refuses anything over 1 MB before it reaches the app). Errors are `{"error": "<code>"}`; no session is 401, the wrong role is 403, and database unavailability on any route is 503.

Students (D1):

- The teacher starts a lesson session for a class and generates a batch of student codes, 30 by default. Each code is six digits, unique among open sessions, and stored as an unbound `students` row. The teacher can generate more during the session.
- "Print slips" opens `/teach/slips`, a print-friendly page that lays the unused codes out as A4 cut-out slips, one per slip, with the class name and the chat web address on each. The browser prints it with print CSS; there is no PDF library.
- A student enters a code and a name. If the code is unbound, the app binds it to that name, records the time and sets a session cookie. If the code is already bound, the app signs the device in as that student under the bound name and ignores the name typed. That is how a student resumes after closing the tab or on another device.
- The student can't change the bound name. The teacher can rename a student, unbind a code or remove it. Unbinding marks the student removed and adds a fresh unbound row with the same code, so earlier conversations stay with the old name. Removing retires the code. Either way the old cookies stop working.
- Every student page shows the bound name and code in a header that stays visible (R4.7).
- Opening and closing: a session is `open`, `paused` or `closed`. Pause keeps students signed in but stops sending (R4.6). Close ends the session, all its codes stop working and student cookies expire. A closed session's transcripts stay readable by the teacher.
- A student record lasts one session. "Past conversations in this class" (R4.4) means past conversations in this session. Linking a student across lessons needs accounts, which are a later increment.

How step 3 builds this (`app/classes.py`, `app/students.py`):

- Teachers see and change only their own classes; an admin may act on any. A class, lesson session or student the caller may not see answers 404, so its existence is not confirmed. A class has at most one lesson session that is not closed (`003_lesson_sessions.sql`), so starting a second gives 409.
- Codes are drawn from `secrets.randbelow(10**6)` and checked against every code held by a not-removed student in a session that is not closed, and every code this session has ever used, so a retired code (a lost slip) never works again in its session. Code generation takes an advisory lock, so two sessions generating at once cannot both pick the same code. One call makes 1 to 200 codes (30 by default) and a session holds at most 500. Unbinding keeps the code live, so it needs no lock.
- Joining locks the student row and holds a share lock on its lesson session, so two devices binding one code at once end up as the same student under one name, and a close, unbind or remove either finishes first (the join then fails) or waits for the join and then ends the cookie it created. Joining over a live session on the device needs that session's CSRF token. A code from a closed session, a removed code and a code never issued all answer the same 404. Class names, instructions and student names refuse control characters. Student names are trimmed, inner spaces collapsed, 1 to 60 characters; control, private-use and unassigned characters are refused, while format characters such as the zero-width non-joiner are kept because some scripts need them. Join attempts are limited to 30 a minute per client address (a whole class behind one shared address would hit this; see bead cai-1ir.16).
- One `session` cookie per device. A device is either a staff device or a student device: joining ends a staff session on that device and signing in ends a student one. A student cookie works only while the student is bound and not removed and the lesson session is not closed; this is checked on every lookup, and close, unbind and remove also delete the student's cookie rows.
- State changes are one conditional update, so closed is final (409 after). Pause keeps students signed in; `require_student(request, sending=True)` refuses with 409 while paused, for step 4's send endpoint to call.
- API: `GET/POST /api/classes`, `PATCH /api/classes/{id}`, `POST /api/classes/{id}/sessions` (start, with `count`), `GET /api/sessions/{id}` (state and roster), `POST /api/sessions/{id}/state`, `POST /api/sessions/{id}/codes`, `PATCH /api/students/{id}` (rename), `POST /api/students/{id}/unbind`, `POST /api/students/{id}/remove`, `POST /api/join`, `GET /api/student/me`.
- Audit rows so far: `staff.create`, `class.create`, `class.edit`, `session.start`, `session.open`, `session.paused`, `session.closed`, `session.codes`, `student.rename`, `student.unbind`, `student.remove`, and from steps 2 and 8 `model.enable`, `model.disable`, `worker.remove`, `workers.rotate_join_token`, `retention.set`, `student.export`, `student.delete`, `backup.download`. The detail holds ids and names, never codes.
- Pages: `/teach` lists classes with create and edit, "Start lesson" with a code count, then the session bar (state, time since start, pause or resume, close, "Print slips", generate more) and the roster with rename, unbind and remove, refreshed by a button until step 7. `/teach/slips?session=<id>` prints three slips across on A4. `/` is the join form, then the student header and a paused notice; it re-checks `/api/student/me` every 15 seconds, so a pause or close shows without a reload. An admin on `/teach` sees every class, with the owning teacher named.

Cookies: the `session` cookie is a random 32-byte token, stored in the `auth_sessions` table as its sha256 hash, `HttpOnly`, `SameSite=Lax`, and `Secure` whenever the request came over HTTPS (Caddy's `X-Forwarded-Proto`). On the plain-HTTP development stack it is not Secure, so Safari keeps it; from step 9 every request is HTTPS. Staff sessions last 12 hours. Each login issues a new token, never one the client supplied, and deletes expired rows. Signing in on a device that already has a live session replaces it, and like any signed-in request needs that session's CSRF token. Sign out only goes to `/login` once the server confirms it; otherwise the page says the person is still signed in.

CSRF: every state-changing request under `/api/` needs an `X-CSRF-Token` header equal to the `csrf_token` cookie (readable by the page, not HttpOnly). A cross-site page can neither read the cookie nor add the header without a CORS preflight, which the app never grants. Each session also has its own token in `auth_sessions.csrf_token`, and a signed-in request must present that one, so a cookie planted by someone else is useless. The middleware in `app/auth.py` checks the header first (so an unknown path answers 403 rather than 404 without it) and keeps the cookie right: when a response does not set `csrf_token` itself, a request with no `csrf_token` cookie gets the session's token (or a random one before sign-in), and on routes that load the session (such as `/api/me`) a cookie that is not the session's is replaced with the session's. Bearer-token paths get no cookie. `web/lib/api.ts` sends the header on every request other than GET and HEAD, and fetches `GET /api/me` first if it has no token yet. Paths authenticated by a bearer token and never by cookie are listed in `CSRF_EXEMPT` (the worker heartbeat); a request is never exempted just because it has no cookies. Student sessions (step 3) also need a `csrf_token`, since the column is required.

Student codes can be guessed: 30 tries per minute per IP address on the join endpoint. With 30 codes open, one address needs around 18 hours on average to hit one. A guessed bound code shows the real student's name and code in the header, so the teacher can spot it.

## Worker trust

- The admin console shows the join token and can rotate it. Rotating it drops every worker until each is rejoined. The token is stored in `settings` under `join_token` with a generation number that rotation increments; each worker row records the generation its last heartbeat carried, and a worker is live only while that matches. A heartbeat takes a share lock on the token row and rotation an update lock, so a heartbeat with the old token either commits before the rotation (and is then stale) or waits and is rejected.
- A heartbeat without the current token is rejected, before the server makes any call to the worker. A removed worker is put on a deny list by worker ID (`workers.removed`), so its heartbeats are rejected even with the token. A heartbeat never clears `removed`, and its upsert skips a removed row, so a heartbeat racing a removal cannot bring the worker back. Removing a worker leaves the other workers serving. A machine that deletes its identity volume comes back with a new ID; to stop that too, the admin also rotates the token when removing it, at the cost of rejoining every other worker.
- Models a worker offers for the first time are added disabled. Staff enable them; a model that disappears and comes back keeps its setting.
- The server sends the worker's API key on every model request. A student device that reaches the agent's port without the key is refused, and the model server itself is not on the LAN (acceptance 5). The network docs add a firewall rule allowing only the server's address.
- Server-to-worker traffic is plain HTTP on the school LAN. Sharing GPUs between schools (vision, later increment) needs that link encrypted, for example a WireGuard tunnel between the sites. Nothing else in the design assumes the worker is on the same network.
- Model requests already carry no identity (ADR-0010): the app sends only the class instructions and the conversation text, never a student name, class name, school or user field. Keep it that way, because it is what lets a school's server anonymise traffic to another school's GPUs. Cross-school sharing adds a redaction step (for example Presidio) on requests bound for a remote worker.
- A worker missing three heartbeats (45 seconds) is marked down and gets no requests (R2.4: within a minute). A request that fails to connect marks the worker down at once and retries on another worker serving the same model.

## Data

PostgreSQL in the `db` service, data in the `pgdata` volume (ADR-0005). Credentials are in `.env`, generated at install. Tables:

| Table | Holds |
| --- | --- |
| `staff` | Staff accounts and roles. |
| `auth_sessions` | Cookie tokens for staff and students. |
| `classes` | Name, instructions (system prompt), message limit, owning teacher. |
| `lesson_sessions` | Class, state, opened and closed times. |
| `students` | Lesson session, code, bound name, bound at (empty until first use), removed flag. |
| `conversations` | Student, model, started time. |
| `messages` | Conversation, role, text, time, status (ok, blocked, error), worker used. |
| `flags` | Message, category, action taken, reviewed by, reviewed at. |
| `workers` | ID, address, API key, models, capacity, last heartbeat, removed, join-token generation of the last heartbeat. |
| `models` | Model name, enabled. |
| `settings` | Key/value: category actions and messages, retention days (30), join token. |
| `audit` | Who, what, when, for staff actions (R7.4). |
| `schema_migrations` | Applied migration numbers, a sha256 of each file, and when. |

Migrations are numbered SQL files in `app/schema/`, named `NNN_description.sql`, applied at startup and recorded in `schema_migrations` with a sha256 of each file. The runner (`app/db.py`) applies every pending file in one transaction under a Postgres advisory lock, so a failure applies nothing and app processes starting together apply each file once. It refuses to start if a file name does not match the pattern, two files share a number, an applied file has changed, a new file is numbered below the highest applied one, or the database records a migration the code lacks. Migration files cannot contain transaction control or statements that refuse to run in a transaction, such as `CREATE INDEX CONCURRENTLY`. Take the next free number when adding one.

Schema notes from `001_init.sql`:

- `students` keeps one row per code. A code is unique within a lesson session among rows that are not removed, which is what lets unbinding add a fresh row with the same code. Uniqueness across open sessions is checked when codes are generated (step 3).
- `auth_sessions` stores the sha256 of the cookie token, never the token, with exactly one of `staff_id` and `student_id` set and an `expires_at`. `002_staff_auth.sql` adds `csrf_token`, the session's own CSRF token, which every row needs.
- `003_lesson_sessions.sql` allows at most one lesson session per class that is not closed.
- Deleting a student cascades to their conversations, messages, flags and cookie sessions, and deleting a lesson session cascades to its students, so delete-one-student is a single delete, and retention deletes sessions and conversations without touching their children one by one. `messages.worker_id` and `conversations.model` are plain text, so removing a worker or a model never touches transcripts. Deleting a staff account keeps audit rows (the username is copied into each) and flag reviews (`reviewed_by` becomes empty).

Retention (R7.2), `app/records.py`: the app runs it at startup and then hourly, in one transaction under an advisory lock. It never waits for a lock. If another run holds the advisory lock, this run is skipped. Rows another transaction holds (a message being written, a roster edit, a flag being reviewed, a cookie session in use) are skipped with `FOR UPDATE SKIP LOCKED` and deleted on a later run, and that includes every row a delete would cascade to: a message goes only if its flags could be locked, and a session only if it, its students, their conversations and their cookie sessions could all be locked. Any other lock it would have to wait for (a table lock, for example) fails the run after 200 ms (`lock_timeout`): the run rolls back, is logged, and the next run tries again. So retention cannot deadlock with anything. What was locked is checked again before deleting. The period is `settings.retention_days`, 30 when unset; a stored value that is not a whole number from 1 to 3650 is logged as an error and 30 is used. With cutoff = now minus the period, it deletes messages created before the cutoff (their flags go with them), then conversations started before the cutoff that have no messages left, then closed lesson sessions closed before the cutoff with no message in any of their conversations (their students and cookie sessions go with them). Open and paused sessions are never deleted. Deletion is per message, so a conversation that spans the cutoff keeps only its newer messages, and a flag on a deleted message goes whether or not it was reviewed. It logs counts only. A failed run is logged and the next one comes an hour later.

Backup: `scripts/backup.sh [file]` runs `docker compose exec db pg_dump -Fc` while the app keeps running and writes the dump readable by its owner only, renaming it into place only once `pg_dump` has succeeded. Restore: `scripts/restore.sh [--yes] <file>` first checks the file with `pg_restore --list` (a file that is not a dump changes nothing), asks for confirmation, stops the app, then inside the db container unpacks the dump to SQL and runs `DROP SCHEMA public CASCADE`, `CREATE SCHEMA public AUTHORIZATION pg_database_owner` (the owner a new database has, so later dumps restore the same way) and that SQL with `psql --single-transaction`, so a failure leaves the database as it was. It starts the app again either way, then waits up to a minute for it to answer `/healthz` and says if it does not, for example when the backup is newer than the code. Startup migrations bring an older dump up to date. Retention runs as the app starts, so messages in a restored backup that are older than the retention period are deleted straight away. The admin page download (`POST /api/admin/backup`, admin only, with the CSRF header so another site cannot start a dump, audited) runs the same `pg_dump -Fc` from the app container, which carries the PostgreSQL 18 client from the PGDG apt repository at the same version as the `postgres` image; the two move together. The client is pinned to an exact package version, so a fresh build fails once PGDG replaces that version; the fix is to bump both pins in one release. It writes the dump to a temporary file and serves it only once `pg_dump` has succeeded, so a failure is an error, never a truncated file. Model weights are not in the backup; they download again.

Export one student (R7.3): JSON of their conversations, messages and flags (`GET /api/admin/students/{id}/export`). Delete one student removes all of those rows (`DELETE /api/admin/students/{id}`). Both go in the audit log, naming the student by id and lesson session, never by code or by what they wrote. The retention period is set with `PUT /api/admin/retention` (1 to 3650 days, audited), `GET /api/admin/audit` lists the audit log newest first, 100 rows a page, and `GET /api/admin/students?q=` lists students newest first (100 at most, removed ones too, so their data can still be exported or deleted), matching `q` against name or code as typed. The admin page's Records section searches that list, downloads one student's export, and deletes a student after a confirmation that says how many messages go with them.

## Teacher console

The console is a plain web page, but the PRD expects a non-technical teacher to enjoy using it. Requirements on the front end:

- A session bar at the top: class name, open/pause/close buttons, time since start, a "Print slips" button and a control to generate more codes.
- A roster of codes: each code, whether it is used and the name bound to it, with rename, unbind and remove.
- A grid of student cards: name and code, latest message, message count, a red border and badge when flagged. Cards update over SSE without the page jumping.
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

- Caddy publishes 443 and redirects 80. `SERVER_NAME` in `.env` sets the hostname (a local DNS name or the IP address), and `BIND_ADDRESS` the address the ports are published on: `0.0.0.0` on a school server, `127.0.0.1` when unset. `scripts/init-env.sh <name>` writes both, plus `COMPOSE_FILE=compose.yml:compose.https.yml` so compose also publishes 443; development publishes only port 80. Compose passes Caddy `SITE_ADDRESS` (the name, or `:80` when there is none) and `SERVER_NAME`; the Caddyfile's `default_sni` makes Caddy present the server's certificate to browsers that send no name, which is what they do for an IP address. The certificate covers only `SERVER_NAME`.
- `tls internal` (the global `local_certs` option in `Caddyfile`) creates a local CA in the `caddy_data` volume. The root is valid for ten years. Until the admin page has a download link, `docker compose cp caddy:/data/caddy/pki/authorities/local/root.crt .` fetches the root certificate, and `docs/install.md` shows how to trust it on Windows, macOS, iOS, Android and ChromeOS. Until a device trusts it, the browser shows a warning (PRD risk). iOS needs the profile installed and then enabled under Certificate Trust Settings. Managed Chromebooks take it through the Google Admin console. Android trust in Chrome is unverified and is a pilot check.
- Caddy routes `/api/*`, including the SSE streams, and `/healthz` to `app`, and everything else to `web`. Both share one origin, so the session cookie and the CSRF header work unchanged. Browsers never call the app on another origin, and neither `app` nor `web` publishes a port.
- In development `SERVER_NAME` is unset: Caddy serves plain HTTP on `http://localhost`, published on 127.0.0.1 only. `HTTP_PORT` and `HTTPS_PORT` in the environment or `.env` pick other host ports, for a second stack on one machine or when a port is already taken.
- The guard and the database are on the internal compose network and publish no ports.

## Tests

- Unit tests with pytest for verdict parsing, routing choice, rate limits, retention and migrations.
- API tests drive the Starlette app in-process with httpx's ASGI transport against a real Postgres. The test session starts one Postgres container in Docker and creates a disposable database for each test run. There is no in-process database. A fake worker and a fake guard are small Starlette apps that stream canned replies and verdicts, so tests need no models.
- One smoke test boots the real compose stack with an agent in front of a local model server running Gemma 4 E2B, joins a student, and checks a reply arrives and appears in the teacher stream. Run on demand on the Mac, not in every test run.
- Guard check: a fixed list of test prompts per enabled category run against the real guard (acceptance 7), on demand.
- Load test: `tests/load.py` simulates 30 students over the HTTP API and reports lost messages and time to first segment (acceptance 12). Run on the Windows GPU machine.
- Front end: component and page tests with Vitest and Testing Library, with the API mocked. A Playwright smoke in `web/e2e/` runs against the compose stack with the fake worker and fake guard: a student joins and chats, and the teacher sees it. Devices are still checked by hand against `docs/manual-tests.md` on a phone, a tablet and a laptop.

## Operations

- Logs: the app logs JSON lines to stdout. Docker keeps them. Chat text never goes to logs, only to the database.
- Health: `/healthz` reports database, guard and worker counts. The admin page shows the same. So far it reports the database: 200 `{"status":"ok","db":"ok"}`, or 503 with `"db":"down"` when the database does not answer within two seconds.
- Telemetry: the web image sets `NEXT_TELEMETRY_DISABLED=1` for the build and at run time.
- Upgrade: `git pull && docker compose up -d --build`, which rebuilds both the `app` and `web` images. Migrations run on start. Back up first with `pg_dump`; the docs say so.
- Postgres major upgrades are a dump and restore into the new version, done in a release with its own instructions.
- No telemetry leaves the server.

## Decisions

Recorded one per file in the ADR register, `docs/adr/README.md`. Add an ADR for any new decision or change to one. Product decisions are in the PRD's Decisions section: student codes (D1), retention (D2) and flags (D6).
