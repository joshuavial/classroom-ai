# ADR-0014: Any OpenAI-compatible worker

Status: Accepted. Date: 2026-10-07. Supersedes ADR-0007. Amends ADR-0002 and ADR-0003.

## Context

Schools have different GPU machines and different people looking after them. Tying workers to llama-server in Docker forces one way of running models, and rules out what a school may already use, such as Ollama. It also blocks the Apple GPU, which Docker containers can't reach.

## Decision

A worker is any machine running an OpenAI-compatible model server plus the project's agent. The agent is the only part the project requires. It runs in Docker, publishes the worker's one LAN port, checks the worker's API key, forwards chat requests to the model server, and heartbeats to the proxy with the models the server offers. The model server can be llama-server, Ollama, vLLM or anything else open source that speaks the OpenAI API, in Docker or installed natively, with any number of models.

The project ships recipes for llama-server in Docker and native Ollama, with suggested Gemma 4 models per card size.

## Consequences

The school chooses how GPUs run. Every backend gets the same auth, including Ollama, which has none of its own. The proxy measures busyness from its own in-flight request counts instead of a backend-specific endpoint. The agent becomes a streaming proxy, slightly larger than a heartbeat script. Keeping the model server off the LAN (localhost or a Docker network) is part of each recipe.
