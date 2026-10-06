# ADR-0009: Segmented reply checking

Status: Accepted. Date: 2026-10-06.
## Context

Replies should stream to students, and no reply text may reach a student unchecked.

## Decision

Replies are released a sentence (or 300 characters) at a time, each segment checked before it is sent. A tripped segment stops generation and the rest is replaced by the block message.

## Consequences

One guard call per segment. If that is too slow, segments get longer before anything else changes.
