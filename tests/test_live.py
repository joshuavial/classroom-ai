"""Live events for the teacher console (step 7, slice A)."""

import asyncio
import json
import logging

import psycopg
import pytest

from app import auth, live
from app.main import create_app
from tests.helpers import make_staff, new_client, post, sign_in
from tests.test_classes import join, new_lesson


@pytest.fixture
async def teacher(client, dsn):
    await sign_in(client, await make_staff(dsn, "teacher"))
    return client


async def next_event(queue, timeout=5.0) -> dict:
    return await asyncio.wait_for(queue.get(), timeout)


async def no_event(queue, wait=0.5) -> bool:
    try:
        await asyncio.wait_for(queue.get(), wait)
        return False
    except TimeoutError:
        return True


async def test_join_state_and_roster_events_reach_only_their_session(app, teacher):
    a, roster_a = await new_lesson(teacher, 2)
    b, _ = await new_lesson(teacher, 1)
    qa, qb = app.state.hub.subscribe(a), app.state.hub.subscribe(b)
    try:
        await join(app, roster_a[0]["code"], "Aroha")
        assert (await next_event(qa))["k"] == "join"
        await post(teacher, f"/api/sessions/{a}/state", {"state": "paused"})
        assert (await next_event(qa)) == {"k": "state"}
        await post(teacher, f"/api/sessions/{a}/codes", {"count": 1})
        assert (await next_event(qa)) == {"k": "roster"}
        await post(teacher, f"/api/students/{roster_a[1]['id']}/remove")
        assert (await next_event(qa))["k"] == "roster"
        assert await no_event(qa) and await no_event(qb)
    finally:
        app.state.hub.unsubscribe(a, qa)
        app.state.hub.unsubscribe(b, qb)


