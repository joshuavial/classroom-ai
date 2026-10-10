# Retro 1: epic cai-1ir, steps 0, 1 and 3

Date: 2026-10-11. Beads: cai-1ir.1 (scaffold), cai-1ir.3 (staff accounts), cai-1ir.4 (classes and lesson sessions). Lane: cai-build, dev-claude.

## Summary

All three beads landed on main with both review seats passing, a QA worker on each, and live browser checks. The work was correct but slow: steps 1 and 3 each took four or five review rounds, and the Astra seat was out of quota for about nine hours in total, which held up landing longer than any defect did.

## What went wrong

- Steps 1 and 3 hit the same family of defects one round at a time: no CSRF token on a fresh browser, replacing a live session without that session's token, a second pool connection taken inside a transaction (could exhaust the pool), join not locked against close. Each was found by a seat after implementation.
- Two tests passed without the fix they were meant to guard: cookie tests that set `domain="test"` (httpx never sent the cookie) and a first pool-starvation test (scrypt's semaphore staggered the logins). Found by QA and by running the test against the unfixed code.
- One Fable follow-up (redirect to /login even when logout fails) was folded as a cheap fix and then blocked by Astra, because it made a failed sign-out look like success.
- The Astra seat was refused by ai-route three times (quota floors and a 5-hour cap). High-risk beads cannot land without it, and there is no substitute by policy.
- Step 9 work was started after the lead had moved it to another lane; the bead was already in progress elsewhere. Caught before any commit.
- Docker Desktop's port 80 forwarding reset connections on this Mac; a manual check failed until the host port became configurable (`HTTP_PORT`).
- The machine ran at load average 50 to 75, so suites took two to four times longer and several commands hit their timeout.

## Root causes

- No checklist for the session, connection and lock invariants, so the plan did not enumerate them and the seats found them one per round.
- No rule that a regression test must fail without its fix.
- Seat follow-ups were treated as all equally cheap to fold, including one that relaxed a guarantee.
- Seat availability is outside the lane's control, and the lane had no way to see a quota window coming before starting a gate.
- The lane checked SCOPE.md at the start of a step but not again before claiming a later one.

## What worked well

- Two independent seats with different strengths: Fable found test and doc gaps, Astra found concurrency and pool defects. Neither alone would have caught all of them.
- QA workers on Opus found real defects every time (logout CSRF, a double hash, Cancel submitting a form, retired codes reusable).
- Driving the real stack in Chromium, through Caddy, caught the port problem and proved the live roster update.
- Negative controls once adopted: the one-connection pool test fails on the old code and passes on the new.
- Stacking beads on local branches let work continue while a seat was unavailable.

## Changes

- Applied: `.etude/checklists/sessions-and-locks.md`, covering session paths and CSRF, no nested pool connections, lock order and race tests, negative controls for every regression test, and putting follow-ups that relax a guarantee to the other seat. Plans for any bead touching auth, cookies, the pool or locks answer it up front.
- Tracked: link the checklist from `.etude/development.md` "Plan" (a change to the maintained policy, so it is a bead for the lead rather than applied here): cai-1ir.20.
- Tracked: a way for a lane to see when the Astra seat will next be available before starting a high-risk gate, and to queue a seat run for that time (a change to ai-route and the review seat tooling, outside this repo): cai-1ir.21.
- Applied: `.etude/checklists/before-claiming.md` (re-read SCOPE.md, `bd show`, and fetch before claiming a bead).
