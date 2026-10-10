"""The worker agent: an auth proxy in front of an OpenAI-compatible model
server, plus a heartbeat to the classroom-ai server.

Configuration, from the environment:
  SERVER_URL      the classroom-ai server, e.g. http://192.168.1.10
  JOIN_TOKEN      the join token shown on the admin page
  BACKEND_URL     the model server, never reachable from the LAN
  MAX_CONCURRENT  requests this worker takes at once (default 4)
  WORKER_URL      optional; how the server reaches this agent when the
                  address it sees the heartbeat come from is not reachable
  IDENTITY_PATH   where the worker ID and API key live (default /data/identity.json)
  SERVER_CA       the server's root certificate, used to check SERVER_URL over
                  HTTPS (default /etc/classroom-ai/server-ca.crt, which
                  worker/compose.yml mounts from worker/server-ca.crt)
"""

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import ssl
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

log = logging.getLogger("agent")

PORT = 8081
HEARTBEAT_SECONDS = 15
MAX_MODELS = 128
# Read timeout is the longest gap between two chunks, not the whole reply.
BACKEND_TIMEOUT = httpx.Timeout(connect=10, read=120, write=30, pool=10)
SERVER_TIMEOUT = httpx.Timeout(10)
# Headers passed between client and backend. Authorization is never forwarded.
REQUEST_HEADERS = ("content-type", "accept")
RESPONSE_HEADERS = ("content-type", "cache-control")


class IdentityError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    worker_id: str
    api_key: str


@dataclass(frozen=True)
class Config:
    server_url: str
    join_token: str
    backend_url: str
    max_concurrent: int = 4
    worker_url: str | None = None
    server_ca: str | None = None


def load_identity(path: Path) -> Identity:
    """Read the worker ID and API key, creating them on first start.

    A file that exists but cannot be read is an error, not a reason to make a
    new identity: a fresh ID would sidestep a removal by the admin.
    """
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            identity = Identity(str(uuid.UUID(data["worker_id"])), data["api_key"])
        except (ValueError, KeyError, TypeError):
            raise IdentityError(f"{path} is unreadable; restore it or delete it to rejoin") from None
        if not isinstance(identity.api_key, str) or len(identity.api_key) < 32:
            raise IdentityError(f"{path} has no usable API key")
        return identity
    identity = Identity(str(uuid.uuid4()), secrets.token_urlsafe(32))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".identity-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"worker_id": identity.worker_id, "api_key": identity.api_key}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)  # mkstemp already made it owner-only
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return identity


def parse_models(payload: object) -> list[str]:
    """Model IDs from an OpenAI /v1/models reply. Raises ValueError if malformed."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        raise ValueError("not a model list")
    models: list[str] = []
    for item in payload["data"]:
        model_id = item.get("id") if isinstance(item, dict) else None
        if not isinstance(model_id, str) or not 1 <= len(model_id) <= 255:
            raise ValueError("bad model id")
        if model_id not in models:
            models.append(model_id)
    if not models or len(models) > MAX_MODELS:
        raise ValueError("wrong number of models")
    return models


async def build_heartbeat(config: Config, identity: Identity, backend: httpx.AsyncClient) -> dict:
    response = await backend.get(f"{config.backend_url}/v1/models")
    response.raise_for_status()
    beat = {
        "worker_id": identity.worker_id,
        "api_key": identity.api_key,
        "models": parse_models(response.json()),
        "capacity": config.max_concurrent,
    }
    if config.worker_url:
        beat["worker_url"] = config.worker_url
    return beat


async def send_heartbeat(
    config: Config, identity: Identity, backend: httpx.AsyncClient, server: httpx.AsyncClient
) -> None:
    beat = await build_heartbeat(config, identity, backend)
    response = await server.post(
        f"{config.server_url}/api/workers/heartbeat",
        json=beat,
        headers={"authorization": f"Bearer {config.join_token}"},
    )
    response.raise_for_status()


def reason(response: httpx.Response) -> str:
    """The server's short reason for refusing a heartbeat; it never holds a secret."""
    try:
        return str(response.json().get("error", ""))[:200]
    except Exception:
        return ""


