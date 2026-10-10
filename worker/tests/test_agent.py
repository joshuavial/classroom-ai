import asyncio
import json
import os
import stat

import httpx
import pytest

import agent

KEY = "k" * 43
CONFIG = agent.Config(
    server_url="http://server", join_token="join-secret", backend_url="http://backend"
)
IDENTITY = agent.Identity("6f1c3a9e-2b7d-4a52-9e4f-0d1b2c3e4f50", KEY)
MODELS = {"object": "list", "data": [{"id": "gemma-4-e2b-it", "object": "model"}]}


async def streamed(data: bytes):
    yield data


def json_stream(status: int, payload) -> httpx.Response:
    """A reply that arrives as a stream, as a real backend's does."""
    return httpx.Response(status, headers={"content-type": "application/json"},
                          content=streamed(json.dumps(payload).encode()))


def backend_client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def call(app, method: str, path: str, headers=None, body: bytes = b""):
    """Drive the ASGI app directly so tests see each chunk as it is sent."""
    sent: list[dict] = []
    first_chunk = asyncio.Event()
    received_body = False

    async def receive():
        nonlocal received_body
        if not received_body:
            received_body = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()  # no disconnect unless the test cancels

    async def send(message):
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            first_chunk.set()

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
        "method": method, "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "scheme": "http", "server": ("agent", 8081), "client": ("10.0.0.5", 5000),
        "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        "app": app,
    }
    task = asyncio.create_task(app(scope, receive, send))
    return task, sent, first_chunk


async def run(app, method, path, headers=None, body=b""):
    task, sent, _ = await call(app, method, path, headers, body)
    await task
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], dict((k.decode(), v.decode()) for k, v in start["headers"]), data


def make_app(handler, **kw):
    app = agent.create_app(CONFIG, IDENTITY, backend=backend_client(handler), heartbeat=False, **kw)
    app.state.backend = backend_client(handler)
    return app


AUTH = {"authorization": f"Bearer {KEY}"}


@pytest.mark.parametrize("header", [None, "", "Bearer", f"Bearer {KEY}x", f"bearer {KEY}", KEY, "Basic abc"])
async def test_request_without_the_key_is_refused_before_forwarding(header):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=MODELS)

    app = make_app(handler)
    headers = {} if header is None else {"authorization": header}
    for method, path in (("GET", "/v1/models"), ("POST", "/v1/chat/completions")):
        status, _, _ = await run(app, method, path, headers, b'{"model":"m"}')
        assert status == 401
    assert calls == []


async def test_models_forwarded_without_worker_key():
    seen = {}

    def handler(request):
        seen["auth"] = request.headers.get("authorization")
        seen["url"] = str(request.url)
        return json_stream(200, MODELS)

    status, headers, data = await run(make_app(handler), "GET", "/v1/models", AUTH)
    assert status == 200
    assert headers["content-type"] == "application/json"
    assert json.loads(data) == MODELS
    assert seen == {"auth": None, "url": "http://backend/v1/models"}


async def test_chat_streams_first_chunk_before_backend_finishes():
    release = asyncio.Event()
    closed = asyncio.Event()
    seen = {}

    async def chunks():
        try:
            yield b"data: one\n\n"
            await release.wait()
            yield b"data: two\n\n"
        finally:
            closed.set()

    async def handler(request):
        seen["body"] = await request.aread()
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())

    app = make_app(handler)
    task, sent, first = await call(app, "POST", "/v1/chat/completions",
                                   {**AUTH, "content-type": "application/json"}, b'{"model":"m"}')
    await asyncio.wait_for(first.wait(), 2)
    assert not task.done()
    start = sent[0]
    assert start["status"] == 200
    assert (b"content-type", b"text/event-stream") in start["headers"]
    release.set()
    await asyncio.wait_for(task, 2)
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    assert body == b"data: one\n\ndata: two\n\n"
    assert seen == {"body": b'{"model":"m"}', "auth": None}
    assert closed.is_set()


async def test_backend_status_passed_through():
    app = make_app(lambda r: json_stream(404, {"error": "no model"}))
    status, _, data = await run(app, "POST", "/v1/chat/completions", AUTH, b"{}")
    assert status == 404 and json.loads(data) == {"error": "no model"}