async def test_events_carry_ids_only(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    queue = app.state.hub.subscribe(session_id)
    try:
        await join(app, roster[0]["code"], "Aroha")
        event = await next_event(queue)
        text = json.dumps(event)
        assert "Aroha" not in text and roster[0]["code"] not in text
        assert event == {"k": "join", "student": roster[0]["id"]}
    finally:
        app.state.hub.unsubscribe(session_id, queue)


async def test_a_rolled_back_write_sends_nothing(app, dsn):
    queue = app.state.hub.subscribe(999_999)
    try:
        async with await psycopg.AsyncConnection.connect(dsn) as conn:
            await live.publish(conn, 999_999, "state")
            await conn.rollback()
            assert await no_event(queue)
            await live.publish(conn, 999_999, "state")
            await conn.commit()
        assert await next_event(queue) == {"k": "state"}
    finally:
        app.state.hub.unsubscribe(999_999, queue)


async def test_a_second_app_process_sees_every_event(app, teacher, dsn):
    other = create_app(dsn)
    async with other.router.lifespan_context(other):
        session_id, roster = await new_lesson(teacher, 1)
        queue = other.state.hub.subscribe(session_id)
        try:
            # The write happens in the first app; the second app's hub delivers it.
            await join(app, roster[0]["code"], "Aroha")
            assert (await next_event(queue))["k"] == "join"
        finally:
            other.state.hub.unsubscribe(session_id, queue)


async def test_hub_resyncs_after_losing_its_connection_and_keeps_going(dsn):
    hub = live.Hub(dsn, retry_seconds=0.1)
    await hub.start()
    queue = hub.subscribe(42)
    try:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute("SELECT pg_terminate_backend(%s)", (hub.backend_pid,))
            assert await next_event(queue) == live.RESYNC
            await asyncio.wait_for(hub.connected.wait(), 10)
            admin.execute("SELECT pg_notify(%s, %s)", (live.CHANNEL, json.dumps({"s": 42, "k": "state"})))
        # The reconnect may add a second resync before the event.
        event = await next_event(queue)
        if event == live.RESYNC:
            event = await next_event(queue)
        assert event == {"k": "state"}
    finally:
        await hub.stop()


def test_a_client_that_falls_behind_gets_one_resync():
    hub = live.Hub("unused")
    queue = hub.subscribe(1)
    for _ in range(live.QUEUE_SIZE + 5):
        hub.deliver(1, {"k": "state"})
    assert queue.qsize() <= live.QUEUE_SIZE
    items = [queue.get_nowait() for _ in range(queue.qsize())]
    assert live.RESYNC in items


# The SSE endpoint, driven directly over ASGI so the stream can stay open.


async def open_stream(app, path: str, cookie: str):
    """Start a GET on the app; return (chunks queue, disconnect, task)."""
    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        await chunks.put(message)

    task = asyncio.create_task(app(scope, receive, send))
    return chunks, disconnect, task


async def test_sse_endpoint_streams_hello_then_events(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        start = await asyncio.wait_for(chunks.get(), 5)
        assert start["status"] == 200
        assert dict(start["headers"])[b"content-type"].startswith(b"text/event-stream")
        hello = await asyncio.wait_for(chunks.get(), 5)
        assert hello["body"].startswith(b"event: hello")
        await join(app, roster[0]["code"], "Aroha")
        body = (await asyncio.wait_for(chunks.get(), 5))["body"].decode()
        assert body.startswith("event: join\n") and f'"student": {roster[0]["id"]}' in body
    finally:
        disconnect.set()
        await asyncio.wait_for(task, 20)
    assert session_id not in app.state.hub.subscribers


async def test_sse_endpoint_refuses_others(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 1)
    async with new_client(app) as anon:
        assert (await anon.get(f"/api/sessions/{session_id}/events")).status_code == 401
    async with new_client(app) as other:
        await sign_in(other, await make_staff(dsn, "teacher"))
        assert (await other.get(f"/api/sessions/{session_id}/events")).status_code == 404
    student, _ = await join(app, roster[0]["code"], "Aroha")
    assert (await student.get(f"/api/sessions/{session_id}/events")).status_code == 401


async def test_listener_reconnect_logs_the_exception_class_only(caplog, monkeypatch):
    """A database error on the LISTEN connection must not log the server
    DETAIL or the query. The hub still tells clients to resync and tries again."""
    secret = "DETAIL: live-listener-secret-9f3a private chat text"
    query = "LISTEN classroom_live_query_marker"
    calls = 0

    async def connect(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise psycopg.errors.OperationalError(f"{secret}\nQUERY: {query}")
        raise asyncio.CancelledError

    monkeypatch.setattr(live.psycopg.AsyncConnection, "connect", connect)
    hub = live.Hub("postgresql://unused", retry_seconds=0)
    queue = hub.subscribe(7)
    caplog.set_level(logging.WARNING, logger="app.live")
    with pytest.raises(asyncio.CancelledError):
        await hub.run()

    assert calls == 2
    assert queue.get_nowait() == live.RESYNC
    assert "live connection lost; reconnecting" in caplog.text
    assert "OperationalError" in caplog.text
    assert secret not in caplog.text
    assert query not in caplog.text
    assert "Traceback" not in caplog.text
    assert all(record.exc_info is None for record in caplog.records)


async def test_staff_logout_ends_an_open_sse_stream(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05, raising=False)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(chunks.get(), 5)
        assert (await teacher.post("/api/logout", headers={"X-CSRF-Token": teacher.cookies["csrf_token"]})).status_code == 204
        async def keep_events_queued():
            while not task.done():
                app.state.hub.deliver(session_id, {"k": "state"})
                await asyncio.sleep(0)
        producer = asyncio.create_task(keep_events_queued())
        try:
            await asyncio.wait_for(asyncio.shield(task), 1)
        finally:
            producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)
    finally:
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 20)
    assert session_id not in app.state.hub.subscribers


async def test_a_stalled_staff_session_check_ends_the_stream(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(live, "SESSION_CHECK_TIMEOUT_SECONDS", 0.05)
    check_cancelled = asyncio.Event()

    async def stalled_check(pool, token):
        try:
            await asyncio.Event().wait()
        finally:
            check_cancelled.set()

    monkeypatch.setattr(live, "find_session", stalled_check)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(asyncio.shield(task), 1)
    finally:
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 20)
    assert check_cancelled.is_set()
    assert session_id not in app.state.hub.subscribers


async def test_a_stalled_database_query_releases_the_pool_after_stream_ends(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05)
    monkeypatch.setattr(live, "SESSION_CHECK_TIMEOUT_SECONDS", 0.05)
    cleanup_done = asyncio.Event()

    async def stalled_query(pool, token):
        try:
            async with pool.connection() as conn:
                await conn.execute("SELECT pg_sleep(30)")
        finally:
            cleanup_done.set()

    monkeypatch.setattr(live, "find_session", stalled_query)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(asyncio.shield(task), 1)
        await asyncio.wait_for(cleanup_done.wait(), 10)
        async with app.state.pool.connection() as conn:
            assert (await (await conn.execute("SELECT 1")).fetchone()) == (1,)
    finally:
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 20)
    assert session_id not in app.state.hub.subscribers


