"""Worker registry, heartbeat and routing. See docs/architecture.md "Worker trust".

A worker is live while it is not removed, its last heartbeat carried the
current join-token generation, and that heartbeat is under 45 seconds old.
"""

import asyncio
import contextlib
import dataclasses
import hmac
import ipaddress
import json
import secrets
import shlex
import socket
import urllib.parse
import uuid
from collections import Counter
from datetime import UTC, datetime, timedelta

import httpx
from psycopg.types.json import Jsonb
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from app import auth

AGENT_PORT = 8081
DOWN_AFTER = timedelta(seconds=45)
MAX_HEARTBEAT_BYTES = 64 * 1024
MAX_MODELS = 128
MAX_CAPACITY = 64
PROBE_TIMEOUT = httpx.Timeout(5)


class HeartbeatError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class Beat:
    worker_id: str
    api_key: str
    models: list[str]
    capacity: int
    worker_url: str | None


@dataclasses.dataclass(frozen=True)
class Worker:
    id: str
    address: str
    api_key: str
    models: list[str]
    capacity: int
    last_heartbeat: datetime
    removed: bool
    token_generation: int


def utcnow() -> datetime:
    return datetime.now(UTC)


# Join token


async def ensure_join_token(conn) -> None:
    await conn.execute(
        "INSERT INTO settings (key, value) VALUES ('join_token', %s) ON CONFLICT DO NOTHING",
        (Jsonb({"token": secrets.token_urlsafe(32), "generation": 1}),),
    )


async def join_token(conn, lock: str = "") -> dict:
    """The current {"token", "generation"}; lock is '', 'FOR SHARE' or 'FOR UPDATE'."""
    cur = await conn.execute(f"SELECT value FROM settings WHERE key = 'join_token' {lock}")
    row = await cur.fetchone()
    if row is None:
        raise RuntimeError("join token missing; ensure_join_token runs at startup")
    return row[0]


def token_matches(given: str, current: dict) -> bool:
    return hmac.compare_digest(given.encode(), current["token"].encode())


async def rotate_join_token(conn) -> dict:
    """Replace the token. Every worker is down until it heartbeats with the new one."""
    current = await join_token(conn, "FOR UPDATE")
    new = {"token": secrets.token_urlsafe(32), "generation": current["generation"] + 1}
    await conn.execute(
        "UPDATE settings SET value = %s, updated_at = now() WHERE key = 'join_token'", (Jsonb(new),)
    )
    return new


# Heartbeat parsing


def parse_beat(raw: bytes) -> Beat:
    try:
        data = json.loads(raw)
    except ValueError:
        raise HeartbeatError(400, "not JSON") from None
    if not isinstance(data, dict):
        raise HeartbeatError(400, "not an object")
    worker_id = data.get("worker_id")
    try:
        if not isinstance(worker_id, str) or str(uuid.UUID(worker_id)) != worker_id:
            raise ValueError
    except ValueError:
        raise HeartbeatError(400, "worker_id must be a lower-case UUID") from None
    api_key = data.get("api_key")
    if not isinstance(api_key, str) or not 32 <= len(api_key) <= 128 or not api_key.isascii():
        raise HeartbeatError(400, "bad api_key")
    models = data.get("models")
    if not isinstance(models, list) or not 1 <= len(models) <= MAX_MODELS:
        raise HeartbeatError(400, "bad models")
    if not all(isinstance(m, str) and 1 <= len(m) <= 255 for m in models):
        raise HeartbeatError(400, "bad model name")
    capacity = data.get("capacity")
    if type(capacity) is not int or not 1 <= capacity <= MAX_CAPACITY:
        raise HeartbeatError(400, f"capacity must be 1 to {MAX_CAPACITY}")
    worker_url = data.get("worker_url")
    if worker_url is not None and not isinstance(worker_url, str):
        raise HeartbeatError(400, "bad worker_url")
    return Beat(worker_id, api_key, list(dict.fromkeys(models)), capacity, worker_url or None)


async def read_body(request: Request, limit: int = MAX_HEARTBEAT_BYTES) -> bytes:
    if int(request.headers.get("content-length") or 0) > limit:
        raise HeartbeatError(413, "too large")
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            raise HeartbeatError(413, "too large")
    return bytes(body)


# Worker address


def agent_url(ip: str) -> str:
    host = f"[{ip}]" if ":" in ip else ip
    return f"http://{host}:{AGENT_PORT}"


