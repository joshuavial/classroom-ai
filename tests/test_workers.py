"""Worker registry: heartbeat, join token, deny list, down detection, routing."""

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest

from app import workers
from app.main import create_app
from tests.fakes import fake_worker

T0 = datetime(2026, 10, 10, 9, 0, tzinfo=UTC)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


class Agents(httpx.AsyncBaseTransport):
    """Routes app-to-worker calls by host to fake workers; unknown hosts refuse."""

    def __init__(self):
        self.by_host: dict[str, httpx.ASGITransport] = {}
        self.calls: list[str] = []

    def add(self, host: str, key: str, models=None):
        self.by_host[host] = httpx.ASGITransport(app=fake_worker(models=models, api_key=key))

    async def handle_async_request(self, request):
        self.calls.append(str(request.url))
        transport = self.by_host.get(request.url.host)
        if transport is None or request.url.port != 8081:
            raise httpx.ConnectError("refused", request=request)
        return await transport.handle_async_request(request)


class Env:
    def __init__(self, app, dsn, agents, clock):
        self.app, self.dsn, self.agents, self.clock = app, dsn, agents, clock

    @property
    def pool(self):
        return self.app.state.pool

    async def token(self) -> str:
        async with self.pool.connection() as conn:
            return (await workers.join_token(conn))["token"]

    async def beat(self, worker_id, key, models=("gemma-4-e2b-it",), capacity=4, token=None,
                   source="10.0.0.5", worker_url=None, body=None):
        if token is None:
            token = await self.token()
        payload = {"worker_id": worker_id, "api_key": key, "models": list(models), "capacity": capacity}
        if worker_url:
            payload["worker_url"] = worker_url
        transport = httpx.ASGITransport(app=self.app, client=("172.18.0.2", 40000))
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            return await c.post(
                "/api/workers/heartbeat",
                content=body if body is not None else json.dumps(payload),
                headers={"authorization": f"Bearer {token}", "x-forwarded-for": source,
                         "content-type": "application/json"},
            )

    async def join(self, host="10.0.0.5", key=None, **kw):
        """Start a fake agent at host and heartbeat it in. Returns (id, key)."""
        worker_id, key = str(uuid.uuid4()), key or ("k" * 40 + host[-3:])
        self.agents.add(host, key)
        r = await self.beat(worker_id, key, source=host, **kw)
        assert r.status_code == 204, r.text
        return worker_id, key

    async def worker(self, worker_id) -> workers.Worker | None:
        async with self.pool.connection() as conn:
            return await workers.get_worker(conn, worker_id)

    async def live(self, worker_id) -> bool:
        async with self.pool.connection() as conn:
            w = await workers.get_worker(conn, worker_id)
            generation = (await workers.join_token(conn))["generation"]
        return w is not None and workers.is_live(w, generation, self.clock())

    async def sql(self, query, params=()):
        async with self.pool.connection() as conn:
            cur = await conn.execute(query, params)
            return await cur.fetchall() if cur.description else None


@pytest.fixture
async def env(empty_dsn):
    agents, clock = Agents(), Clock()
    app = create_app(empty_dsn, worker_transport=agents, clock=clock)
    async with app.router.lifespan_context(app):
        yield Env(app, empty_dsn, agents, clock)


# Heartbeat


async def test_heartbeat_registers_worker_with_models_disabled(env):
    worker_id, key = await env.join()
    w = await env.worker(worker_id)
    assert (w.address, w.api_key, w.models, w.capacity, w.last_heartbeat, w.removed) == (
        "http://10.0.0.5:8081", key, ["gemma-4-e2b-it"], 4, T0, False)
    assert await env.live(worker_id)
    assert await env.sql("SELECT name, enabled FROM models") == [("gemma-4-e2b-it", False)]
    assert env.agents.calls == ["http://10.0.0.5:8081/v1/models"]


async def test_repeat_heartbeat_skips_probe_and_updates(env):
    worker_id, key = await env.join()
    env.clock.now = T0 + timedelta(seconds=15)
    r = await env.beat(worker_id, key, models=["a", "b", "a"], capacity=2)
    assert r.status_code == 204
    w = await env.worker(worker_id)
    assert (w.models, w.capacity, w.last_heartbeat) == (["a", "b"], 2, T0 + timedelta(seconds=15))
    assert len(env.agents.calls) == 1


