# ADR-0015: Postgres database

Status: Accepted. Supersedes the SQLite part of ADR-0005. Date: 2026-10-07.

## Context

ADR-0005 put all server data in SQLite and the live event bus in memory, so the app had to run as one process. A second process would split the event bus. SQLite is also harder to query from outside the app when the school wants reports later.

## Decision

The server stack keeps all its data in PostgreSQL: conversations, flags, settings, accounts, audit and workers.

- A `db` service in `compose.yml` runs the official `postgres:18` image (18.6 at time of writing, pinned by tag and digest). Data is in a named volume. It is on the internal compose network only and publishes no port. Credentials come from `.env`, which the install step generates.
- The app uses psycopg 3 (LGPL-3.0, OSI-approved) with its async connection pool (`psycopg-pool`, also LGPL-3.0). No ORM. Plain SQL.
- Migrations are numbered SQL files in `app/schema/`, applied at startup and recorded in a `schema_migrations` table.
- Live updates: the app issues `NOTIFY` on each stored message, flag, join and state change. Each app process `LISTEN`s and fans the events out to its own SSE clients. This removes the single-process limit of ADR-0005.
- Backup is `pg_dump` in custom format through `docker compose exec`. Restore is `pg_restore` into an emptied database. The admin page download runs the same dump.

## Consequences

One more container, using roughly 100-200 MB of RAM. Backups are no longer a file copy. Tests need a Postgres: one runs in Docker for the test session, with a disposable database per test run. The database is easier to query for reports later.
