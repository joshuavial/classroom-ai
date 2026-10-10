# Licences

Every component the project ships or runs, with its licence. Started in step 0 and completed in step 9, including the default models.

## Server runtime

| Component | Version | Licence |
| --- | --- | --- |
| Python | 3.14.8 | PSF-2.0 |
| Starlette | 1.7.0 | BSD-3-Clause |
| uvicorn | 0.54.0 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| psycopg (with psycopg-binary) | 3.3.6 | LGPL-3.0; the binary wheel bundles libpq (PostgreSQL licence) and OpenSSL (Apache-2.0) |
| psycopg-pool | 3.3.3 | LGPL-3.0 |
| PostgreSQL (`postgres` image) | 18.6 | PostgreSQL licence |
| Caddy (`caddy` image) | 2.11.7 | Apache-2.0 |
| Node.js | 24.21.0 | MIT |
| Next.js | 16.4.0 | MIT |
| React, React DOM | 19.3.0 | MIT |

Indirect Python dependencies (from `uv.lock`) include anyio, h11, httpcore, idna, certifi (MPL-2.0), click and typing-extensions, all OSI-approved.

## Development and build only

| Component | Version | Licence |
| --- | --- | --- |
| uv | 0.12.13 | MIT or Apache-2.0 |
| pytest | 9.1.1 | MIT |
| pytest-asyncio | 1.4.0 | Apache-2.0 |
| TypeScript | 7.0.2 | Apache-2.0 |
| Vitest | 5.0.3 | MIT |
| Testing Library (react, dom, jest-dom) | 16.3.3, 10.4.2, 7.0.1 | MIT |
| jsdom | 30.1.2 | MIT |
| @vitejs/plugin-react | 6.1.2 | MIT |