async def test_staff_session_checks_keep_a_fixed_start_cadence(app, teacher, monkeypatch):
    token = teacher.cookies["session"]
    staff = await auth.find_session(app.state.pool, token)
    starts = []

    async def slow_valid_check(pool, checked_token):
        assert checked_token == token
        starts.append(asyncio.get_running_loop().time())
        await asyncio.sleep(0.025)
        return staff

    monkeypatch.setattr(live, "find_session", slow_valid_check)
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.35)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.06)
    monkeypatch.setattr(live, "SESSION_CHECK_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(live, "PING_SECONDS", 1.0)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(task, 1)
    finally:
        disconnect.set()
    assert len(starts) >= 3
    assert max(later - earlier for earlier, later in zip(starts, starts[1:])) < 0.08
    assert session_id not in app.state.hub.subscribers


async def test_an_event_ready_after_the_stream_deadline_is_not_sent(app, teacher, monkeypatch):
    original_wait_for = asyncio.wait_for

    async def late_event(awaitable, timeout):
        awaitable.close()
        await asyncio.sleep(0.06)
        return {"k": "state"}

    monkeypatch.setattr(live, "STREAM_SECONDS", 0.05)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)
    monkeypatch.setattr(live, "PING_SECONDS", 1.0)
    monkeypatch.setattr(live.asyncio, "wait_for", late_event)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await original_wait_for(chunks.get(), 5)
        await original_wait_for(chunks.get(), 5)
        await original_wait_for(task, 1)
    finally:
        disconnect.set()
    bodies = []
    while not chunks.empty():
        message = chunks.get_nowait()
        bodies.append(message.get("body", b""))
    assert not any(body.startswith(b"event: state") for body in bodies)
    assert session_id not in app.state.hub.subscribers


@pytest.mark.parametrize("failure", ["replacement", "database"])
async def test_an_invalid_staff_session_check_ends_the_stream(app, teacher, monkeypatch, caplog, failure):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05)

    async def invalid_check(pool, token):
        if failure == "database":
            raise psycopg.OperationalError("DETAIL: confidential sentinel")
        return {"kind": "staff", "id": -1}

    monkeypatch.setattr(live, "find_session", invalid_check)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(asyncio.shield(task), 1)
    finally:
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 20)
    assert session_id not in app.state.hub.subscribers
    assert "confidential sentinel" not in caplog.text


async def test_staff_session_expiry_ends_an_open_sse_stream(app, teacher, dsn, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05)
    token = teacher.cookies["session"]
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"
    session_id, _ = await new_lesson(teacher, 1)
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(chunks.get(), 5)
        await asyncio.wait_for(chunks.get(), 5)
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "UPDATE auth_sessions SET expires_at = now() - interval '1 minute' WHERE token_hash = %s",
                (auth.token_hash(token),),
            )
        await asyncio.wait_for(asyncio.shield(task), 1)
    finally:
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 20)
    assert session_id not in app.state.hub.subscribers


async def test_a_non_object_payload_is_ignored_without_a_reconnect(dsn):
    hub = live.Hub(dsn, retry_seconds=0.1)
    await hub.start()
    queue = hub.subscribe(43)
    try:
        with psycopg.connect(dsn, autocommit=True) as admin:
            admin.execute("SELECT pg_notify(%s, %s)", (live.CHANNEL, "[1, 2]"))
            admin.execute("SELECT pg_notify(%s, %s)", (live.CHANNEL, json.dumps({"s": 43, "k": "state"})))
        assert await next_event(queue) == {"k": "state"}  # no resync in between
    finally:
        await hub.stop()


