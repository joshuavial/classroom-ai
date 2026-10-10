# Development policy

This is the maintained Etude policy for classroom-ai. Both profiles, `dev-claude` and `dev-codex`, follow it unchanged; they differ only in the models they bind, listed in [models.md](models.md). The legacy etude-loop rules (five fixed gates, L0-L4 tiers, fixed rosters, `.etude/registry.yaml` and `workflow.yaml`) do not apply here.

## Profile selection

A lane launched with `claude-route` runs `dev-claude`. A lane launched with `codex-route` runs `dev-codex`. A lane brief may name the other profile explicitly; otherwise the host CLI decides.

## Inputs

- Product: [docs/vision.md](../docs/vision.md), [docs/prd-00-lab-pilot.md](../docs/prd-00-lab-pilot.md). Agents do not edit these; product changes go to the operator.
- Design: [docs/architecture.md](../docs/architecture.md), [docs/adr/](../docs/adr/README.md).
- Backlog: [docs/implementation-plan.md](../docs/implementation-plan.md), steps 0-10, tracked as beads (prefix `cai`).

Read the parts that bear on the bead, not every document. Cross-check claims against source and tests once code exists, and record contradictions, saying whether the code or the doc is wrong.

## Sequence

```text
Read docs + inspect source
  -> Plan behavior, code, docs, and verification
  -> Independent plan review when high risk
  -> Implement code + regression tests + technical docs
  -> Independent QA and verification
  -> Independent final review
  -> Record evidence and deliver
```

A phase being recorded does not mean it needs a model gate. Verification is always required for behavior the change touches.

## Plan

Record on the bead (`bd update <id> --design`): acceptance criteria mapped to the plan step's Requirements and Checks, invariants to keep, components touched, failure cases, docs to change, and how each criterion will be verified. Plans that outgrow a bead note go in `docs/plans/` and are marked proposed.

## Implement

One worker carries the bead through code, tests and docs. Escalate to the stronger implementation model, or to the lead, when the work needs an architectural decision, grows in scope, or fails twice on the same problem; hand over the evidence collected so far.

## Documentation

Every change either updates `docs/architecture.md` or the relevant doc in `docs/` (an ADR for a new or changed decision, `docs/install.md`, `docs/manual-tests.md` and so on as they appear), or records a no-docs rationale on the bead. A rationale names the doc checked and why it is still accurate, for example "restores the behavior architecture.md already describes". Docs describe what the code does. When a step ships, mark what it delivered in `docs/implementation-plan.md` so the plan never shows proposed work as done.

## Risk and review

Classify by the behavior affected, not diff size.

| Class | Examples here | Review |
| --- | --- | --- |
| Docs or test only | wording, a missing test | relevant checks + routine final review |
| Routine code | a page, an endpoint inside an existing pattern | regression evidence + routine final review |
| Consequential, well understood | a fix to auth, guard, retention or migrations | regression and integration evidence + Fable and Astra final review |
| High risk design | staff auth and roles, student codes and cookies, CSRF, worker join tokens, the guard fail-closed path, schema migrations, retention and delete, backup and restore, anything that could send chat text off the server | Fable and Astra plan review + Fable and Astra final review |

Seat commands are in [models.md](models.md). Rules:

- Both seats get the same evidence and answer in fresh contexts. The implementer's self-review is not a vote.
- A two-seat gate needs both seats to pass. Auth failure, quota, timeout, truncated output or a missing tool leaves the review incomplete, never passed.
- A blocker states the defect, its trigger or violated requirement, the consequence and the evidence. Other suggestions are fixed or deferred to a named bead.
- On disagreement, check the source or reproduce the claim. After two substantive revision rounds without agreement, stop and put the issue and a proposed resolution to the operator.
- If the second provider is unavailable, say cross-provider review is unavailable. Do not put another model in the seat and call it the requested one.

## Verification

The implementation worker runs the full required suites and records command, exit status and the exact commit (plus a hash of any uncommitted diff). The lead checks that record against the current revision and runs targeted checks for named risks; it does not rerun completed suites without a new change or failure. A fresh QA worker reads the requirements, plan, source, docs and test results, adds focused tests if coverage is missing, and reports pass, fail or blocked. A production fix found by QA goes back to implementation and gets a new verification record.

Checks for this project (exact commands are added to AGENTS.md "Build and test" by step 0):

| Surface | Evidence |
| --- | --- |
| app (Starlette) | pytest against the disposable test Postgres in Docker; API tests through the ASGI client with the fake worker and fake guard; allowed and denied cases for every auth or role check |
| migrations | apply to an empty database and to an already-migrated one |
| web (Next.js) | Vitest and Testing Library in `web/`; for a changed page, drive it in a real browser against the running stack and check it works in the user's role, not only that it renders |
| stack | `docker compose up`, then `curl localhost/healthz` and the changed route; tear the stack down afterwards |
| end to end | Playwright smoke in `web/e2e/` once step 7 adds it |
| on demand | the real-model smoke, guard check and load test from architecture.md "Tests", only when the bead's acceptance names them |

Do not install anything globally or change the host to make a check pass. A missing tool or service is a blocked result with the exact missing piece.

Evidence record, one per evaluated revision, as a bead note:

```text
Revision: <commit> (+ diff hash if uncommitted)
Acceptance: criterion -> evidence or gap
Checks: command, exit, log path
Exercises: scenario, expected, observed
Docs: paths changed, or the no-docs rationale
QA: blockers, gaps, residual risk
Reviews: seat, model that ran, verdict, packet path + sha256
Status: pass | fail | blocked
```

## Tracker and delivery

- Beads, prefix `cai`. In a worktree, use the shared database the repo already points to; never run `bd init`.
- One branch per bead off current `main`. Stage files by name. Commit message: one short sentence, no body, no co-author line, no emoji, no mention of AI tools.
- The project lead (the session that launches the lanes) lands lane branches into `main`. Do not push to any remote or create a GitHub repo without the operator's say-so.
- Gate packets and reviewer output go under `.etude/tmp/` (ignored) and are never committed.
- Prose in docs: plain English, no em dashes, never name a person.
