# ADR-0002: Proxy server and gpu workers

Status: Proposed. Date: 2026-10-06.
## Context

Students use phones, tablets and lab PCs that have no GPU. The school's GPUs are spread across other machines.

## Decision

One server in a Docker stack handles sign-in, logging, guards, the student chat page and the teacher console. GPU machines run an OpenAI-compatible model server plus the project's agent, and register with the server using a join token (ADR-0007). Students only ever talk to the server.

## Consequences

Adding a GPU is one command on that machine. Workers need not be on the same network, which leaves room for sharing GPUs between schools (ADR-0010).
