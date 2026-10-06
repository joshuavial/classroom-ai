# ADR-0003: Docker desktop everywhere

Status: Accepted. Date: 2026-10-06. Decided by: JV (D3).

## Context

No pilot school yet. Windows is the likely host. Development happens on a Mac.

## Decision

The server and the worker both run in Docker, so each machine needs only Docker Desktop. Workers use an NVIDIA GPU through WSL2 on Windows. On a Mac the worker runs on CPU with a small model, for development only.

## Consequences

The Mac can't test GPU speed; that happens on JV's Windows machine. Docker Model Runner can use the Mac GPU but has no API key, so it is not used.