async def test_a_stream_ends_after_its_lifetime(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.5)
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={teacher.cookies['session']}; csrf_token={teacher.cookies['csrf_token']}"
    chunks, disconnect, task = await open_stream(app, f"/api/sessions/{session_id}/events", cookie)
    try:
        await asyncio.wait_for(task, 10)
    finally:
        disconnect.set()
    assert session_id not in app.state.hub.subscribers


async def test_stalled_send_terminates_promptly_after_staff_session_revocation(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05)
    token = teacher.cookies["session"]
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    send_blocked = asyncio.Event()
    release_send = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": f"/api/sessions/{session_id}/events",
             "raw_path": f"/api/sessions/{session_id}/events".encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def blocking_send(message):
        await chunks.put(message)
        if message.get("type") == "http.response.body" and b"event: state" in message.get("body", b""):
            send_blocked.set()
            await release_send.wait()

    task = asyncio.create_task(app(scope, receive, blocking_send))
    try:
        start = await asyncio.wait_for(chunks.get(), 5)
        assert start["type"] == "http.response.start"
        hello = await asyncio.wait_for(chunks.get(), 5)
        assert b"event: hello" in hello["body"]

        app.state.hub.deliver(session_id, {"k": "state"})
        await asyncio.wait_for(send_blocked.wait(), 5)
        assert not task.done()
        assert session_id in app.state.hub.subscribers

        assert (await teacher.post("/api/logout", headers={"X-CSRF-Token": teacher.cookies["csrf_token"]})).status_code == 204
        await asyncio.wait_for(task, 1.0)
        assert session_id not in app.state.hub.subscribers
    finally:
        release_send.set()
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 5)


async def test_stalled_send_terminates_promptly_after_stream_lifetime(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.08)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)
    token = teacher.cookies["session"]
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    send_blocked = asyncio.Event()
    release_send = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": f"/api/sessions/{session_id}/events",
             "raw_path": f"/api/sessions/{session_id}/events".encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def blocking_send(message):
        await chunks.put(message)
        if message.get("type") == "http.response.body" and b"event: state" in message.get("body", b""):
            send_blocked.set()
            await release_send.wait()

    task = asyncio.create_task(app(scope, receive, blocking_send))
    try:
        start = await asyncio.wait_for(chunks.get(), 5)
        assert start["type"] == "http.response.start"
        hello = await asyncio.wait_for(chunks.get(), 5)
        assert b"event: hello" in hello["body"]

        app.state.hub.deliver(session_id, {"k": "state"})
        await asyncio.wait_for(send_blocked.wait(), 5)
        assert not task.done()
        assert session_id in app.state.hub.subscribers

        await asyncio.wait_for(task, 1.0)
        assert session_id not in app.state.hub.subscribers
    finally:
        release_send.set()
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 5)


async def test_stream_lifetime_completes_the_asgi_response_normally(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.08)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)
    token = teacher.cookies["session"]
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": f"/api/sessions/{session_id}/events",
             "raw_path": f"/api/sessions/{session_id}/events".encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        await chunks.put(message)

    task = asyncio.create_task(app(scope, receive, send))
    try:
        start = await asyncio.wait_for(chunks.get(), 5)
        assert start["type"] == "http.response.start"
        hello = await asyncio.wait_for(chunks.get(), 5)
        assert b"event: hello" in hello["body"]

        await asyncio.wait_for(task, 1.0)
        terminal = await asyncio.wait_for(chunks.get(), 1.0)
        assert terminal["type"] == "http.response.body"
        assert terminal["body"] == b""
        assert terminal["more_body"] is False
        assert session_id not in app.state.hub.subscribers
    finally:
        disconnect.set()


