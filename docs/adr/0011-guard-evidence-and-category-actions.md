# ADR-0011: Guard evidence and category actions

Status: Proposed. Date: 2026-10-11.
## Context

A message can be safe, blocked by an action, or fail because the guard could not return a valid result. Flags alone cannot record a plain block, a disabled category, or an unexpected category. The guard model is selected separately, so the database must not depend on one model's verdict labels.

## Decision

Each message has a nullable project guard status: passed, blocked or error. Null means legacy data or work that has not completed. Immutable guard check rows record each valid prompt or reply segment check in order, with the bounded exact model response, project-normalized safety, model-provided categories and elapsed duration in milliseconds.

Category settings are one versioned document. Each published category keeps an enabled choice, one of allow and flag, block and flag, or block, and a student-facing message. A required Uncategorized fallback handles an unsafe result with no published category. Missing, malformed or incomplete settings stop guarded chat.

## Consequences

The selected model can change without rewriting evidence rows. A student's export includes guard status and checks, and retention deletes them with the message. Configured messages are not written to the audit log. The pipeline must persist a valid check before it sends a prompt to a worker or releases a reply segment.
