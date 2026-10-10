"""Student chat and the turn pipeline (step 4)."""

import asyncio
import json
import logging
from datetime import UTC, datetime

import httpx
import psycopg
import pytest

from app import chat, guard, workers
from app.main import create_app
from tests.helpers import make_staff, new_client, post, sign_in

MODEL = "gemma-4-e2b-it"
# Worker ids are UUIDs; the hosts are w1 and w2.
ID = {"w1": "00000000-0000-4000-8000-000000000001", "w2": "00000000-0000-4000-8000-000000000002"}


# A scripted worker: each host plays a list of steps. A string is a content
# delta; "DONE" ends the stream; "STOP" sends finish_reason stop; "CUT" ends
# the stream with no end marker; a number sleeps that many seconds; an
# exception class is raised when reached.


class Workers(httpx.AsyncBaseTransport):
    def __init__(self):
        self.scripts: dict[str, list] = {}
        self.status: dict[str, int] = {}
        self.refuse: set[str] = set()
        self.header_delay: dict[str, float] = {}
        self.requests: list[httpx.Request] = []
        self.streams: dict[str, Script] = {}
        self.on_request = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.refuse:
            raise httpx.ConnectError("refused", request=request)
        self.requests.append(request)
        if self.on_request:
            await self.on_request(request)
        if delay := self.header_delay.get(host):
            await asyncio.sleep(delay)
        script = Script(list(self.scripts.get(host, ["Hello", " there", "STOP", "DONE"])))
        self.streams[host] = script
        return httpx.Response(self.status.get(host, 200), stream=script,
                              headers={"content-type": "text/event-stream"})


class Script(httpx.AsyncByteStream):
    def __init__(self, steps):
        self.steps = steps
        self.closed = False

    async def aclose(self):
        self.closed = True

    async def __aiter__(self):
        for step in self.steps:
            if isinstance(step, (int, float)):
                await asyncio.sleep(step)
            elif isinstance(step, type) and issubclass(step, Exception):
                raise step("scripted failure")
            elif step == "DONE":
                yield b"data: [DONE]\n\n"
                return
            elif step == "CUT":
                return
            elif step == "STOP":
                chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            elif step == "MALFORMED":
                yield b'data: {"choices":[null]}\n\n'
            elif step == "LENGTH":
                chunk = {"choices": [{"index": 0, "delta": {}, "finish_reason": "length"}]}
                yield f"data: {json.dumps(chunk)}\n\n".encode()
            else:
                chunk = {"choices": [{"index": 0, "delta": {"content": step}, "finish_reason": None}]}
                yield f"data: {json.dumps(chunk)}\n\n".encode()


@pytest.fixture
async def setup(dsn):
    """A fresh app with scripted workers w1 and w2 offering MODEL, model on."""
    fake = Workers()
    app = create_app(dsn, worker_transport=fake, guard_disabled=True)
    async with app.router.lifespan_context(app):
        with psycopg.connect(dsn) as conn:
            generation = conn.execute("SELECT value->>'generation' FROM settings WHERE key = 'join_token'").fetchone()[0]
            conn.execute("DELETE FROM workers")
            for name in ("w1", "w2"):
                conn.execute(
                    "INSERT INTO workers (id, address, api_key, models, capacity, last_heartbeat, token_generation)"
                    " VALUES (%s, %s, %s, %s, 4, now(), %s)",
                    (ID[name], f"http://{name}:8081", f"key-{name}", json.dumps([MODEL]), int(generation)))
            conn.execute("INSERT INTO models (name, enabled) VALUES (%s, true)"
                         " ON CONFLICT (name) DO UPDATE SET enabled = true", (MODEL,))
        app.state.limiter.reset()
        app.state.join_limiter.reset()
        yield app, fake


async def lesson(app, dsn, limit=None, instructions="Guide, don't answer."):
    teacher = new_client(app)
    await sign_in(teacher, await make_staff(dsn, "teacher"))
    cls = (await post(teacher, "/api/classes", {"name": "Year 9", "instructions": instructions,
                                                 "message_limit": limit})).json()["id"]
    session_id = (await post(teacher, f"/api/classes/{cls}/sessions", {"count": 3})).json()["id"]
    roster = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"]
    student = new_client(app)
    r = await post(student, "/api/join", {"code": roster[0]["code"], "name": "Aroha"})
    assert r.status_code == 200
    return teacher, student, session_id, roster


