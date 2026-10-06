# Telemetry and live teacher monitoring

## Where to capture traffic

Put one OpenAI-compatible gateway between the chat UI and the model server, and make it the only route to the model. Run llama.cpp, Ollama or vLLM on a Compose network with `internal: true` and publish no port for it [11]. A student who learns the model URL then can't reach it from a lab PC. Every request, whether it comes from the UI, a notebook or curl, goes through the gateway, which logs it, runs the guardrails and raises alerts.

UI-side hooks are only a second layer. Open WebUI filters (`inlet`, `stream`, `outlet`) can block a turn by raising an exception, and they also run for Open WebUI's own `/api/chat/completions` endpoint [7]. They never see traffic that goes around Open WebUI. Open WebUI has also used a non-OSI licence with a branding clause since v0.6.6. Deployments with 50 or fewer users per 30 days are exempt from the branding clause, and v0.6.5 and earlier remain BSD-3 [8].

LiteLLM proxy is the ready-made gateway. Its core is MIT, and only the `enterprise/` directory has a separate licence [5]. Its custom pre-call and moderation hooks can reject a request, and the docs show no enterprise flag for them [6]. Its Logs UI stores full prompts and responses once `store_prompts_in_spend_logs` is turned on [9].

These LiteLLM features are enterprise-only: SSO beyond 5 users, audit logs, per-key or per-team guardrails, team-based logging, IP allowlists, secret managers, S3/GCS export and soft budget alerts [4]. A school needs none of them.

PyPI releases 1.82.7 and 1.82.8 were trojaned on 2026-03-24 [12], so pin the image by digest.

## Options

| Option | Licence | Stack | Live view | Per-student | Alerting | Notes |
|---|---|---|---|---|---|---|
| Langfuse v4 | MIT core; EE key for RBAC, retention, audit logs, masking, SCIM [3] | web, worker, Postgres, ClickHouse, Redis, S3/MinIO; 4 cores, 16 GiB, 100 GiB [1][2] | Within seconds with v4 SDKs or OTel plus the `x-langfuse-ingestion-version: 4` header; otherwise about 15 min [13] | `userId`, Users view [14] | Monitors to Slack or webhook, self-hosted v4+ [15] | Heavy; Compose setup has no HA or backups [2] |
| Arize Phoenix | **Elastic License 2.0, not OSI** [16] | One container, SQLite by default [17] | Trace UI | Attributes | Not documented | Fails the licence rule |
| Helicone | Apache-2.0 [18] | All-in-one image; the gateway is a separate component [19] | Request log | Header | Not in self-host docs | Maintenance mode since the Mintlify acquisition in March 2026 [20] |
| OpenLIT | Apache-2.0 [21] | ClickHouse, OTLP | Trace UI | Attributes | Not documented | Uses SDK instrumentation with no proxy [21], so it misses traffic that skips the SDK |
| Lunary | Apache-2.0 (reported) [22] | Postgres 15 + bun; Kubernetes needs a licence key [23] | Conversations | Yes | Unclear | GitHub repo returned 404 today; avoid |
| Opik | Apache-2.0 [24] | ClickHouse, MySQL, Redis, MinIO [24] | Trace UI | Threads | Online eval rules | Heavy, aimed at developers |
| LiteLLM UI | MIT core [5] | Proxy + Postgres | Logs page, refresh only [10] | Per key or user | Budget alerts are EE [4] | Already the gateway |
| Custom dashboard | Your own (MIT) | Gateway callback writes to SQLite; one page with SSE [25][26] | Pushes updates in under a second | Seat column | Same callback | Smallest option, built for this use |

ClickHouse has owned Langfuse since January 2026, and the licence stays MIT [27]. Langfuse is the strongest general tool, but it was built for developers debugging apps. It runs six services and takes 16 GiB of RAM from the box that also serves the models. It has no classroom view where every seat updates live and flagged messages stand out.

## Recommendation

Run everything with one `docker compose up` on the GPU box:

