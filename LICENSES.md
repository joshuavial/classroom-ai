# Licences

Every component the project ships or runs, with its licence (acceptance 11). Licences were checked at their source repositories and model cards on 2026-10-10. Components arrive with the step that adds them; a step that adds one adds its row here.

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

## Models and model servers

| Component | Version | Licence |
| --- | --- | --- |
| Gemma 4 (E2B-it, E4B-it, 12B-it), the suggested chat models, as `google/gemma-4-*-it` and the `ggml-org/gemma-4-*-it-GGUF` builds | 4 | Apache-2.0 |
| Qwen3Guard-Gen-0.6B, the default guard model, as `Qwen/Qwen3Guard-Gen-0.6B` and the `mradermacher/Qwen3Guard-Gen-0.6B-GGUF` build | Gen | Apache-2.0 |
| llama.cpp `llama-server` (`ghcr.io/ggml-org/llama.cpp` images) | the build pinned in the compose files that use it | MIT |
| Ollama, optional on workers, installed by the school | any | MIT |

## Host software the install uses

Installed by the school from the vendors' own repositories, not shipped with this project.

| Component | Licence |
| --- | --- |
| Docker Engine (moby) and the Docker CLI | Apache-2.0 |
| Docker Compose plugin | Apache-2.0 |
| NVIDIA Container Toolkit, on GPU workers in Docker | Apache-2.0 |
| Windows Subsystem for Linux | MIT, except a few WSL1 components |
| Ubuntu | free for organisations' internal use under Canonical's IP policy; made of open-source packages under their own licences |

The NVIDIA driver for Windows is proprietary. It is the GPU vendor's driver, which any use of the card needs, not part of this project.

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