async def test_disconnect_during_blocked_send_unsubscribes_deterministically(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)
    token = teacher.cookies["session"]
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    send_blocked = asyncio.Event()
    release_send = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": f"/api/sessions/{session_id}/events",
             "raw_path": f"/api/sessions/{session_id}/events".encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def blocking_send(message):
        await chunks.put(message)
        if message.get("type") == "http.response.body" and b"event: state" in message.get("body", b""):
            send_blocked.set()
            await release_send.wait()

    task = asyncio.create_task(app(scope, receive, blocking_send))
    try:
        start = await asyncio.wait_for(chunks.get(), 5)
        assert start["type"] == "http.response.start"
        hello = await asyncio.wait_for(chunks.get(), 5)
        assert b"event: hello" in hello["body"]

        app.state.hub.deliver(session_id, {"k": "state"})
        await asyncio.wait_for(send_blocked.wait(), 5)
        assert not task.done()
        assert session_id in app.state.hub.subscribers

        disconnect.set()
        await asyncio.wait_for(task, 1.0)
        assert session_id not in app.state.hub.subscribers
    finally:
        release_send.set()
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 5)


async def test_stalled_response_start_terminates_promptly_after_staff_session_revocation(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.05)
    token = teacher.cookies["session"]
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    start_blocked = asyncio.Event()
    release_start = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": f"/api/sessions/{session_id}/events",
             "raw_path": f"/api/sessions/{session_id}/events".encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def blocking_send(message):
        await chunks.put(message)
        if message.get("type") == "http.response.start":
            start_blocked.set()
            await release_start.wait()

    task = asyncio.create_task(app(scope, receive, blocking_send))
    try:
        await asyncio.wait_for(start_blocked.wait(), 5)
        assert not task.done()

        assert (await teacher.post("/api/logout", headers={"X-CSRF-Token": teacher.cookies["csrf_token"]})).status_code == 204
        await asyncio.wait_for(task, 1.0)
        assert session_id not in app.state.hub.subscribers
        while not chunks.empty():
            msg = chunks.get_nowait()
            assert msg.get("type") != "http.response.body"
    finally:
        release_start.set()
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 5)


async def test_stalled_response_start_terminates_promptly_after_stream_lifetime(app, teacher, monkeypatch):
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.08)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)
    token = teacher.cookies["session"]
    session_id, _ = await new_lesson(teacher, 1)
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    start_blocked = asyncio.Event()
    release_start = asyncio.Event()
    chunks: asyncio.Queue = asyncio.Queue()
    disconnect = asyncio.Event()
    scope = {"type": "http", "method": "GET", "path": f"/api/sessions/{session_id}/events",
             "raw_path": f"/api/sessions/{session_id}/events".encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode())], "scheme": "https",
             "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1", "root_path": ""}
    sent_request = False

    async def receive():
        nonlocal sent_request
        if not sent_request:
            sent_request = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def blocking_send(message):
        await chunks.put(message)
        if message.get("type") == "http.response.start":
            start_blocked.set()
            await release_start.wait()

    task = asyncio.create_task(app(scope, receive, blocking_send))
    try:
        await asyncio.wait_for(start_blocked.wait(), 5)
        assert not task.done()

        await asyncio.wait_for(task, 1.0)
        assert session_id not in app.state.hub.subscribers
        while not chunks.empty():
            msg = chunks.get_nowait()
            assert msg.get("type") != "http.response.body"
    finally:
        release_start.set()
        disconnect.set()
        if not task.done():
            await asyncio.wait_for(task, 5)


async def test_uvicorn_request_lifecycle_terminates_promptly_when_start_is_blocked(app, teacher, monkeypatch):
    from types import SimpleNamespace
    import h11
    from uvicorn.protocols.http.flow_control import FlowControl
    from uvicorn.protocols.http.h11_impl import RequestResponseCycle

    monkeypatch.setattr(live, "STREAM_SECONDS", 0.05)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 0.02)

    async def no_session(*args):
        return None

    monkeypatch.setattr(live, "find_session", no_session)

    session_id, _ = await new_lesson(teacher, 1)
    token = teacher.cookies["session"]
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    closed = []
    transport = SimpleNamespace(close=lambda: closed.append(True), write=lambda data: None)
    flow = FlowControl(transport)
    flow.pause_writing()

    conn = h11.Connection(h11.SERVER)
    conn.receive_data(f"GET /api/sessions/{session_id}/events HTTP/1.1\r\nHost: test\r\nCookie: {cookie}\r\n\r\n".encode())
    conn.next_event()

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/sessions/{session_id}/events",
        "raw_path": f"/api/sessions/{session_id}/events".encode(),
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"cookie", cookie.encode())],
        "http_version": "1.1",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
    }
    logger = logging.getLogger("test_uvicorn_lifecycle")
    cycle = RequestResponseCycle(scope, conn, transport, flow, logger, logger, False, [], asyncio.Event(), lambda: None)

    task = asyncio.create_task(cycle.run_asgi(app))
    await asyncio.wait_for(task, 1.0)
    assert bool(closed) is True


