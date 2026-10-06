# ADR-0003: Docker desktop everywhere

Status: Proposed. Date: 2026-10-06.
## Context

No pilot school yet. Windows is the likely host. Development happens on a Mac.

## Decision

The server and the worker agent run in Docker, so each machine needs only Docker Desktop. The model server may run in Docker or natively (ADR-0007). Workers use an NVIDIA GPU through WSL2 on Windows. On a Mac the worker runs on CPU with a small model in Docker, or on the Mac GPU with a native model server, for development only.

## Consequences

GPU speed is tested on a Windows test machine with an NVIDIA card. Docker Model Runner can use the Mac GPU but has no API key, so it is not used.