async def heartbeat_loop(
    config: Config,
    identity: Identity,
    backend: httpx.AsyncClient,
    server: httpx.AsyncClient,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Heartbeat forever. A failed beat is skipped, so the server sees the
    worker go down rather than up with models it cannot serve."""
    while True:
        try:
            await send_heartbeat(config, identity, backend, server)
        except httpx.HTTPStatusError as e:
            log.warning("heartbeat failed: %s %s", e.response.status_code, reason(e.response))
        except Exception as e:  # keep beating whatever went wrong
            log.warning("heartbeat failed: %s", type(e).__name__)
        await sleep(HEARTBEAT_SECONDS)


def server_verify(config: Config) -> ssl.SSLContext | bool:
    """Trust the server's own root certificate when there is one."""
    return ssl.create_default_context(cafile=config.server_ca) if config.server_ca else True


def create_app(
    config: Config,
    identity: Identity,
    backend: httpx.AsyncClient | None = None,
    server: httpx.AsyncClient | None = None,
    heartbeat: bool = True,
) -> Starlette:
    expected = f"Bearer {identity.api_key}".encode()

    async def proxy(request: Request) -> Response:
        given = request.headers.get("authorization", "").encode()
        if not hmac.compare_digest(given, expected):
            return JSONResponse({"error": "unauthorised"}, status_code=401)
        client: httpx.AsyncClient = request.app.state.backend
        upstream_request = client.build_request(
            request.method,
            f"{config.backend_url}{request.url.path}",
            params=request.query_params,
            # identity: the reply is passed on byte for byte, so it must not be compressed.
            headers={**{k: v for k, v in request.headers.items() if k in REQUEST_HEADERS},
                     "accept-encoding": "identity"},
            content=request.stream() if request.method == "POST" else None,
        )
        try:
            upstream = await client.send(upstream_request, stream=True)
        except httpx.TimeoutException:
            return JSONResponse({"error": "model server timed out"}, status_code=504)
        except httpx.HTTPError:
            return JSONResponse({"error": "model server unavailable"}, status_code=502)

        async def body():
            # Once the reply has started, an upstream failure can only end the
            # stream early. Closing here also runs when the client goes away.
            try:
                async for chunk in upstream.aiter_raw():
                    yield chunk
            finally:
                await upstream.aclose()

        return StreamingResponse(
            body(),
            status_code=upstream.status_code,
            headers={k: v for k, v in upstream.headers.items() if k in RESPONSE_HEADERS},
        )

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        async with contextlib.AsyncExitStack() as stack:
            app.state.backend = backend or await stack.enter_async_context(
                httpx.AsyncClient(timeout=BACKEND_TIMEOUT)
            )
            server_client = server or await stack.enter_async_context(
                httpx.AsyncClient(timeout=SERVER_TIMEOUT, verify=server_verify(config))
            )
            task = None
            if heartbeat:
                task = asyncio.create_task(
                    heartbeat_loop(config, identity, app.state.backend, server_client)
                )
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await task

    return Starlette(
        routes=[
            Route("/v1/models", proxy, methods=["GET"]),
            Route("/v1/chat/completions", proxy, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


def from_env() -> Starlette:
    """Entry point for uvicorn --factory."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ
    missing = [k for k in ("SERVER_URL", "JOIN_TOKEN", "BACKEND_URL") if not env.get(k)]
    if missing:
        raise SystemExit(f"missing environment variables: {', '.join(missing)}")
    try:
        max_concurrent = int(env.get("MAX_CONCURRENT") or 4)
    except ValueError:
        raise SystemExit("MAX_CONCURRENT must be a whole number") from None
    if not 1 <= max_concurrent <= 64:
        raise SystemExit("MAX_CONCURRENT must be between 1 and 64")
    ca = env.get("SERVER_CA", "/etc/classroom-ai/server-ca.crt")
    config = Config(
        server_url=env["SERVER_URL"].rstrip("/"),
        join_token=env["JOIN_TOKEN"],
        backend_url=env["BACKEND_URL"].rstrip("/"),
        max_concurrent=max_concurrent,
        worker_url=env.get("WORKER_URL") or None,
        server_ca=ca if Path(ca).is_file() else None,
    )
    identity = load_identity(Path(env.get("IDENTITY_PATH", "/data/identity.json")))
    log.info("worker %s starting, backend %s", identity.worker_id, config.backend_url)
    return create_app(config, identity)