async def conversation(student) -> int:
    r = await post(student, "/api/student/conversations", {"model": MODEL})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def events(text: str) -> list[tuple[str, dict]]:
    out = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n") if ": " in line)
        if "event" in lines:
            out.append((lines["event"], json.loads(lines["data"])))
    return out


async def send(student, convo: int, text: str):
    return await post(student, f"/api/student/conversations/{convo}/messages", {"text": text})


def rows(dsn, convo: int) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT role, text, status, shown_length, worker_id FROM messages"
                            " WHERE conversation_id = %s ORDER BY id", (convo,)).fetchall()


# Happy path, storage and identity.


async def test_reply_streams_and_is_stored(setup, dsn):
    app, fake = setup
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    r = await send(student, convo, "What is a cell?")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    evs = events(r.text)
    assert [e for e, _ in evs] == ["delta", "delta", "done"]
    assert "".join(d["text"] for e, d in evs if e == "delta") == "Hello there"
    stored = rows(dsn, convo)
    assert stored[0][:3] == ("user", "What is a cell?", "ok")
    assert stored[1][:4] == ("assistant", "Hello there", "ok", len("Hello there"))
    transcript = (await student.get(f"/api/student/conversations/{convo}")).json()["messages"]
    assert [m["text"] for m in transcript] == ["What is a cell?", "Hello there"]


async def test_stored_messages_notify_the_right_session_across_app_processes(setup, dsn):
    app, _ = setup
    teacher, student, session_id, roster = await lesson(app, dsn)
    conversation_id = await conversation(student)
    other = create_app(dsn, guard_disabled=True)
    async with other.router.lifespan_context(other):
        right = other.state.hub.subscribe(session_id)
        wrong_id = session_id + 1_000_000
        wrong = other.state.hub.subscribe(wrong_id)
        try:
            response = await send(student, conversation_id, "hello")
            assert response.status_code == 200
            notices = [await asyncio.wait_for(right.get(), 5) for _ in range(2)]
            assert [notice["k"] for notice in notices] == ["message", "message"]
            assert [notice["student"] for notice in notices] == [roster[0]["id"], roster[0]["id"]]
            assert notices[0]["message"] != notices[1]["message"]
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(right.get(), 0.3)
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(wrong.get(), 0.3)
        finally:
            other.state.hub.unsubscribe(session_id, right)
            other.state.hub.unsubscribe(wrong_id, wrong)
            await teacher.aclose()
            await student.aclose()


@pytest.mark.parametrize("ending", [["LENGTH"], ["DONE"]])
async def test_length_or_done_without_stop_is_a_normal_end(setup, dsn, ending):
    app, fake = setup
    fake.scripts["w1"] = fake.scripts["w2"] = ["Hi", *ending]
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    assert events((await send(student, convo, "hello")).text)[-1][0] == "done"


async def test_the_message_is_committed_before_the_worker_sees_it(setup, dsn):
    app, fake = setup
    seen = []

    async def check(request):
        with psycopg.connect(dsn) as conn:  # a separate connection sees only committed rows
            seen.append(conn.execute(
                "SELECT role, status, worker_id FROM messages WHERE conversation_id ="
                " (SELECT conversation_id FROM messages WHERE text = 'committed?') ORDER BY id"
            ).fetchall())

    fake.on_request = check
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    await send(student, convo, "committed?")
    assert seen == [[("user", "ok", None), ("assistant", "pending", ID["w1"])]]


async def test_worker_request_carries_no_identity(setup, dsn):
    app, fake = setup
    _, student, _, roster = await lesson(app, dsn, instructions="Be a tutor.")
    convo = await conversation(student)
    await send(student, convo, "first")
    await send(student, convo, "second")
    request = fake.requests[-1]
    assert request.url.path == "/v1/chat/completions" and request.url.query == b""
    body = json.loads(request.content)
    assert set(body) == {"model", "messages", "stream"}
    assert body["messages"] == [
        {"role": "system", "content": "Be a tutor."},
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "Hello there"},
        {"role": "user", "content": "second"},
    ]
    everything = request.content.decode() + str(request.url) + json.dumps(dict(request.headers))
    for secret in ("Aroha", roster[0]["code"], "Year 9"):
        assert secret not in everything
    assert request.headers["authorization"].startswith("Bearer key-w")


