"""Live events for the teacher console (step 7, slice A)."""

import asyncio
import json

import psycopg
import pytest

from app import live
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
