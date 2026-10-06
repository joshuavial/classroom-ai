# ADR-0001: Open source only

Status: Accepted. Date: 2026-10-06. Decided by: JV.

## Context

Schools need something they can audit, fork and keep running without a vendor. Many classroom AI products are paid cloud services.

## Decision

Every component is OSI-licensed and every model openly licensed. No SaaS, vendor accounts or telemetry sent home. LM Studio, Bionic, Open WebUI from v0.6.6, LobeChat, Llama and Gemma-licence models are out.

## Consequences

Some strong tools are excluded. Docker Desktop is proprietary but free for schools; it is a host prerequisite, not a shipped component, and Docker Engine is the open option on Linux.