@pytest.mark.parametrize("token", ["", "wrong", None])
async def test_bad_join_token_rejected_without_probe(env, token):
    env.agents.add("10.0.0.5", "k" * 40)
    if token is None:  # the right token in the wrong scheme
        token = "x"
        headers_token = await env.token()
        transport = httpx.ASGITransport(app=env.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/api/workers/heartbeat", json={}, headers={"authorization": f"Basic {headers_token}"})
    else:
        r = await env.beat(str(uuid.uuid4()), "k" * 40, token=token)
    assert r.status_code == 401
    assert env.agents.calls == []
    assert await env.sql("SELECT count(*) FROM workers") == [(0,)]


@pytest.mark.parametrize("change", [
    {"worker_id": "not-a-uuid"},
    {"worker_id": "6F1C3A9E-2B7D-4A52-9E4F-0D1B2C3E4F50"},
    {"api_key": "short"},
    {"api_key": 7},
    {"models": []},
    {"models": "gemma"},
    {"models": [""]},
    {"models": ["x" * 256]},
    {"models": [f"m{i}" for i in range(129)]},
    {"capacity": 0},
    {"capacity": 65},
    {"capacity": True},
    {"capacity": "4"},
    {"worker_url": 5},
])
async def test_malformed_heartbeat_rejected(env, change):
    payload = {"worker_id": str(uuid.uuid4()), "api_key": "k" * 40, "models": ["m"], "capacity": 4, **change}
    r = await env.beat(None, None, body=json.dumps(payload))
    assert r.status_code == 400
    assert await env.sql("SELECT count(*) FROM workers") == [(0,)]


@pytest.mark.parametrize("body", [b"not json", b"[]", b"null"])
async def test_non_object_heartbeat_rejected(env, body):
    assert (await env.beat(None, None, body=body)).status_code == 400


async def test_oversized_heartbeat_rejected(env):
    r = await env.beat(None, None, body=b"{" + b" " * (64 * 1024) + b"}")
    assert r.status_code == 413


async def test_unreachable_worker_not_registered(env):
    r = await env.beat(str(uuid.uuid4()), "k" * 40, source="10.0.0.9")
    assert r.status_code == 422
    assert await env.sql("SELECT count(*) FROM workers") == [(0,)]


async def test_worker_with_other_key_not_registered(env):
    env.agents.add("10.0.0.5", "a" * 40)
    r = await env.beat(str(uuid.uuid4()), "b" * 40)
    assert r.status_code == 422


async def test_worker_url_cannot_claim_another_agent(env):
    await env.join("10.0.0.5", key="a" * 40)
    r = await env.beat(str(uuid.uuid4()), "b" * 40, source="10.0.0.6",
                       worker_url="http://10.0.0.5:8081")
    assert r.status_code == 422


async def test_worker_url_used_when_given(env):
    env.agents.add("10.0.0.7", "k" * 40)
    worker_id = str(uuid.uuid4())
    r = await env.beat(worker_id, "k" * 40, source="172.18.0.1", worker_url="http://10.0.0.7:8081/")
    assert r.status_code == 204
    assert (await env.worker(worker_id)).address == "http://10.0.0.7:8081"


@pytest.mark.parametrize("url", [
    "https://10.0.0.7:8081", "http://10.0.0.7", "http://10.0.0.7:8080", "http://u:p@10.0.0.7:8081",
    "http://10.0.0.7:8081/x", "http://10.0.0.7:8081?q", "http://127.0.0.1:8081", "http://0.0.0.0:8081",
    "http://169.254.1.1:8081", "http://224.0.0.1:8081", "http://[::1]:8081", "http://localhost:8081",
    "http://no-such-host.invalid:8081", "http://10.0.0.7:x",
])
async def test_bad_worker_url_rejected(env, url):
    env.agents.add("10.0.0.7", "k" * 40)
    r = await env.beat(str(uuid.uuid4()), "k" * 40, worker_url=url)
    assert r.status_code == 400
    assert env.agents.calls == []


@pytest.mark.parametrize("forwarded,expected", [
    ("10.0.0.5", "http://10.0.0.5:8081"),
    ("fd00::5", "http://[fd00::5]:8081"),
    ("10.0.0.5, 10.0.0.6", "http://172.18.0.2:8081"),  # not exactly one address: socket peer
    ("garbage", "http://172.18.0.2:8081"),
])
async def test_source_address(env, forwarded, expected):
    host = expected.split("//")[1].rsplit(":", 1)[0].strip("[]")
    env.agents.add(host, "k" * 40)
    worker_id = str(uuid.uuid4())
    assert (await env.beat(worker_id, "k" * 40, source=forwarded)).status_code == 204
    assert (await env.worker(worker_id)).address == expected


@pytest.mark.parametrize("forwarded", ["0.0.0.0", "224.0.0.1", "127.0.0.1", "169.254.0.9"])
async def test_unusable_source_address_rejected(env, forwarded):
    assert (await env.beat(str(uuid.uuid4()), "k" * 40, source=forwarded)).status_code == 400


async def test_known_id_with_another_key_refused(env):
    worker_id, key = await env.join("10.0.0.5")
    env.agents.add("10.0.0.6", "t" * 40)
    r = await env.beat(worker_id, "t" * 40, source="10.0.0.6")
    assert r.status_code == 409
    w = await env.worker(worker_id)
    assert (w.api_key, w.address) == (key, "http://10.0.0.5:8081")


async def test_rejection_says_what_to_fix(env):
    r = await env.beat(str(uuid.uuid4()), "k" * 40, source="10.0.0.9")
    assert r.json() == {"error": "the server cannot reach http://10.0.0.9:8081; set WORKER_URL on the worker"}
    assert "k" * 40 not in r.text


async def test_changed_address_is_probed_again(env):
    worker_id, key = await env.join("10.0.0.5")
    r = await env.beat(worker_id, key, source="10.0.0.6")
    assert r.status_code == 422  # nothing answers at the new address
    assert (await env.worker(worker_id)).address == "http://10.0.0.5:8081"


# Deny list and rotation


async def test_removed_worker_rejected_and_stays_removed(env):
    worker_id, key = await env.join("10.0.0.5")
    other, _ = await env.join("10.0.0.6")
    async with env.pool.connection() as conn:
        assert await workers.remove_worker(conn, worker_id)
    env.clock.now = T0 + timedelta(seconds=15)
    assert (await env.beat(worker_id, key)).status_code == 403
    w = await env.worker(worker_id)
    assert w.removed and w.last_heartbeat == T0
    assert not await env.live(worker_id)
    assert await env.live(other)  # removing one worker leaves the rest serving


async def test_rotation_drops_workers_until_rejoined(env):
    worker_id, key = await env.join()
    old = await env.token()
    async with env.pool.connection() as conn:
        new = await workers.rotate_join_token(conn)
    assert new["token"] != old and new["generation"] == 2
    assert not await env.live(worker_id)
    assert (await env.beat(worker_id, key, token=old)).status_code == 401
    assert (await env.beat(str(uuid.uuid4()), "n" * 40, token=old)).status_code == 401
    assert (await env.beat(worker_id, key)).status_code == 204
    assert await env.live(worker_id)


async def test_remove_and_rotate_denies_a_recreated_identity(env):
    worker_id, key = await env.join()
    old = await env.token()
    async with env.pool.connection() as conn, conn.transaction():
        await workers.remove_worker(conn, worker_id)
        await workers.rotate_join_token(conn)
    # The machine wipes its identity and comes back with a new ID and the old token.
    assert (await env.beat(str(uuid.uuid4()), "z" * 40, token=old)).status_code == 401


async def test_heartbeat_waits_for_a_rotation_in_progress_then_fails(env):
    worker_id, key = await env.join()
    async with await psycopg.AsyncConnection.connect(env.dsn) as conn:
        async with conn.transaction():
            await workers.rotate_join_token(conn)
            beat = asyncio.create_task(env.beat(worker_id, key))
            await asyncio.sleep(0.3)
            assert not beat.done()  # blocked on the token row
        assert (await beat).status_code == 401
    assert not await env.live(worker_id)


async def test_rotation_waits_for_a_heartbeat_in_progress(env):
    worker_id, key = await env.join()
    # Hold the share lock a heartbeat takes, as if one were mid-transaction.
    async with await psycopg.AsyncConnection.connect(env.dsn) as hb:
        async with hb.transaction():
            async with hb.cursor() as cur:
                await cur.execute("SELECT value FROM settings WHERE key = 'join_token' FOR SHARE")

            async def rotate():
                async with env.pool.connection() as conn:
                    return await workers.rotate_join_token(conn)

            rotation = asyncio.create_task(rotate())
            await asyncio.sleep(0.3)
            assert not rotation.done()
        await rotation
    assert not await env.live(worker_id)  # the earlier heartbeat's generation is stale


async def test_heartbeat_waits_for_a_removal_in_progress_then_fails(env):
    worker_id, key = await env.join()
    env.clock.now = T0 + timedelta(seconds=15)
    async with await psycopg.AsyncConnection.connect(env.dsn) as conn:
        async with conn.transaction():
            await workers.remove_worker(conn, worker_id)
            # Already removed? No: not committed, so the heartbeat passes the
            # early check and blocks on the row in its upsert.
            beat = asyncio.create_task(env.beat(worker_id, key))
            await asyncio.sleep(0.3)
            assert not beat.done()
        assert (await beat).status_code == 403
    w = await env.worker(worker_id)
    assert w.removed and w.last_heartbeat == T0


async def test_removal_after_heartbeat_wins(env):
    worker_id, key = await env.join()
    async with env.pool.connection() as conn:
        await workers.remove_worker(conn, worker_id)
    assert (await env.worker(worker_id)).removed


# Models


async def test_rediscovered_model_keeps_its_setting(env):
    worker_id, key = await env.join()
    await env.sql("UPDATE models SET enabled = true")
    assert (await env.beat(worker_id, key, models=["gemma-4-e2b-it", "new"])).status_code == 204
    assert sorted(await env.sql("SELECT name, enabled FROM models")) == [
        ("gemma-4-e2b-it", True), ("new", False)]
    await env.sql("UPDATE models SET enabled = false WHERE name = 'gemma-4-e2b-it'")
    assert (await env.beat(worker_id, key)).status_code == 204
    assert sorted(await env.sql("SELECT name, enabled FROM models")) == [
        ("gemma-4-e2b-it", False), ("new", False)]


# Down detection, with the fake clock


async def test_worker_down_after_45_seconds(env):
    worker_id, _ = await env.join()
    env.clock.now = T0 + timedelta(seconds=44, microseconds=999_999)
    assert await env.live(worker_id)
    env.clock.now = T0 + timedelta(seconds=45)
    assert not await env.live(worker_id)


async def test_lease_skips_a_down_worker_and_fails_over(env):
    first, _ = await env.join("10.0.0.5")
    env.clock.now = T0 + timedelta(seconds=30)
    second, key = await env.join("10.0.0.6")
    await env.sql("UPDATE models SET enabled = true")
    router = env.app.state.router
    env.clock.now = T0 + timedelta(seconds=50)
    async with router.lease(env.pool, "gemma-4-e2b-it", env.clock()) as w:
        assert w.id == second
    env.clock.now = T0 + timedelta(seconds=80)
    with pytest.raises(workers.NoWorker):
        async with router.lease(env.pool, "gemma-4-e2b-it", env.clock()):
            pass


# Routing (no database)


def w(id, capacity=4, models=("m",), last=T0, removed=False, generation=1):
    return workers.Worker(id, f"http://{id}:8081", "k" * 40, list(models), capacity, last, removed, generation)


def test_pick_most_spare_capacity():
    router = workers.Router()
    pool = [w("a", 4), w("b", 4), w("c", 2)]
    router.in_flight.update({"a": 3, "b": 1})
    assert router.pick(pool, 1, {"m"}, "m", T0).id == "b"
    router.in_flight["b"] = 3
    assert router.pick(pool, 1, {"m"}, "m", T0).id == "c"


def test_pick_tie_breaks_on_lowest_id():
    assert workers.Router().pick([w("b"), w("a")], 1, {"m"}, "m", T0).id == "a"


@pytest.mark.parametrize("pool,enabled", [
    ([w("a")], set()),                                    # model disabled
    ([w("a", models=("other",))], {"m"}),                 # not offered
    ([w("a", removed=True)], {"m"}),
    ([w("a", generation=0)], {"m"}),                      # joined with an old token
    ([w("a", last=T0 - timedelta(seconds=45))], {"m"}),   # down
    ([], {"m"}),
])
def test_pick_finds_no_worker(pool, enabled):
    with pytest.raises(workers.NoWorker):
        workers.Router().pick(pool, 1, enabled, "m", T0)


def test_saturated_worker_skipped():
    router = workers.Router()
    router.in_flight["a"] = 2
    with pytest.raises(workers.NoWorker):
        router.pick([w("a", 2)], 1, {"m"}, "m", T0)


async def test_lease_released_after_error_and_cancellation(env):
    worker_id, _ = await env.join()
    await env.sql("UPDATE models SET enabled = true")
    router = workers.Router()
    with pytest.raises(RuntimeError):
        async with router.lease(env.pool, "gemma-4-e2b-it", T0):
            assert router.in_flight[worker_id] == 1
            raise RuntimeError
    assert router.in_flight[worker_id] == 0

    entered = asyncio.Event()

    async def hold():
        async with router.lease(env.pool, "gemma-4-e2b-it", T0):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(hold())
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert router.in_flight[worker_id] == 0


async def test_simultaneous_leases_never_exceed_capacity(env):
    worker_id, key = await env.join()
    assert (await env.beat(worker_id, key, capacity=3)).status_code == 204
    await env.sql("UPDATE models SET enabled = true")
    router = workers.Router()
    release = asyncio.Event()
    got = []

    async def one():
        try:
            async with router.lease(env.pool, "gemma-4-e2b-it", T0):
                got.append(router.in_flight[worker_id])
                await release.wait()
        except workers.NoWorker:
            got.append("none")

    tasks = [asyncio.create_task(one()) for _ in range(5)]
    while len(got) < 5:
        await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(*tasks)
    assert sorted(got, key=str) == [1, 2, 3, "none", "none"]
    assert router.in_flight[worker_id] == 0