async def test_chat_text_never_reaches_the_logs(setup, dsn, caplog):
    app, fake = setup
    fake.scripts["w1"] = fake.scripts["w2"] = ["secret reply", "CUT"]
    caplog.set_level(logging.DEBUG)
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    await send(student, convo, "a private question")
    assert "private question" not in caplog.text and "secret reply" not in caplog.text


# Refusals before the stream.


async def test_a_model_turned_off_is_refused_at_once(setup, dsn):
    app, fake = setup
    teacher, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE models SET enabled = false WHERE name = %s", (MODEL,))
    try:
        r = await send(student, convo, "hi")
        assert (r.status_code, r.json()) == (409, {"error": "model_off"})
        assert (await post(student, "/api/student/conversations", {"model": MODEL})).status_code == 409
        assert (await student.get("/api/student/models")).json() == {"models": []}
        assert fake.requests == [] and rows(dsn, convo) == []
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("UPDATE models SET enabled = true WHERE name = %s", (MODEL,))


async def test_the_message_limit(setup, dsn):
    app, fake = setup
    _, student, _, _ = await lesson(app, dsn, limit=2)
    convo = await conversation(student)
    assert (await send(student, convo, "one")).status_code == 200
    assert (await send(student, convo, "two")).status_code == 200
    r = await send(student, convo, "three")
    assert (r.status_code, r.json()) == (409, {"error": "limit_reached"})
    assert len(fake.requests) == 2 and len(rows(dsn, convo)) == 4
    assert (await student.get("/api/student/me")).json()["student"]["messages_used"] == 2


async def test_the_last_slot_goes_to_one_of_two_tabs(setup, dsn):
    app, fake = setup
    _, student, _, _ = await lesson(app, dsn, limit=1)
    a, b = await conversation(student), await conversation(student)
    results = await asyncio.gather(send(student, a, "x"), send(student, b, "y"), send(student, a, "z"))
    assert sorted(r.status_code for r in results) == [200, 409, 409]


async def test_paused_refuses_sending(setup, dsn):
    app, fake = setup
    teacher, student, session_id, _ = await lesson(app, dsn)
    convo = await conversation(student)
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "paused"})
    r = await send(student, convo, "hi")
    assert (r.status_code, r.json()) == (409, {"error": "paused"})
    assert fake.requests == []


async def test_guard_unavailable_fails_closed(dsn, setup):
    app, fake = setup
    app.state.guard_disabled = False
    try:
        _, student, _, _ = await lesson(app, dsn)
        convo = await conversation(student)
        r = await send(student, convo, "hi")
        assert (r.status_code, r.json()) == (503, {"error": "chat_unavailable"})
        assert fake.requests == []
        assert rows(dsn, convo)[0][2] == "error"
    finally:
        app.state.guard_disabled = True


async def test_bad_text_and_access(setup, dsn):
    app, fake = setup
    teacher, student, _, roster = await lesson(app, dsn)
    convo = await conversation(student)
    assert (await send(student, convo, "   ")).status_code == 400
    assert (await send(student, convo, "x" * 4001)).status_code == 400
    async with new_client(app) as anon:
        assert (await anon.get("/api/student/conversations")).status_code == 401
        assert (await send(anon, convo, "hi")).status_code == 401
    assert (await send(teacher, convo, "hi")).status_code == 401
    other = new_client(app)
    await post(other, "/api/join", {"code": roster[1]["code"], "name": "Ben"})
    assert (await other.get(f"/api/student/conversations/{convo}")).status_code == 404
    assert (await send(other, convo, "hi")).status_code == 404
    session = student.cookies["session"]
    planted = await student.post(f"/api/student/conversations/{convo}/messages", json={"text": "hi"}, headers={
        "X-CSRF-Token": "planted", "Cookie": f"session={session}; csrf_token=planted"})
    assert planted.status_code == 403


# Workers failing.


