# ADR-0004: Own gateway app

Status: Accepted. Date: 2026-10-06.
## Context

Every request must be stored and checked, and routed to a live worker.

## Decision

A small Python app (Python 3.14, Starlette, uvicorn, httpx) does routing, guarding and logging itself. LiteLLM is not used.

## Consequences

A few hundred lines to own. LiteLLM's useful features are partly enterprise-only and its PyPI package was trojaned in March 2026.
