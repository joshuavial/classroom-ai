# ADR-0013: Anonymous model requests

Status: Proposed. Date: 2026-10-06.
## Context

Schools with spare GPUs could lend them to schools without, especially across time zones.

## Decision

Model requests carry only class instructions and conversation text, never a student, class or school identifier. Cross-school sharing (later) adds redaction and an encrypted link.

## Consequences

Every change to the request builder must keep identity out of it.