async def test_failover_when_a_worker_refuses_connections(setup, dsn):
    app, fake = setup
    fake.refuse.add("w1")
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    assert events((await send(student, convo, "hi")).text)[-1][0] == "done"
    assert rows(dsn, convo)[1][4] == ID["w2"]
    with psycopg.connect(dsn) as conn:
        down = conn.execute("SELECT now() - last_heartbeat >= interval '45 seconds'"
                            " FROM workers WHERE id = %s", (ID["w1"],)).fetchone()[0]
    assert down
    assert app.state.router.in_flight[ID["w1"]] == app.state.router.in_flight[ID["w2"]] == 0


@pytest.mark.parametrize("failure", ["status", "cut_before_text", "slow_first_chunk", "slow_headers", "malformed"])
async def test_failures_before_any_text_retry_without_marking_down(setup, dsn, monkeypatch, failure):
    app, fake = setup
    if failure == "status":
        fake.status["w1"] = 503
    elif failure == "cut_before_text":
        fake.scripts["w1"] = ["CUT"]
    else:
        monkeypatch.setenv("FIRST_CHUNK_TIMEOUT", "0.3")
        if failure == "slow_first_chunk":
            fake.scripts["w1"] = [2, "late"]
        elif failure == "slow_headers":
            fake.header_delay["w1"] = 2
        else:
            fake.scripts["w1"] = ["MALFORMED"]
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    assert events((await send(student, convo, "hi")).text)[-1][0] == "done"
    assert rows(dsn, convo)[1][4] == ID["w2"]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT now() - last_heartbeat < interval '45 seconds'"
                            " FROM workers WHERE id = %s", (ID["w1"],)).fetchone()[0]


async def test_a_drop_mid_reply_keeps_the_partial_as_an_error(setup, dsn):
    app, fake = setup
    fake.scripts["w1"] = fake.scripts["w2"] = ["Part", "ial", "CUT"]
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    evs = events((await send(student, convo, "hi")).text)
    assert [e for e, _ in evs] == ["delta", "delta", "error"]
    assert evs[-1][1]["message"] == chat.CUT_OFF
    assert rows(dsn, convo)[1][:4] == ("assistant", "Partial", "error", 7)
    assert len(fake.requests) == 1  # no failover after text was shown


async def test_no_live_worker(setup, dsn):
    app, fake = setup
    fake.refuse.update({"w1", "w2"})
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    evs = events((await send(student, convo, "hi")).text)
    assert evs == [("error", {"message": chat.NO_MODEL})]
    assert rows(dsn, convo)[1][:3] == ("assistant", "", "error")


async def test_a_reply_that_cannot_be_saved_says_so(setup, dsn, monkeypatch):
    app, fake = setup
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)

    real = chat.store_flags

    async def broken(conn, message_id, verdicts):
        if not verdicts:  # the reply's store; the prompt's verdict list is never empty
            raise psycopg.OperationalError("disk full")
        await real(conn, message_id, verdicts)

    monkeypatch.setattr(chat, "store_flags", broken)
    evs = events((await send(student, convo, "hi")).text)
    assert evs[-1] == ("error", {"message": chat.NOT_SAVED})


# The guard seam, driven by a stub guard.


class StubGate(guard.ReplyGate):
    """Shows the first delta, then blocks on the next."""

    async def feed(self, delta):
        if self.verdicts or delta == " there":
            verdict = guard.Verdict("block_flag", ("Violent", "Hate"), "That reply was stopped.")
            self.verdicts.append(verdict)
            raise guard.ReplyBlocked(verdict)
        return delta

    async def finish(self):
        return ""


async def test_a_blocked_prompt_gets_its_message_and_reaches_no_worker(setup, dsn, monkeypatch):
    app, fake = setup

    async def block(app_, ctx):
        return guard.Verdict("block_flag", ("Violent",), "Let's talk about something else.")

    monkeypatch.setattr(guard, "check_prompt", block)
    teacher, student, session_id, roster = await lesson(app, dsn)
    convo = await conversation(student)
    r = await send(student, convo, "bad thing")
    assert r.json() == {"blocked": "Let's talk about something else."}
    assert fake.requests == []
    assert rows(dsn, convo)[0][2] == "blocked"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT category, action FROM flags f JOIN messages m ON m.id = f.message_id"
                            " WHERE m.conversation_id = %s", (convo,)).fetchall() == [("Violent", "block_flag")]
    # A blocked prompt is never sent later as history.
    monkeypatch.setattr(guard, "check_prompt", allow_all)
    await send(student, convo, "fine question")
    assert "bad thing" not in fake.requests[-1].content.decode()


