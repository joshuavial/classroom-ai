# Model registry

The one place model IDs live. Policy is in [development.md](development.md); it refers to roles here by name.

All calls go through `~/.ai-shared/bin/ai-route`. Never name an account. ai-route logs the account and model of every run to `~/.local/state/orchestrator/tasks.jsonl`; copy the model that actually ran into the evidence record.

IDs resolved 2026-10-10 from the local Codex model cache and the current Claude model list. Status of every binding: unvalidated (no smoke run yet; see the end of this file).

## Roles

| Role | dev-claude | dev-codex |
| --- | --- | --- |
| Lead: scope, risk class, hard decisions, persistent debugging | Fable 5.1 | GPT-6 Astra, effort high |
| Context gathering, simple docs | Haiku 5.5 | GPT-5.6 Luna |
| Bounded implementation and tests | Sonnet 5.5 | GPT-5.6 Terra |
| Demanding implementation | Opus 5.5 | GPT-5.6 Sol |
| QA (routine) | Sonnet 5.5 | GPT-5.6 Terra |
| QA (high risk) | Opus 5.5 | GPT-5.6 Sol |
| Routine final review | GPT-5.6 Sol (cross-provider) | Sonnet 5.5 (cross-provider) |
| Consequential final review, high-risk plan review | fresh Fable 5.1 + fresh Astra | fresh Astra + fresh Fable 5.1 |

Start strong models at high effort. Raise it only after a demonstrated failure at high.

## IDs

| Name | ID |
| --- | --- |
| Fable 5.1 | `claude-fable-5-1` |
| Opus 5.5 | `claude-opus-5-5` |
| Sonnet 5.5 | `claude-sonnet-5-5` |
| Haiku 5.5 | `claude-haiku-5-5` |
| GPT-6 Astra | `gpt-6-astra` |
| GPT-5.6 Sol | `gpt-5.6-sol` |
| GPT-5.6 Terra | `gpt-5.6-terra` |
| GPT-5.6 Luna | `gpt-5.6-luna` |

## Workers

Pick the worker model through the harness's model parameter, not the task name or prompt.

- dev-claude, in-lane subagent: Agent tool with `model: haiku | sonnet | opus`. A full-context fork inherits the lead's model, so give the subagent a self-contained brief instead.
- dev-claude, headless: `ai-route --kind work claude -p --model <ID> --allowedTools ...`
- dev-codex, headless: `ai-route --kind work codex exec -m <ID> -s workspace-write -` with the brief on stdin.

## Review seats

Write the packet to `.etude/tmp/<bead>/packet.md` and feed it on stdin. Seats are read-only and start in a fresh context.

| Seat | Command |
| --- | --- |
| Fable | `ai-route --kind review claude -p --model claude-fable-5-1 --allowedTools Read,Grep,Glob < packet.md` |
| Astra | `ai-route --kind review codex exec -m gpt-6-astra -c model_reasoning_effort=high -s read-only - < packet.md` |
| Sol | `ai-route --kind review codex exec -m gpt-5.6-sol -c model_reasoning_effort=high -s read-only - < packet.md` |
| Sonnet | `ai-route --kind review claude -p --model claude-sonnet-5-5 --allowedTools Read,Grep,Glob < packet.md` |

Run from the repo root so the seat can read the evidence the packet points to.

Fable runs only on the account that has Fable quota. On a credit error ai-route retries the call on Opus. A run that came back from Opus does not fill the Fable seat: record it as a substitution and treat the required review as incomplete unless the operator has allowed it. `ai-route seat claude --prefer fable` does the same fallback and pins `claude-fable-5`, not 5.1, so this file uses the explicit ID instead.

## Lanes

| Profile | Launch | Lead model |
| --- | --- | --- |
| dev-claude | `workmux add -a claude-route <branch>` | account default; switch to Fable 5.1 with `/model` at the start of the lane |
| dev-codex | `workmux add -a codex-route <branch>` | account default at effort medium; switch to Astra at effort high with `/model` |

## Validation

Before a profile delivers its first change, smoke each seat above with a one-line prompt from the repo root and confirm usable output and the model in `tasks.jsonl`. Record the date and result here.

- 2026-10-10, dev-claude lane: Fable (`claude-fable-5-1`), Astra (`gpt-6-astra`) and Sol (`gpt-5.6-sol`) each returned the requested one-line reply, and `tasks.jsonl` logged those models. Sonnet not yet smoked.
