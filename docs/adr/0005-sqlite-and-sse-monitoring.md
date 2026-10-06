# ADR-0005: Sqlite and sse monitoring

Status: Accepted. Date: 2026-10-06. Decided by: Lead agent.

## Context

The teacher needs a live view of every conversation. Langfuse needs six services and 16 GiB of RAM and has no classroom view.

## Decision

Conversations go in SQLite (WAL mode, stdlib sqlite3, no ORM). The teacher console gets live updates over Server-Sent Events from an in-memory event bus in a single app process.

## Consequences

One file to back up. The app must run as one process; a second process would split the event bus.