async def allow_all(app, ctx):
    return guard.Verdict()


async def test_a_blocked_reply_keeps_withheld_text_out_of_student_reads(setup, dsn, monkeypatch):
    app, fake = setup
    monkeypatch.setattr(guard, "reply_gate", lambda app_, ctx: StubGate(app_, ctx))
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    evs = events((await send(student, convo, "hi")).text)
    assert evs == [("delta", {"text": "Hello"}), ("blocked", {"message": "That reply was stopped."})]
    assert rows(dsn, convo)[1][:4] == ("assistant", "Hello there", "blocked", 5)
    student_view = (await student.get(f"/api/student/conversations/{convo}")).json()["messages"][1]["text"]
    assert student_view == "Hello\nThat reply was stopped." and "there" not in student_view
    with psycopg.connect(dsn) as conn:
        cats = conn.execute("SELECT category FROM flags f JOIN messages m ON m.id = f.message_id"
                            " WHERE m.conversation_id = %s ORDER BY category", (convo,)).fetchall()
    assert cats == [("Hate",), ("Violent",)]


async def test_guard_unavailable_mid_reply_stops_and_sends_nothing_unchecked(setup, dsn, monkeypatch):
    app, fake = setup

    class Down(guard.ReplyGate):
        async def feed(self, delta):
            if delta == " there":
                raise guard.GuardUnavailable()
            return delta

    monkeypatch.setattr(guard, "reply_gate", lambda app_, ctx: Down(app_, ctx))
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    evs = events((await send(student, convo, "hi")).text)
    assert evs == [("delta", {"text": "Hello"}), ("error", {"message": chat.UNAVAILABLE})]
    assert rows(dsn, convo)[1][:4] == ("assistant", "Hello there", "error", 5)
    assert len(fake.requests) == 1
    assert app.state.router.in_flight[ID["w1"]] == app.state.router.in_flight[ID["w2"]] == 0


async def test_a_pending_prompt_never_appears_in_another_turns_history(setup, dsn, monkeypatch):
    app, fake = setup
    release = asyncio.Event()

    async def slow_for_first(app_, ctx):
        if ctx.prompt == "turn A":
            await release.wait()
        return guard.Verdict()

    monkeypatch.setattr(guard, "check_prompt", slow_for_first)
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    turn_a = asyncio.create_task(send(student, convo, "turn A"))
    await asyncio.sleep(0.3)
    await send(student, convo, "turn B")
    assert "turn A" not in fake.requests[-1].content.decode()
    release.set()
    await turn_a


async def test_a_stale_pending_row_stops_counting(setup, dsn):
    app, fake = setup
    _, student, _, _ = await lesson(app, dsn, limit=1)
    convo = await conversation(student)
    with psycopg.connect(dsn) as conn:
        conn.execute("INSERT INTO messages (conversation_id, role, text, status, created_at)"
                     " VALUES (%s, 'user', 'abandoned', 'pending', now() - interval '10 minutes')", (convo,))
    assert (await student.get("/api/student/me")).json()["student"]["messages_used"] == 0
    assert (await send(student, convo, "real one")).status_code == 200
    assert [r[2] for r in rows(dsn, convo)][0] == "error"


async def test_admission_does_not_sweep_an_active_assistant_reply(setup, dsn):
    app, _ = setup
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    with psycopg.connect(dsn) as conn:
        reply = conn.execute(
            "INSERT INTO messages (conversation_id, role, text, status, shown_length, created_at)"
            " VALUES (%s, 'assistant', '', 'pending', 0, now() - interval '3 minutes') RETURNING id",
            (convo,),
        ).fetchone()[0]
        student_id = conn.execute("SELECT student_id FROM conversations WHERE id = %s", (convo,)).fetchone()[0]
    async with app.state.pool.connection() as conn:
        await chat.sweep_pending(conn, student_id)
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT status FROM messages WHERE id = %s", (reply,)).fetchone() == ("pending",)