1. **Model server** (llama.cpp or Ollama) on the internal-only network.
2. **LiteLLM proxy**, pinned by digest, as the only published API. Give each student seat its own virtual key or `user` value.
3. **One Python callback file** mounted into LiteLLM. Its pre-call hook runs the local guardrail and rejects the request on a hit. Every request and response is written to SQLite as `seat, time, prompt, response, flagged, reason`.
4. **A teacher page** of about 150 lines that reads SQLite and pushes new rows over SSE [25]. It shows a grid of seats with the newest message per seat, plus a red banner and a sound when a row is flagged. SQLite in WAL mode lets readers and the writer run at the same time [26].
5. **Any chat UI** pointed at LiteLLM. If the Open WebUI licence is a problem, pin v0.6.5 [8] or use a small custom UI.

This needs no Postgres, ClickHouse, Redis or S3. Adding one Postgres container turns on LiteLLM's searchable Logs UI [9].

Add Langfuse later only if the teacher wants analytics. Connect it through LiteLLM's `langfuse_otel` callback with the v4 header [28] so the gateway stays the single capture point.

Two things are still open: how seats map to students, and how long chats by minors are kept. Automated retention is an EE feature in Langfuse [3]; on the custom path it's a nightly `DELETE` of rows older than N days.

## Sources

1. https://langfuse.com/self-hosting - Langfuse v3 components; Compose is not recommended for production.
2. https://langfuse.com/self-hosting/deployment/docker-compose - Needs 4 cores, 16 GiB and 100 GiB; no HA, scaling or backups.
3. https://langfuse.com/self-hosting/license-key - MIT core and the list of EE features.
4. https://docs.litellm.ai/docs/enterprise - LiteLLM enterprise-only features; SSO free up to 5 users.
5. https://raw.githubusercontent.com/BerriAI/litellm/main/LICENSE - MIT except the `enterprise/` directory.
6. https://docs.litellm.ai/docs/proxy/call_hooks - Pre-call and moderation hooks can reject requests.
7. https://docs.openwebui.com/features/extensibility/plugin/functions/filter.md - Filter hooks; an exception aborts the turn; API callers are covered.
8. https://docs.openwebui.com/license - Non-OSI since v0.6.6, 50-user exemption, v0.6.5 is BSD-3.
9. https://docs.litellm.ai/docs/proxy/ui_spend_log_settings - The `store_prompts_in_spend_logs` toggle.
10. https://docs.litellm.ai/docs/proxy/ui_logs - LiteLLM Logs UI page.
11. https://docs.docker.com/compose/how-tos/networking/ - Compose networking, `internal: true`, and host vs container ports.
12. https://www.netspi.com/blog/executive-blog/ai-ml-pentesting/litellm-supply-chain-compromise/ - LiteLLM PyPI compromise of 2026-03-24.
13. https://langfuse.com/self-hosting/upgrade/upgrade-guides/upgrade-v3-to-v4 - v4 requirements and real-time ingestion conditions.
14. https://langfuse.com/docs/observability/features/users - Per-user tracking and Users view.
15. https://langfuse.com/docs/observability/features/alerts - Alerts on self-hosted v4+.
16. https://github.com/Arize-ai/phoenix - Phoenix is under Elastic License 2.0.
17. https://arize.com/docs/phoenix/self-hosting/deployment-options/docker - Single container, SQLite default, optional Postgres.
18. https://github.com/Helicone/helicone - Apache-2.0 and its service list.
19. https://docs.helicone.ai/getting-started/self-host/docker - All-in-one image; gateway runs separately.
20. https://chatforest.com/reviews/helicone-llm-observability-gateway/ - Maintenance mode after the Mintlify acquisition (secondary source).
21. https://github.com/openlit/openlit - Apache-2.0; SDK to ClickHouse; "No proxy is required".
22. https://www.sourcepulse.org/projects/1828225 - Lunary licence, stack, last commit about 10 months ago (secondary source).
23. https://docs.lunary.ai/docs/more/self-hosting/kubernetes - Lunary Kubernetes self-hosting needs a licence key.
24. https://github.com/comet-ml/opik - Apache-2.0 and its Compose stack.
25. https://developer.mozilla.org/en-US/docs/Web/API/Server-sent_events - Server-Sent Events push.
26. https://www.sqlite.org/wal.html - WAL mode allows concurrent readers and a writer.
27. https://clickhouse.com/blog/clickhouse-acquires-langfuse-open-source-llm-observability - ClickHouse acquired Langfuse; licence stays MIT.
28. https://langfuse.com/integrations/gateways/litellm - LiteLLM `langfuse_otel` callback.
