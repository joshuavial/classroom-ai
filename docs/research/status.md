# Research Status: Classroom local AI with teacher monitoring

## Objective
Design an accessible git repo a school tech teacher can run: local open-source LLMs on GPU lab machines, a student chat UI, realtime teacher visibility of all conversations (Langfuse or similar), and local guardrails that block and alert the teacher.

## Constraints (2026-10-06)
- Fully self-managed open-source tech. No Bionic, no LM Studio (closed source), no SaaS or vendor accounts.

## Timeline
- 2026-10-06 - Research initiated
- 2026-10-06 - Scope reset to open-source only; 4 threads relaunched via ai-route (Grok: serving/UI, guardrails; Claude dev: telemetry, prior art)
- 2026-10-06 - Serving and chat UI report written: `serving-and-chat-ui.md`
- 2026-10-06 - Guardrails thread written to guardrails.md (models, duty of care, alerts, lab lockdown)
- 2026-10-06 - All 4 threads in; summary.md, sources.md and index written
- 2026-10-06 - client/proxy/GPU-worker split, no phone alerts; Bionic checked and ruled out; design.md drafted

## Open Questions
- [ ] One central GPU server vs every lab PC running its own model?
- [ ] Student identity: school SSO, lab login, or anonymous seat IDs?
- [ ] Privacy/consent rules for logging minors' chats (NZ Privacy Act / school policy)
