# ADR-0007: Llama server one model per worker

Status: Superseded by ADR-0014. Date: 2026-10-06. Decided by: Lead agent.

## Context

Workers need an MIT-licensed OpenAI-compatible server that fits 8-24 GB cards and runs on CPU for development.

## Decision

Workers run llama.cpp llama-server from the official images, one chat model per worker stack. Default models are Gemma 4 official GGUFs: 12B on 16 GB, E4B on 8 GB, E2B on CPU.

## Consequences

Two models on one machine means two worker stacks. Router mode can replace this once a machine needs to swap models.
