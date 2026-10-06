# ADR-0005: Postgres and SSE monitoring

Status: Proposed. Date: 2026-10-07.

## Context

The teacher needs a live view of every conversation. Langfuse needs six services and 16 GiB of RAM and has no classroom view. SQLite with an in-memory event bus would tie the app to one process, since a second process would split the bus. SQLite is also harder to query from outside the app when the school wants reports later.

## Decision

The server stack keeps all its data in PostgreSQL: conversations, flags, settings, accounts, audit and workers. The teacher console gets live updates over Server-Sent Events.

- A `db` service in `compose.yml` runs the official `postgres:18` image (18.6 at time of writing, pinned by tag and digest). Data is in a named volume. It is on the internal compose network only and publishes no port. Credentials come from `.env`, which the install step generates.
- The app uses psycopg 3 (LGPL-3.0, OSI-approved) with its async connection pool (`psycopg-pool`, also LGPL-3.0). No ORM. Plain SQL.
- Migrations are numbered SQL files in `app/schema/`, applied at startup and recorded in a `schema_migrations` table.
- Live updates: the app issues `NOTIFY` on each stored message, flag, join and state change. Each app process `LISTEN`s and fans the events out to its own SSE clients, so the app can run as several processes.
- Backup is `pg_dump` in custom format through `docker compose exec`. Restore is `pg_restore` into an emptied database. The admin page download runs the same dump.

## Consequences

One more container, using roughly 100-200 MB of RAM. Backups are a database dump, not a file copy. Tests need a Postgres: one runs in Docker for the test session, with a disposable database per test run. The database is easy to query for reports later.