async def test_backpressure_released_after_expiry_does_not_emit_late_frame(app, teacher, monkeypatch):
    from types import SimpleNamespace
    import h11
    from uvicorn.protocols.http.flow_control import FlowControl
    from uvicorn.protocols.http.h11_impl import RequestResponseCycle

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.04)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)

    session_id, _ = await new_lesson(teacher, 1)
    token = teacher.cookies["session"]
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    writes = []
    closed = []

    def write(data):
        writes.append(data)
        if b"event: hello" in data:
            flow.pause_writing()
            app.state.hub.deliver(session_id, {"k": "state"})
            loop.call_later(0.0401, flow.resume_writing)

    transport = SimpleNamespace(close=lambda: closed.append(True), write=write, is_closing=lambda: False)
    flow = FlowControl(transport)

    conn = h11.Connection(h11.SERVER)
    conn.receive_data(f"GET /api/sessions/{session_id}/events HTTP/1.1\r\nHost: test\r\nCookie: {cookie}\r\n\r\n".encode())
    conn.next_event()

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/sessions/{session_id}/events",
        "raw_path": f"/api/sessions/{session_id}/events".encode(),
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"cookie", cookie.encode())],
        "http_version": "1.1",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
    }
    logger = logging.getLogger("test_backpressure_race")
    cycle = RequestResponseCycle(scope, conn, transport, flow, logger, logger, False, [], asyncio.Event(), lambda: None)

    task = asyncio.create_task(cycle.run_asgi(app))
    await asyncio.wait_for(task, 1.0)
    assert not any(b"event: state" in b for b in writes)
    assert session_id not in app.state.hub.subscribers
    assert cycle.response_complete is True


async def test_backpressure_resumed_across_deadline_in_callback_does_not_emit_late_frame(app, teacher, monkeypatch):
    import time
    from types import SimpleNamespace
    import h11
    from uvicorn.protocols.http.flow_control import FlowControl
    from uvicorn.protocols.http.h11_impl import RequestResponseCycle

    loop = asyncio.get_running_loop()
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.04)
    monkeypatch.setattr(live, "SESSION_CHECK_SECONDS", 1.0)

    session_id, _ = await new_lesson(teacher, 1)
    token = teacher.cookies["session"]
    cookie = f"session={token}; csrf_token={teacher.cookies['csrf_token']}"

    writes = []
    closed = []

    def resume_after_busy_callback():
        time.sleep(0.02)
        flow.resume_writing()

    def write(data):
        writes.append(data)
        if b"event: hello" in data:
            flow.pause_writing()
            app.state.hub.deliver(session_id, {"k": "state"})
            loop.call_later(0.03, resume_after_busy_callback)

    transport = SimpleNamespace(close=lambda: closed.append(True), write=write, is_closing=lambda: False)
    flow = FlowControl(transport)

    conn = h11.Connection(h11.SERVER)
    conn.receive_data(f"GET /api/sessions/{session_id}/events HTTP/1.1\r\nHost: test\r\nCookie: {cookie}\r\n\r\n".encode())
    conn.next_event()

    scope = {
        "type": "http",
        "method": "GET",
        "path": f"/api/sessions/{session_id}/events",
        "raw_path": f"/api/sessions/{session_id}/events".encode(),
        "query_string": b"",
        "headers": [(b"host", b"test"), (b"cookie", cookie.encode())],
        "http_version": "1.1",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
    }
    logger = logging.getLogger("test_backpressure_callback_race")
    cycle = RequestResponseCycle(scope, conn, transport, flow, logger, logger, False, [], asyncio.Event(), lambda: None)

    task = asyncio.create_task(cycle.run_asgi(app))
    await asyncio.wait_for(task, 1.0)
    assert not any(b"event: state" in b for b in writes)
    assert session_id not in app.state.hub.subscribers
    assert cycle.response_complete is True