@pytest.mark.parametrize("exc,status", [
    (httpx.ConnectError("refused"), 502),
    (httpx.ReadTimeout("slow"), 504),
    (httpx.ConnectTimeout("slow"), 504),
])
async def test_failure_before_reply_maps_to_gateway_error(exc, status):
    def handler(request):
        raise exc

    got, _, _ = await run(make_app(handler), "POST", "/v1/chat/completions", AUTH, b"{}")
    assert got == status


async def test_failure_mid_stream_ends_the_stream_and_closes_upstream():
    closed = asyncio.Event()

    async def chunks():
        try:
            yield b"data: one\n\n"
            raise httpx.ReadTimeout("stalled")
        finally:
            closed.set()

    app = make_app(lambda r: httpx.Response(200, content=chunks()))
    task, sent, _ = await call(app, "POST", "/v1/chat/completions", AUTH, b"{}")
    with pytest.raises(Exception):
        await asyncio.wait_for(task, 2)
    bodies = [m for m in sent if m["type"] == "http.response.body"]
    assert bodies[0]["body"] == b"data: one\n\n"
    assert not any(m.get("more_body") is False for m in bodies)  # never ended cleanly
    assert closed.is_set()


async def test_client_going_away_closes_upstream():
    closed = asyncio.Event()

    async def chunks():
        try:
            yield b"data: one\n\n"
            await asyncio.Event().wait()
        finally:
            closed.set()

    app = make_app(lambda r: httpx.Response(200, content=chunks()))
    task, _, first = await call(app, "POST", "/v1/chat/completions", AUTH, b"{}")
    await asyncio.wait_for(first.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await asyncio.wait_for(closed.wait(), 2)


async def test_other_paths_not_served():
    app = make_app(lambda r: httpx.Response(200))
    status, _, _ = await run(app, "GET", "/slots", AUTH)
    assert status == 404


# Identity


def test_identity_created_once_and_reused(tmp_path):
    path = tmp_path / "data" / "identity.json"
    first = agent.load_identity(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert len(first.api_key) >= 32
    assert agent.load_identity(path) == first
    assert [p.name for p in path.parent.iterdir()] == ["identity.json"]


@pytest.mark.parametrize("content", ["", "{", "[]", '{"worker_id": "x", "api_key": "' + "a" * 43 + '"}',
                                     '{"worker_id": "6f1c3a9e-2b7d-4a52-9e4f-0d1b2c3e4f50", "api_key": "short"}',
                                     '{"worker_id": "6f1c3a9e-2b7d-4a52-9e4f-0d1b2c3e4f50"}'])
def test_corrupt_identity_fails_closed(tmp_path, content):
    path = tmp_path / "identity.json"
    path.write_text(content)
    with pytest.raises(agent.IdentityError):
        agent.load_identity(path)
    assert path.read_text() == content  # never replaced by a new identity


# Heartbeat


@pytest.mark.parametrize("payload", [
    [], {"data": "x"}, {"data": []}, {"data": [{"id": ""}]}, {"data": [{"id": 3}]},
    {"data": ["m"]}, {"data": [{"id": "x" * 256}]},
    {"data": [{"id": f"m{i}"} for i in range(129)]},
])
def test_malformed_model_list_rejected(payload):
    with pytest.raises(ValueError):
        agent.parse_models(payload)


def test_model_list_deduplicated():
    assert agent.parse_models({"data": [{"id": "a"}, {"id": "b"}, {"id": "a"}]}) == ["a", "b"]


async def test_heartbeat_payload_built_from_backend():
    backend = backend_client(lambda r: httpx.Response(200, json={
        "object": "list", "data": [{"id": "gemma-4-e2b-it"}, {"id": "gemma-4-e4b-it"}]}))
    beat = await agent.build_heartbeat(CONFIG, IDENTITY, backend)
    assert beat == {
        "worker_id": IDENTITY.worker_id, "api_key": KEY,
        "models": ["gemma-4-e2b-it", "gemma-4-e4b-it"], "capacity": 4,
    }
    config = agent.Config("http://server", "j", "http://backend", 2, "http://host.docker.internal:8081")
    beat = await agent.build_heartbeat(config, IDENTITY, backend)
    assert beat["capacity"] == 2 and beat["worker_url"] == "http://host.docker.internal:8081"


async def test_heartbeat_posts_with_join_token():
    posted = []

    def server_handler(request):
        posted.append(request)
        return httpx.Response(204)

    await agent.send_heartbeat(CONFIG, IDENTITY, backend_client(lambda r: httpx.Response(200, json=MODELS)),
                               backend_client(server_handler))
    (request,) = posted
    assert str(request.url) == "http://server/api/workers/heartbeat"
    assert request.headers["authorization"] == "Bearer join-secret"
    assert json.loads(request.content)["models"] == ["gemma-4-e2b-it"]


async def test_heartbeat_loop_skips_beats_while_backend_is_down_and_logs_no_secrets(caplog):
    backend_up = [False, True, False]
    posted = []
    sleeps = []

    def backend_handler(request):
        return httpx.Response(200, json=MODELS) if backend_up[len(sleeps)] else httpx.Response(500)

    def server_handler(request):
        posted.append(json.loads(request.content))
        return httpx.Response(401, json={"error": "bad token"})  # also a rejected beat

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await agent.heartbeat_loop(CONFIG, IDENTITY, backend_client(backend_handler),
                                   backend_client(server_handler), sleep=fake_sleep)
    assert sleeps == [15, 15, 15]
    assert len(posted) == 1
    assert "heartbeat failed: 401 bad token" in caplog.text
    assert KEY not in caplog.text and "join-secret" not in caplog.text


async def test_heartbeat_loop_survives_unexpected_errors(caplog):
    sleeps = []

    def broken(request):
        raise RuntimeError("surprise")

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 2:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await agent.heartbeat_loop(CONFIG, IDENTITY, backend_client(broken),
                                   backend_client(broken), sleep=fake_sleep)
    assert sleeps == [15, 15]
    assert "heartbeat failed: RuntimeError" in caplog.text


async def test_lifespan_starts_and_stops_heartbeat():
    posted = asyncio.Event()

    def server_handler(request):
        posted.set()
        return httpx.Response(204)

    app = agent.create_app(CONFIG, IDENTITY, backend=backend_client(lambda r: httpx.Response(200, json=MODELS)),
                           server=backend_client(server_handler))
    async with app.router.lifespan_context(app):
        await asyncio.wait_for(posted.wait(), 2)


def test_from_env_requires_settings(monkeypatch, tmp_path):
    for k in ("SERVER_URL", "JOIN_TOKEN", "BACKEND_URL"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(SystemExit):
        agent.from_env()
    monkeypatch.setenv("SERVER_URL", "http://s/")
    monkeypatch.setenv("JOIN_TOKEN", "j")
    monkeypatch.setenv("BACKEND_URL", "http://b")
    monkeypatch.setenv("IDENTITY_PATH", str(tmp_path / "id.json"))
    monkeypatch.setenv("MAX_CONCURRENT", "0")
    with pytest.raises(SystemExit):
        agent.from_env()
    monkeypatch.setenv("MAX_CONCURRENT", "8")
    assert agent.from_env() is not None


def test_server_root_certificate_is_trusted_when_present(monkeypatch, tmp_path):
    import subprocess
    ca = tmp_path / "server-ca.crt"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:P-256",
                    "-nodes", "-keyout", str(tmp_path / "k"), "-out", str(ca), "-subj", "/CN=test root",
                    "-days", "1"], check=True, capture_output=True)
    for k, v in {"SERVER_URL": "https://s", "JOIN_TOKEN": "j", "BACKEND_URL": "http://b",
                 "IDENTITY_PATH": str(tmp_path / "id.json")}.items():
        monkeypatch.setenv(k, v)
    created = {}
    monkeypatch.setattr(agent, "create_app", lambda config, identity: created.setdefault("c", config))
    monkeypatch.setenv("SERVER_CA", str(ca))
    agent.from_env()
    config = created.pop("c")
    assert config.server_ca == str(ca)
    context = agent.server_verify(config)
    assert context.cert_store_stats()["x509_ca"] >= 1
    monkeypatch.setenv("SERVER_CA", str(tmp_path / "missing.crt"))
    agent.from_env()
    assert created["c"].server_ca is None and agent.server_verify(created["c"]) is True
