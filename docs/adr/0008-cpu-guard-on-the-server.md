# ADR-0008: Cpu guard on the server

Status: Accepted. Date: 2026-10-06. Decided by: Lead agent.

## Context

Checks must work when no GPU worker is up.

## Decision

Qwen3Guard-Gen-0.6B (community GGUF, pinned by file hash) runs on a CPU llama-server inside the server stack. Controversial counts as unsafe. If the guard is down, chat stops.

## Consequences

Guard speed on CPU limits class size; the load test measures it.