def source_ip(request: Request) -> str:
    """The heartbeat's source. The app publishes no port, so requests arrive
    through Caddy, which overwrites X-Forwarded-For with the client address."""
    forwarded = request.headers.get("x-forwarded-for", "").strip()
    candidate = forwarded if forwarded and "," not in forwarded else ""
    with contextlib.suppress(ValueError):
        if candidate:
            return str(ipaddress.ip_address(candidate))
    return request.client.host if request.client else ""


def usable(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (addr.is_loopback or addr.is_unspecified or addr.is_link_local or addr.is_multicast)


def derived_address(ip: str) -> str:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        addr = None
    if addr is None or not usable(addr):
        raise HeartbeatError(400, f"heartbeat came from {ip or 'nowhere'}; set WORKER_URL on the worker")
    return agent_url(str(addr))


async def resolve(host: str, port: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def override_address(worker_url: str) -> str:
    """Check a WORKER_URL: plain http on the agent port, to a usable unicast host.

    Returns the address it checked, not the name, so a later DNS answer cannot
    send the server's requests somewhere else."""
    url = urllib.parse.urlsplit(worker_url)
    try:
        port = url.port
    except ValueError:
        port = None
    if (url.scheme != "http" or not url.hostname or url.username or url.password
            or url.query or url.fragment or url.path not in ("", "/") or port != AGENT_PORT):
        raise HeartbeatError(400, f"worker_url must be http://host:{AGENT_PORT}")
    try:
        addresses = await resolve(url.hostname, port)
    except OSError:
        raise HeartbeatError(400, "worker_url host does not resolve") from None
    if not addresses or not all(usable(ipaddress.ip_address(a)) for a in addresses):
        raise HeartbeatError(400, "worker_url is not a usable address")
    return agent_url(str(ipaddress.ip_address(addresses[0])))


async def probe(client: httpx.AsyncClient, address: str, api_key: str) -> None:
    """Confirm the server can reach the agent and the agent holds this key."""
    try:
        # Streamed and closed unread: only the status matters.
        async with client.stream(
            "GET", f"{address}/v1/models", headers={"authorization": f"Bearer {api_key}"},
            timeout=PROBE_TIMEOUT, follow_redirects=False,
        ) as response:
            status = response.status_code
    except httpx.HTTPError:
        raise HeartbeatError(422, f"the server cannot reach {address}; set WORKER_URL on the worker") from None
    if status != 200:
        raise HeartbeatError(422, f"{address} refused this worker's key")


# Registry


async def get_worker(conn, worker_id: str) -> Worker | None:
    cur = await conn.execute(
        "SELECT id, address, api_key, models, capacity, last_heartbeat, removed, token_generation"
        " FROM workers WHERE id = %s", (worker_id,))
    row = await cur.fetchone()
    return Worker(*row) if row else None


async def record_heartbeat(pool, request: Request, now: datetime, client: httpx.AsyncClient) -> None:
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    raw = await read_body(request)
    async with pool.connection() as conn:
        # Checked first so only a token holder can make the server probe an address.
        if not token_matches(token, await join_token(conn)):
            raise HeartbeatError(401, "unauthorised")
    beat = parse_beat(raw)
    if beat.worker_url:
        address = await override_address(beat.worker_url)
    else:
        address = derived_address(source_ip(request))
    async with pool.connection() as conn:
        known = await get_worker(conn, beat.worker_id)
    if known and known.removed:
        raise HeartbeatError(403, "worker removed")
    # The agent keeps its ID and key together, so a known ID with another key
    # is someone else claiming that worker.
    if known and not hmac.compare_digest(known.api_key, beat.api_key):
        raise HeartbeatError(409, "worker ID already joined with another key")
    if not known or known.address != address:
        await probe(client, address, beat.api_key)

    async with pool.connection() as conn, conn.transaction():
        # The share lock makes a concurrent rotation wait for this heartbeat to
        # commit, or this heartbeat wait for the rotation and then fail.
        current = await join_token(conn, "FOR SHARE")
        if not token_matches(token, current):
            raise HeartbeatError(401, "unauthorised")
        # Never touches removed: a removed row stays removed.
        cur = await conn.execute(
            "INSERT INTO workers (id, address, api_key, models, capacity, last_heartbeat, token_generation)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (id) DO UPDATE SET address = EXCLUDED.address, api_key = EXCLUDED.api_key,"
            "  models = EXCLUDED.models, capacity = EXCLUDED.capacity,"
            "  last_heartbeat = EXCLUDED.last_heartbeat, token_generation = EXCLUDED.token_generation"
            " WHERE NOT workers.removed AND workers.api_key = EXCLUDED.api_key RETURNING id",
            (beat.worker_id, address, beat.api_key, Jsonb(beat.models), beat.capacity, now,
             current["generation"]),
        )
        if await cur.fetchone() is None:
            raise HeartbeatError(403, "worker removed or joined with another key")
        # New models start disabled; a known model keeps its setting.
        await conn.execute(
            "INSERT INTO models (name, first_seen) SELECT unnest(%s::text[]), %s ON CONFLICT DO NOTHING",
            (beat.models, now),
        )


async def heartbeat(request: Request) -> Response:
    app = request.app
    try:
        await record_heartbeat(app.state.pool, request, app.state.clock(), app.state.worker_client)
    except HeartbeatError as e:
        return JSONResponse({"error": str(e)}, status_code=e.status)
    return Response(status_code=204)


async def remove_worker(conn, worker_id: str) -> bool:
    cur = await conn.execute("UPDATE workers SET removed = true WHERE id = %s RETURNING id", (worker_id,))
    return await cur.fetchone() is not None


def is_live(worker: Worker, generation: int, now: datetime) -> bool:
    return (not worker.removed and worker.token_generation == generation
            and now - worker.last_heartbeat < DOWN_AFTER)


async def list_workers(conn) -> list[Worker]:
    cur = await conn.execute(
        "SELECT id, address, api_key, models, capacity, last_heartbeat, removed, token_generation"
        " FROM workers ORDER BY id")
    return [Worker(*row) for row in await cur.fetchall()]


async def enabled_models(conn) -> set[str]:
    cur = await conn.execute("SELECT name FROM models WHERE enabled")
    return {row[0] for row in await cur.fetchall()}


# Admin views. Never include a worker's API key.


async def worker_list(conn, now: datetime, router: "Router") -> list[dict]:
    generation = (await join_token(conn))["generation"]
    return [{
        "id": w.id,
        "address": w.address,
        "status": "removed" if w.removed else "up" if is_live(w, generation, now) else "down",
        "models": w.models,
        "capacity": w.capacity,
        "in_flight": router.in_flight[w.id],
        "last_heartbeat": w.last_heartbeat.isoformat(),
    } for w in await list_workers(conn)]


async def model_list(conn, now: datetime) -> list[dict]:
    generation = (await join_token(conn))["generation"]
    offered = {m for w in await list_workers(conn) if is_live(w, generation, now) for m in w.models}
    cur = await conn.execute("SELECT name, enabled FROM models ORDER BY name")
    return [{"name": name, "enabled": enabled, "offered": name in offered}
            for name, enabled in await cur.fetchall()]


async def set_model(conn, name: str, enabled: bool) -> bool:
    cur = await conn.execute("UPDATE models SET enabled = %s WHERE name = %s RETURNING name", (enabled, name))
    return await cur.fetchone() is not None


# Join command


def powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def join_commands(server_url: str, token: str, ca_pem: str | None = None,
                  worker_url: str | None = None) -> dict[str, str]:
    """The one-line join command, in bash and PowerShell forms, run from the
    repository checkout on the worker."""
    env = {"SERVER_URL": server_url, "JOIN_TOKEN": token}
    if worker_url:
        env["WORKER_URL"] = worker_url
    up = "docker compose -f worker/compose.yml up -d --build"
    bash = " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + " " + up
    powershell = "; ".join(f"$env:{k}={powershell_quote(v)}" for k, v in env.items()) + "; " + up
    if ca_pem:
        bash = f"printf '%s\\n' {shlex.quote(ca_pem.strip())} > worker/server-ca.crt && {bash}"
        powershell = (f"Set-Content -Path worker/server-ca.crt -Value {powershell_quote(ca_pem.strip())}; "
                      + powershell)
    return {"bash": bash, "powershell": powershell}


# Routing


class NoWorker(Exception):
    pass


class Router:
    """Counts this process's requests in flight per worker and leases the
    live worker with the most spare capacity.

    ponytail: the count is per process. The app runs one uvicorn process;
    a second process needs a shared counter (see docs/architecture.md).
    """

    def __init__(self):
        self.in_flight: Counter[str] = Counter()

    def pick(self, workers: list[Worker], generation: int, enabled: set[str], model: str,
             now: datetime) -> Worker:
        if model not in enabled:
            raise NoWorker(model)
        best = None
        for w in workers:
            spare = w.capacity - self.in_flight[w.id]
            if model in w.models and spare > 0 and is_live(w, generation, now):
                # Most spare capacity; the lowest ID breaks a tie.
                if best is None or spare > best[0] or (spare == best[0] and w.id < best[1].id):
                    best = (spare, w)
        if best is None:
            raise NoWorker(model)
        return best[1]

    @contextlib.asynccontextmanager
    async def lease(self, pool, model: str, now: datetime):
        async with pool.connection() as conn:
            workers = await list_workers(conn)
            generation = (await join_token(conn))["generation"]
            enabled = await enabled_models(conn)
        # No await between pick and the increment, so two requests cannot
        # both take a worker's last slot.
        worker = self.pick(workers, generation, enabled, model, now)
        self.in_flight[worker.id] += 1
        try:
            yield worker
        finally:
            self.in_flight[worker.id] -= 1


# Staff endpoints. Admin only, except models, which teachers manage too (R3.1).

SAME_HOST = {"localhost", "127.0.0.1", "::1"}


async def json_object(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > auth.MAX_BODY:
        raise auth.HTTPError(413, "too_large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise auth.HTTPError(400, "bad_request") from None
    if not isinstance(body, dict):
        raise auth.HTTPError(400, "bad_request")
    return body


async def get_workers(request: Request) -> JSONResponse:
    await auth.require_staff(request, "admin")
    async with request.app.state.pool.connection() as conn:
        listed = await worker_list(conn, request.app.state.clock(), request.app.state.router)
    return JSONResponse({"workers": listed})


async def get_join(request: Request) -> JSONResponse:
    await auth.require_staff(request, "admin")
    scheme = "https" if auth.is_https(request) else "http"
    host = request.url.hostname or "localhost"
    worker_url = None
    if host in SAME_HOST:
        # The admin is on the server itself; a worker there reaches the
        # server, and the server the worker, through the Docker host.
        host, worker_url = "host.docker.internal", f"http://host.docker.internal:{AGENT_PORT}"
    port = f":{request.url.port}" if request.url.port else ""
    async with request.app.state.pool.connection() as conn:
        token = (await join_token(conn))["token"]
    commands = join_commands(f"{scheme}://{host}{port}", token, worker_url=worker_url)
    return JSONResponse(commands, headers={"Cache-Control": "no-store"})


async def post_remove(request: Request) -> JSONResponse:
    admin = await auth.require_staff(request, "admin")
    body = await json_object(request)
    rotate = body.get("rotate", False)
    if not isinstance(rotate, bool):
        raise auth.HTTPError(400, "bad_request")
    worker_id = request.path_params["worker_id"]
    async with request.app.state.pool.connection() as conn, conn.transaction():
        # Token row before worker row, the order a heartbeat takes them in,
        # so the two can never deadlock.
        if rotate:
            await rotate_join_token(conn)
        if not await remove_worker(conn, worker_id):
            raise auth.HTTPError(404, "not_found")
        await auth.audit(conn, admin["id"], admin["username"], "worker.remove",
                         {"worker_id": worker_id, "rotated_join_token": rotate})
    return JSONResponse({"removed": worker_id})


async def post_rotate(request: Request) -> JSONResponse:
    admin = await auth.require_staff(request, "admin")
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await rotate_join_token(conn)
        await auth.audit(conn, admin["id"], admin["username"], "workers.rotate_join_token", {})
    return JSONResponse({"rotated": True})


async def get_models(request: Request) -> JSONResponse:
    await auth.require_staff(request, "teacher")
    async with request.app.state.pool.connection() as conn:
        return JSONResponse({"models": await model_list(conn, request.app.state.clock())})


async def put_model(request: Request) -> JSONResponse:
    staff = await auth.require_staff(request, "teacher")
    enabled = (await json_object(request)).get("enabled")
    if not isinstance(enabled, bool):
        raise auth.HTTPError(400, "bad_request")
    name = request.path_params["name"]
    async with request.app.state.pool.connection() as conn, conn.transaction():
        if not await set_model(conn, name, enabled):
            raise auth.HTTPError(404, "not_found")
        await auth.audit(conn, staff["id"], staff["username"], "model.enable" if enabled else "model.disable",
                         {"model": name})
    return JSONResponse({"name": name, "enabled": enabled})


routes = [
    Route("/api/workers/heartbeat", heartbeat, methods=["POST"]),
    Route("/api/workers", get_workers, methods=["GET"]),
    Route("/api/workers/join", get_join, methods=["GET"]),
    Route("/api/workers/rotate", post_rotate, methods=["POST"]),
    Route("/api/workers/{worker_id}/remove", post_remove, methods=["POST"]),
    Route("/api/models", get_models, methods=["GET"]),
    Route("/api/models/{name:path}", put_model, methods=["PUT"]),
]
