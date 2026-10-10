# Implementation plan: PRD 00 lab pilot

Status: proposed, 2026-10-06. Built against `prd-00-lab-pilot.md` and `architecture.md`.

Each step ends with passing tests and something you can run on the Mac. Steps run in order unless marked parallel. "Fake worker" and "fake guard" are the test doubles described in the architecture's Tests section.

## 0. Scaffold

Outcome: `docker compose up` on the Mac serves a page from `web` and `/healthz` from the app, and `pytest` and `npm test` pass.

- `pyproject.toml`, `Dockerfile`, `compose.yml` with caddy, app, web and db (official `postgres:18` image, named volume, internal network only, no published port), `Caddyfile` (plain HTTP on localhost for now, `/api/*` and `/healthz` to app, everything else to web). `.env` with generated database credentials.
- `web/`: Next.js app (App Router, TypeScript) with `output: "standalone"`, its Dockerfile, a placeholder page at `/`, `lib/api.ts` for API calls with the CSRF header, and Vitest with Testing Library set up with one passing test. Pinned versions in `package.json` and the lockfile.
- `app/main.py` with `/healthz`, `app/db.py` with the psycopg async pool, applying `schema/001_init.sql` with all tables from the architecture and recording it in `schema_migrations`.
- `tests/` with the test Postgres (one container in Docker for the test session, a disposable database per run), the ASGI test client fixture, the fake worker and the fake guard.
- `LICENSES.md` started.

Excludes: any feature.

Checks: `pytest` and `npm test` in `web/` green. `docker compose up` then `curl localhost/healthz` returns ok and `curl localhost/` returns the web page. A migration test applies the schema to an empty database and to an already-migrated one.

Delivered 2026-10-10: everything listed above. Commands are in AGENTS.md "Build and test".

## 1. Staff accounts (parallel with 2)

Outcome: the tech teacher creates the admin account with the setup code and signs in. The admin creates a teacher account.

- Setup code printed to the log on first start, setup page, login, logout, roles, CSRF, cookie sessions in the database.
- Setup and login pages, and bare `/admin` and `/teach` pages that need the right role, in `web/`.

Requirements: R1.2, part of R7.4 (audit rows for account changes).

Checks: API tests for setup-once, wrong code, login, role enforcement, CSRF rejection, audit rows. Vitest tests for the setup and login pages.

Delivered 2026-10-10: everything listed above, plus a staff list and "create staff account" form on `/admin`. Migration `002_staff_auth.sql` adds the per-session CSRF token. No staff delete or password change yet.

## 2. Worker registry and the worker stack (parallel with 1)

Outcome: on the Mac, the agent in front of a native Ollama (or llama-server) running Gemma 4 E2B shows up in the app as a worker with its models.

- `worker/agent.py` (auth proxy and heartbeat), its Dockerfile and `worker/compose.yml`. `worker/recipes/`: llama-server compose with nvidia and cpu profiles, Ollama notes.
- `/api/workers/heartbeat` with join token, deny list, down after 45 s, routing by spare capacity from in-flight counts, model list built from heartbeats, models on/off.
- Admin page section in `web/`: join token and command (bash and PowerShell), worker list with status, models and capacity, remove worker, rotate token, model toggles.

Requirements: R2.1-R2.5, R3.1.

Checks: unit tests for routing and down-detection with a fake clock. API tests for token rejection, removed worker rejection, model toggle. Agent tests: request without the key refused, streaming forwarded, heartbeat payload built from a fake backend. Manual: start the worker stack on the Mac and see it go up, stop it and see it go down within a minute.

## 3. Classes and lesson sessions

Depends on 1.

Outcome: a teacher creates a class with instructions and a message limit, starts a session, generates and prints code slips, and students join with their slip's code and their name.

- Class create and edit. Start session, open, pause, close. Generate a batch of codes (default 30, number set by the teacher) and more during the session.
- Join endpoint with per-IP rate limit. First use of a code binds it to the name entered. Entering a bound code again resumes as that student under the bound name. Student cookie. Rename, unbind and remove.
- Teacher page in `web/`: session bar with "Print slips" and generate more, roster of codes with used or not and bound name (manual refresh for now).
- `/teach/slips` in `web/`: print-friendly A4 page of cut-out slips, one code per slip, with the class name and the chat web address. Print CSS only, no PDF library.
- Student page in `web/`: join form, and a header with the student's name and code on every page.

