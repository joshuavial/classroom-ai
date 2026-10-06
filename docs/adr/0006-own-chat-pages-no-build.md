# ADR-0006: Own chat pages no build

Status: Superseded by ADR-0016. Date: 2026-10-06.
## Context

Open WebUI is non-OSI from v0.6.6. LibreChat has no live teacher view and needs MongoDB.

## Decision

The student, teacher and admin pages are plain HTML, CSS and JavaScript modules served as static files. No build step.

## Consequences

Anyone can read and change them. The teacher console still has to be pleasant for a non-technical teacher, so it needs design care.
