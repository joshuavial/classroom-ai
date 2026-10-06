# ADR-0016: Next.js web app

Status: Accepted. Supersedes ADR-0006. Date: 2026-10-07.

## Context

ADR-0006 made the student, teacher and admin pages plain HTML, CSS and JavaScript served as they are written. The teacher console has a live card grid, a transcript panel, a usage strip and flag review, all updating over SSE. Building that by hand in plain modules is slow, and it has to be pleasant for a non-technical teacher. A component framework with a test runner makes it easier to build and to test.

## Decision

The three web interfaces are a Next.js app. The Python app stays as the API and gateway: auth, logging, guard pipeline, worker registry, routing and SSE.

- A `web` service in `compose.yml` runs a Next.js app (App Router, TypeScript), built with `output: "standalone"` into a Node image. The build writes `.next/standalone` with a minimal `server.js` and only the `node_modules` files it needs. The Dockerfile copies the `public` and `.next/static` folders into it and runs `node server.js` with `HOSTNAME=0.0.0.0`. Versions at time of writing: Next.js 16.4.0 and React 19.3.0, both MIT, on Node.js 24 LTS (24.21.0).
- `web` renders the student, teacher and admin interfaces. It holds no data and no secrets. Every read and write goes to the Python app's JSON API.
- Caddy routes `/api/*`, including the SSE streams, to the Python `app` service and everything else to `web`. Both are on the same origin, so the session cookie and the CSRF header work unchanged. Browsers never call the Python app on another origin.
- Pages are client components that call the API. The teacher console reads its SSE stream in the browser with `EventSource`. Server-side rendering features stay minimal. No server actions talk to the database.
- Styling is plain CSS modules. No UI framework dependency unless one is needed later. The accessibility requirements in PRD R4.1 still apply.

## Consequences

There is a build step and a Node toolchain. Auditors read a second language, TypeScript beside Python. A pleasant teacher console is easier to build. One more container runs on the server.

Tests: component and page tests with Vitest and Testing Library, and a small Playwright end-to-end smoke against the compose stack (a student joins and chats, the teacher sees it).