Requirements: R3.2, R3.3 as decided in D1, R3.4 (limit stored), R3.5, R4.6, R4.7, R5.4.

Checks: API tests for code uniqueness among open sessions, single-use binding (a second name on a bound code gets the bound name), resume on a second device with the same code, closing the session invalidates all its codes and cookies, unbound and removed students' cookies refused, rate limit, pause blocks sending. Vitest tests for the slips page and the name and code header.

Delivered 2026-10-10: everything listed above. Migration `003_lesson_sessions.sql` allows one live lesson session per class. Pause is enforced through `require_student(sending=True)`, which step 4's send endpoint must call. Audit rows are written for class, session and roster actions.

## 4. Student chat, unguarded behind a feature flag

Depends on 2 and 3.

Outcome: a student on a phone and a laptop each pick a model and get a streamed reply from the Mac worker. Everything is stored.

- `chat.py` pipeline steps 1, 2, 4, 5 and 7. Message limit enforced. Retry on another worker when one fails.
- Student page in `web/`: the monitoring notice before the first message and on every chat, model picker showing enabled models only, streaming reply, new conversation, list of this session's conversations. Accessible markup.
- The guard step is a pass-through that fails closed unless `GUARD_DISABLED=1`, which only the test settings and this step's manual run use.

Requirements: R4.1-R4.4, acceptance 3 (minus the teacher side), 4, 6.

Checks: API tests with the fake worker for streaming, storage before send, disabled model refused, limit reached, worker failover, worker drop mid-reply stored as error. Manual: phone and laptop on the Mac's LAN address.

## 5. Guard bake-off (parallel with 1-4)

Outcome: a measured choice of guard model for step 6, recorded in `docs/guard-results.md`.

- Candidates, all on CPU in llama-server: Qwen3Guard-Gen-0.6B and Qwen3Guard-Gen-4B (Q8_0 GGUF), and Shieldstral-1.0-3B.
- `tests/guard_set.jsonl`: a labelled set of about 1,500 messages, student prompts and model replies, safe and unsafe, covering each guard category, plus borderline classroom content (history, biology, fiction) to measure false positives. The source and licence of each item are noted alongside it.
- `tests/guard_bakeoff.py`: runs each candidate against the set and records recall per category, false-positive rate, and CPU latency per check with 30 concurrent students on the reference server hardware. It is also the on-demand check of the real guard (acceptance 7).
- `docs/guard-results.md` with the numbers and a recommendation. Step 6 uses the winner, and ADR-0008 is updated to name it.

Requirements: acceptance 7.

Checks: unit tests for the scoring on a small fixed set. The script runs end to end against each candidate. `docs/guard-results.md` and ADR-0008 name the same model.

## 6. Guard

Depends on 4 and 5.

Outcome: every prompt and reply segment is checked on CPU by the guard model chosen in step 5, and the configured action happens.

- `guard` service in `compose.yml` with the weights in a volume. `guard.py` prompt format and parsing for the chosen model. If it is Qwen3Guard, Controversial counts as unsafe.
- Pipeline steps 3 and 6: prompt check, segmented reply release, stop on a tripped segment.
- Admin page section in `web/`: for each category the guard model provides, an action and the student-facing message, both editable.
- Fail closed when the guard is down. Remove `GUARD_DISABLED`.

Requirements: R4.5, R6.1-R6.6.

Checks: unit tests for parsing every verdict shape. API tests for saving the settings, and with the fake guard for each action (allow+flag, block+flag, block), a block with the configured message shown and no model reply, a reply segment tripping mid-stream, guard down. On demand: the step 5 script against the guard as deployed.

## 7. Teacher console, live

Depends on 6.

Outcome: the teacher watches the class live, opens transcripts, reviews flags and sees the usage summary.

