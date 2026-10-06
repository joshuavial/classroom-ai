# Architecture decision register

One file per decision, numbered in order. To change a decision, add a new ADR that supersedes the old one and set the old one's status to "Superseded by ADR-NNNN". Do not rewrite an accepted ADR.

Template: copy any ADR and keep the headings (Context, Decision, Consequences).

| ADR | Decision | Status |
| --- | --- | --- |
| [0001](0001-open-source-only.md) | Open source only | Accepted |
| [0002](0002-proxy-server-and-gpu-workers.md) | Proxy server and gpu workers | Accepted, amended by 0014 |
| [0003](0003-docker-desktop-everywhere.md) | Docker desktop everywhere | Accepted, amended by 0014 |
| [0004](0004-own-gateway-app.md) | Own gateway app | Accepted |
| [0005](0005-sqlite-and-sse-monitoring.md) | Sqlite and sse monitoring | Accepted |
| [0006](0006-own-chat-pages-no-build.md) | Own chat pages no build | Accepted |
| [0007](0007-llama-server-one-model-per-worker.md) | Llama server one model per worker | Superseded by 0014 |
| [0008](0008-cpu-guard-on-the-server.md) | Cpu guard on the server | Accepted |
| [0009](0009-segmented-reply-checking.md) | Segmented reply checking | Accepted |
| [0010](0010-six-digit-session-code.md) | Six digit session code | Accepted |
| [0011](0011-flags-to-classroom-teacher-only.md) | Flags to classroom teacher only | Accepted |
| [0012](0012-thirty-day-retention.md) | Thirty day retention | Accepted |
| [0013](0013-anonymous-model-requests.md) | Anonymous model requests | Accepted |
| [0014](0014-any-openai-compatible-worker.md) | Any OpenAI-compatible worker | Accepted |
