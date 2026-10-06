# Design: proxy chat server with registered GPU workers

Draft, 2026-10-06. Captures the project's architecture direction; not built.

## Shape

```
phones / tablets / lab PCs (browser only)
        |  HTTPS, school LAN
        v
+---------------- proxy box: docker compose ----------------+
| caddy (TLS)                                                |
| app: student chat UI, teacher console, auth, logging,      |
|      guardrails, model routing, worker registry            |
| sqlite (WAL)                                               |
| guard: llama-server on CPU running Qwen3Guard-Gen-0.6B     |
+------------------------------------------------------------+
        |  OpenAI-compatible HTTP, LAN only
        v
GPU workers (any number): llama-server + tiny register agent
```

- Clients run nothing but a browser, so any phone, tablet or Chromebook works and never touches a GPU.
- GPU workers are never reachable by students. Firewall them to accept only the proxy.
- The guard model runs on the proxy box (0.6B fits CPU), so a block still works when no GPU worker is up. Granite Guardian on flagged turns can run on a worker.

## Worker registration

Each GPU box runs `llama-server` plus a ~30 line agent that every 15s POSTs to `/api/workers/heartbeat` with a shared join token: its base URL, the models it has loaded, and free slots. The proxy marks a worker dead after 3 missed beats. Routing: pick a live worker serving the requested model with the most free slots.

ponytail: in-app registry and least-busy routing. Swap to LiteLLM router (dynamic `/model/new`) only if the in-app router measurably falls short.

## Teacher config (in the web console)

- Enable/disable models from the list workers advertise. Students only ever see enabled models.
- Per-class system prompt (tutor mode, no essay writing), message limits, open/pause the room.
- Guardrail categories and actions (block, flag, block + flag). Self-harm shows a school-written static message.
- Seat codes or student accounts for a class.

## Teacher monitoring

Live seat grid over SSE, flagged rows highlighted in the console. No phone push alerts (2026-10-06). Full transcripts per student, export, nightly retention delete.

## Bionic as the client: ruled out

- Bionic does not accept a custom OpenAI-compatible base URL. Its only model sources are Local, LM Studio Secure Cloud, and Remote via LM Link (lmstudio-ai/docs `0_bionic/4_models/index.mdx`; https://bitrefinery.com/blog/lm-studio-bionic-remote-gpu-benchmark, 2026-07-18).
- LM Link requires an LM Studio account and an internet connection, and traffic is end-to-end encrypted over Tailscale (lmstudio-ai/docs `3_cli/3_link/link-enable.mdx`; https://lmstudio.ai/docs/lmlink/basics/faq). The proxy could not see, log or guard it.
- Bionic is a desktop app (Windows, macOS, Linux), so it does not cover phones or tablets.
- Bionic is closed source, which breaks the open-source constraint. An account is needed only for cloud models and LM Link. Local models work without one.
