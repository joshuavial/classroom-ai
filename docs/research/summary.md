# Classroom local AI with teacher monitoring

Checked 2026-10-06. Constraint: fully self-managed open source, no SaaS, no vendor accounts, no LM Studio/Bionic.

## Findings

1. No existing open-source project combines offline student chat, a live teacher view of every conversation, and local safety alerts. telli (AGPL, German states) is closest but built for cloud models. Paid products (SchoolAI, MagicSchool, Flint, Khanmigo) set the feature bar: live chat wall, urgent vs flagged alerts, room pause/lock, per-student summaries. See `sources/thread-prior-art.md`.
2. Off-the-shelf chat UIs fall short. Open WebUI is non-OSI at every scale since v0.6.6; the 50-user threshold (up to 50 end users per rolling 30 days) only decides whether branding may be removed. LobeChat needs a commercial licence to develop and distribute a derivative work. LibreChat (MIT) has no live transcript view. See `serving-and-chat-ui.md`.
3. Langfuse (MIT core) works but is six services and 16 GiB RAM, built for developers, with no classroom view. A gateway that writes SQLite and pushes a live SSE page to the teacher is smaller and fits better. See `sources/thread-telemetry.md`.
4. An OSI-licensed guard pair exists: Qwen3Guard-Gen-0.6B (Apache-2.0, ~0.5 GB) on every prompt and reply, Granite Guardian 4.1 8B (Apache-2.0) on flagged turns plus a custom academic-integrity criterion. Llama Guard and ShieldGemma are non-OSI. LLM Guard was archived July 2026. See `guardrails.md`.
5. Self-harm must escalate to a named adult with a school-written static message, not just a block page (UK KCSIE 2026, NZ Ministry self-harm guidelines).

## Recommended stack

Client/proxy/worker split, see `design.md`. Proxy is one `docker compose up`; GPU workers register with it:

- `llama-server` (llama.cpp, MIT): Qwen3-8B Q4/Q5 on 16-24 GB, Qwen3-4B on 8 GB, a few parallel slots, internal network only.
- Small custom chat app (Python + SQLite): student logins or seat codes, stores every turn, runs Qwen3Guard before and after the model, teacher page with live seat grid and red flags.
- Lab lockdown docs: AdGuard Home DNS blocking of public AI sites, firewall allowlist, kiosk browser, AppLocker.
- Optional later: LiteLLM proxy (pin by digest, PyPI was trojaned March 2026), Langfuse for analytics.

## Confidence

Medium-high on licences and architecture (primary docs). Medium on guard-model accuracy for minors and te reo Maori: untested. No published sizing for 30 students on one card; the repo should measure it.

## Open questions

- Central GPU server or a model on every lab PC (what hardware does the target school have)?
- Student identity: seat codes, local accounts, or Google/Entra via Authentik/Keycloak?
- Retention and consent for logging minors' chats (NZ Privacy Act, school policy).
- Project name (brainstorm in progress).
- Bionic as client: ruled out (no custom endpoint; LM Link needs account + internet). See `design.md`.
