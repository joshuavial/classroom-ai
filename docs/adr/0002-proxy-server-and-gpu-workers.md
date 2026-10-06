# ADR-0002: Proxy server and gpu workers

Status: Accepted. Date: 2026-10-06. Decided by: JV.

## Context

Students use phones, tablets and lab PCs that have no GPU. The school's GPUs are spread across other machines.

## Decision

One server in a Docker stack handles sign-in, logging, guards, the student chat page and the teacher console. GPU machines run llama-server and register with the server using a join token. Students only ever talk to the server.

## Consequences

Adding a GPU is one command on that machine. Workers need not be on the same network, which leaves room for sharing GPUs between schools (ADR-0013).