async def test_startup_recovers_stale_pending_rows(setup, dsn):
    app, _ = setup
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "INSERT INTO messages (conversation_id, role, text, status, created_at)"
            " VALUES (%s, 'user', 'abandoned', 'pending', now() - interval '3 minutes') RETURNING id",
            (convo,),
        ).fetchone()

    restarted = create_app(dsn, guard_disabled=True)
    async with restarted.router.lifespan_context(restarted):
        with psycopg.connect(dsn) as conn:
            assert conn.execute("SELECT status FROM messages WHERE id = %s", (row[0],)).fetchone() == ("error",)


async def test_a_student_leaving_mid_reply_keeps_the_partial(setup, dsn):
    app, fake = setup
    fake.scripts["w1"] = fake.scripts["w2"] = ["Part", 5, "never"]
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    cookie = f"session={student.cookies['session']}; csrf_token={student.cookies['csrf_token']}"
    body = json.dumps({"text": "hi"}).encode()
    sent: asyncio.Queue = asyncio.Queue()
    gone = asyncio.Event()
    path = f"/api/student/conversations/{convo}/messages"
    scope = {"type": "http", "method": "POST", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode()), (b"content-type", b"application/json"),
                         (b"x-csrf-token", student.cookies["csrf_token"].encode())],
             "scheme": "https", "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1",
             "root_path": ""}
    first = True

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": body, "more_body": False}
        await gone.wait()
        return {"type": "http.disconnect"}

    task = asyncio.create_task(app(scope, receive, sent.put))
    while True:
        message = await asyncio.wait_for(sent.get(), 10)
        if message.get("body", b"").startswith(b"event: delta"):
            break
    gone.set()
    await asyncio.wait_for(task, 20)
    assert rows(dsn, convo)[1][:4] == ("assistant", "Part", "error", 4)
    assert app.state.router.in_flight[ID["w1"]] == app.state.router.in_flight[ID["w2"]] == 0


async def test_disconnect_during_a_backpressured_send_cleans_up(setup, dsn):
    app, fake = setup
    fake.scripts["w1"] = fake.scripts["w2"] = ["Part", 5, "never"]
    _, student, _, _ = await lesson(app, dsn)
    convo = await conversation(student)
    cookie = f"session={student.cookies['session']}; csrf_token={student.cookies['csrf_token']}"
    body = json.dumps({"text": "hi"}).encode()
    delta_started = asyncio.Event()
    never = asyncio.Event()
    first = True
    path = f"/api/student/conversations/{convo}/messages"
    scope = {"type": "http", "method": "POST", "path": path, "raw_path": path.encode(), "query_string": b"",
             "headers": [(b"host", b"test"), (b"cookie", cookie.encode()), (b"content-type", b"application/json"),
                         (b"x-csrf-token", student.cookies["csrf_token"].encode())],
             "scheme": "https", "server": ("test", 443), "client": ("127.0.0.1", 1), "http_version": "1.1",
             "root_path": "", "asgi": {"spec_version": "2.3"}}

    async def receive():
        nonlocal first
        if first:
            first = False
            return {"type": "http.request", "body": body, "more_body": False}
        await delta_started.wait()
        return {"type": "http.disconnect"}

    async def blocked_send(message):
        if message.get("body", b"").startswith(b"event: delta"):
            delta_started.set()
            await never.wait()

    await asyncio.wait_for(app(scope, receive, blocked_send), 20)
    assert rows(dsn, convo)[1][:4] == ("assistant", "Part", "error", 4)
    assert app.state.router.in_flight[ID["w1"]] == app.state.router.in_flight[ID["w2"]] == 0
    assert fake.streams["w1"].closed


async def test_conversations_list_and_models(setup, dsn):
    app, fake = setup
    _, student, _, _ = await lesson(app, dsn)
    assert (await student.get("/api/student/models")).json() == {"models": [MODEL]}
    convo = await conversation(student)
    await send(student, convo, "What is photosynthesis?")
    listed = (await student.get("/api/student/conversations")).json()["conversations"]
    assert listed[0]["id"] == convo and listed[0]["title"] == "What is photosynthesis?"