- `NOTIFY` on each stored message, flag, join and state change. `live.py` `LISTEN`s and fans out to the SSE endpoint per lesson session, reconnect then resync.
- Teacher page in `web/` as specified in the architecture's Teacher console section: session bar, card grid, usage strip, transcript panel, mark reviewed. The SSE stream is read in the browser with `EventSource`.
- Playwright smoke in `web/e2e/` against the compose stack with the fake worker and fake guard: a student joins and chats, and the teacher sees it.
- `docs/manual-tests.md` for the console on laptop and iPad.

Requirements: R5.1-R5.5, R6.7, D1 usage summary, acceptance 3 (teacher side within five seconds), 9.

Checks: API tests that a stored message, flag, join and state change each produce one event on the right session's stream and none on another, including with two app processes. Vitest tests for the card grid, transcript panel and usage strip. The Playwright smoke passes. Manual tests file run on the Mac with two student devices.

## 8. Records and audit

Depends on 4. Can run alongside 6 and 7.

Outcome: retention runs, one student can be exported and deleted, the whole server can be backed up and restored, and staff actions are in an audit log the admin can read.

- Hourly retention task, default 30 days (D2).
- Student export (JSON) and delete.
- Backup with `pg_dump -Fc` through `docker compose exec db`. Restore with `pg_restore` into an emptied database, app stopped. Download from the admin page in `web/` runs the same dump.
- Audit rows for model changes, session state changes, flag reviews, exports and deletions. Admin page list in `web/`.

Requirements: R1.5, R7.1-R7.4, acceptance 10.

Checks: retention test with a fake clock. Export contents test. Backup, wipe, restore round trip on a populated database. Each staff action writes one audit row.

## 9. Install, TLS and Windows

Depends on 7 and 8.

Outcome: someone new follows the README and gets the server running and a worker joined on Windows with an NVIDIA card.

- Caddy `tls internal` with `SERVER_NAME`, root certificate download on the admin page in `web/`.
- `README.md` quick start. `docs/install.md`: server install, Docker Engine in WSL2 on Windows 11 from `docs/research/container-runtime.md`, worker join on Windows (inside Ubuntu) and Mac, start on boot, trusting the certificate per device, firewall rule so only the server reaches workers, upgrade, backup.
- `LICENSES.md` complete, including default models.
- Run on a Windows test machine with an NVIDIA card: acceptance 1, 2, 5, 8.

Requirements: R1.1, R1.3, R1.4, R1.6, R2.6, acceptance 1, 2, 5, 8, 11.

Checks: the Windows test machine run against the acceptance list, recorded in `docs/pilot-checks.md`. Upgrade from the previous commit keeps data.

Delivered so far (2026-10-10, checked on a Mac): Caddy `tls internal` for `SERVER_NAME` with plain HTTP in development, `scripts/init-env.sh <name>`, `docs/install.md` (server, Docker Engine in WSL2, HTTPS and certificate trust, start on boot, upgrade), `scripts/windows/`, the README quick start and `LICENSES.md` with the default models. Upgrade from the previous commit kept data. Still to do: the root certificate download on the admin page (needs step 1), the install sections for backup (with step 8) and worker join and firewall (with step 2), and the Windows test machine run, which also tests every step `docs/install.md` marks untested.

## 10. Load test

Depends on 9.

Outcome: measured capacity for 30 students on the Windows GPU.

- `tests/load.py`: 30 simulated students, realistic message pacing, counts lost and unrecorded messages, reports time to first segment and guard latency.
- Publish the numbers in `docs/capacity.md`. If time to first segment is far over five seconds, try longer guard segments and more model-server concurrency, and record each result.

Requirements: acceptance 12, PRD outcome on reply time.

## Order and parallel work

```
0 -> 1 -> 3 -> 4 -> 6 -> 7 -> 9 -> 10
0 -> 2 ------^    \-> 8 ------^
0 -> 5 ------------^
```

Lanes: 1 and 2 run together after 0. 5 runs alongside 1-4. 8 runs alongside 6 and 7. Everything else is sequential because each step builds on the one before.

## Not in this plan

Everything in the PRD's non-goals, plus: published container images, academic-integrity checks (D4), student accounts across sessions (D1), Granite Guardian second-pass checks, Presidio redaction.
