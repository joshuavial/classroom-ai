"""Student chat: conversations and the turn pipeline (step 4).

The order is fixed (docs/architecture.md "Turn pipeline"):

1. Check the lesson is open, the model is on and the limit is not reached,
   under a lock on the student row, and store the message as `pending`.
2. Ask the guard (app/guard.py) and store its verdict. Only an approved
   (`ok`) prompt ever goes to a worker; a blocked one gets its message.
3. Lease a live worker, send the class instructions and this conversation's
   approved text, and stream the reply through the guard's reply gate.
4. Store the whole reply, how much of it the student was shown, and the
   verdicts, then tell the teacher's live view.

Before the stream starts, problems answer with an HTTP status; once it has
started they arrive as `error` or `blocked` events. Chat text never goes to
logs: log lines carry ids only.
"""

import asyncio
import json
import logging
import os

import anyio
import httpx
from psycopg.types.json import Jsonb
from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse
from starlette.routing import Route

from app import guard, workers
from app.auth import HTTPError, json_body, require_student
from app.classes import iso, path_id
from app.live import publish

log = logging.getLogger("app.chat")

MAX_TEXT = 4000
MAX_TRIES = 3
PENDING_STALE_MINUTES = 2
CONNECT_TIMEOUT = 5.0
BETWEEN_CHUNKS_TIMEOUT = 120.0
NO_MODEL = "This model isn't available right now. Try again in a minute or pick another model."
CUT_OFF = "The reply was cut off. Try sending your message again."
UNAVAILABLE = "Chat is unavailable right now. Tell your teacher."
NOT_SAVED = "The reply could not be saved. Try sending your message again."


def first_chunk_timeout() -> float:
    return float(os.environ.get("FIRST_CHUNK_TIMEOUT", "30"))


# Reads. Assistant text is cut to what the student was shown, everywhere a
# student can read it.


def shown(role: str, text: str, status: str, shown_length: int | None, verdict: dict | None) -> str:
    if role == "assistant":
        text = text[: shown_length or 0]
    if status == "blocked" and verdict and verdict.get("message"):
        text = f"{text}\n{verdict['message']}" if text else verdict["message"]
    return text


async def own_conversation(conn, student: dict, conversation_id: int) -> str:
    cur = await conn.execute(
        "SELECT model FROM conversations WHERE id = %s AND student_id = %s", (conversation_id, student["id"]))
    row = await cur.fetchone()
    if row is None:
        raise HTTPError(404, "not_found")
    return row[0]


async def models(request: Request) -> JSONResponse:
    await require_student(request)
    return JSONResponse({"models": await workers.offered_models(request.app.state.pool, request.app.state.clock())})


async def list_conversations(request: Request) -> JSONResponse:
    student = await require_student(request)
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT c.id, c.model, c.started_at,"
            " (SELECT text FROM messages m WHERE m.conversation_id = c.id AND m.role = 'user'"
            "  AND m.status = 'ok' ORDER BY m.id LIMIT 1)"
            " FROM conversations c WHERE c.student_id = %s ORDER BY c.id DESC",
            (student["id"],),
        )
        rows = await cur.fetchall()
    return JSONResponse({"conversations": [
        {"id": r[0], "model": r[1], "started_at": iso(r[2]), "title": (r[3] or "")[:80]} for r in rows
    ]})


async def new_conversation(request: Request) -> JSONResponse:
    student = await require_student(request, sending=True)
    model = (await json_body(request)).get("model")
    if not isinstance(model, str) or model not in await workers.offered_models(
            request.app.state.pool, request.app.state.clock()):
        raise HTTPError(409, "model_off")
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "INSERT INTO conversations (student_id, model) VALUES (%s, %s) RETURNING id, started_at",
            (student["id"], model))
        row = await cur.fetchone()
    return JSONResponse({"id": row[0], "model": model, "started_at": iso(row[1]), "title": ""}, status_code=201)


async def get_conversation(request: Request) -> JSONResponse:
    student = await require_student(request)
    conversation_id = path_id(request)
    async with request.app.state.pool.connection() as conn:
        model = await own_conversation(conn, student, conversation_id)
        cur = await conn.execute(
            "SELECT id, role, text, status, shown_length, verdict, created_at FROM messages"
            " WHERE conversation_id = %s AND status <> 'pending' ORDER BY id",
            (conversation_id,),
        )
        rows = await cur.fetchall()
    return JSONResponse({"id": conversation_id, "model": model, "messages": [
        {"id": r[0], "role": r[1], "status": r[3], "text": shown(r[1], r[2], r[3], r[4], r[5]), "at": iso(r[6])}
        for r in rows
    ]})


# The turn.


async def sweep_pending(conn, student_id: int) -> None:
    """A pending row older than a few minutes belongs to a request that died
    before its verdict was stored. It was never sent; mark it an error so it
    stops counting toward the limit."""
    await conn.execute(
        "UPDATE messages SET status = 'error' WHERE role = 'user' AND status = 'pending'"
        " AND created_at < now() - make_interval(mins => %s)"
        " AND conversation_id IN (SELECT id FROM conversations WHERE student_id = %s)",
        (PENDING_STALE_MINUTES, student_id),
    )


async def sweep_all_pending(conn) -> None:
    """Recover abandoned turns before the app begins serving requests."""
    await conn.execute(
        "UPDATE messages SET status = 'error' WHERE status = 'pending'"
        " AND created_at < now() - make_interval(mins => %s)",
        (PENDING_STALE_MINUTES,),
    )


async def messages_used(conn, student_id: int) -> int:
    """User messages that count toward the class limit: pending and ok."""
    cur = await conn.execute(
        "SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id"
        " WHERE c.student_id = %s AND m.role = 'user' AND m.status IN ('pending', 'ok')",
        (student_id,),
    )
    return (await cur.fetchone())[0]


async def admit(request: Request, student: dict, conversation_id: int, text: str) -> tuple[int, str, str, int]:
    """Step 1: checks and the pending user row, under the student row lock.
    Returns (user message id, model, class instructions, lesson session id)."""
    pool = request.app.state.pool
    async with pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT l.state, c.message_limit, c.instructions, l.id FROM students st"
            " JOIN lesson_sessions l ON l.id = st.lesson_session_id JOIN classes c ON c.id = l.class_id"
            " WHERE st.id = %s AND st.name IS NOT NULL AND NOT st.removed FOR UPDATE OF st",
            (student["id"],),
        )
        row = await cur.fetchone()
        if row is None or row[0] == "closed":
            raise HTTPError(401, "not_signed_in")
        state, limit, instructions, lesson_id = row
        if state == "paused":
            raise HTTPError(409, "paused")
        model = await own_conversation(conn, student, conversation_id)
        if model not in await workers.enabled_models(conn):
            raise HTTPError(409, "model_off")
        await sweep_pending(conn, student["id"])
        if limit is not None and await messages_used(conn, student["id"]) >= limit:
            raise HTTPError(409, "limit_reached")
        cur = await conn.execute(
            "INSERT INTO messages (conversation_id, role, text, status) VALUES (%s, 'user', %s, 'pending')"
            " RETURNING id",
            (conversation_id, text),
        )
        message_id = (await cur.fetchone())[0]
    return message_id, model, instructions, lesson_id


async def store_verdict(conn, message_id: int, verdict: guard.Verdict, status: str) -> None:
    cur = await conn.execute(
        "UPDATE messages SET status = %s, verdict = %s WHERE id = %s AND status = 'pending'",
        (status, Jsonb(verdict.to_json()), message_id))
    if cur.rowcount != 1:
        raise HTTPError(503, "chat_unavailable")
    await store_flags(conn, message_id, [verdict])


async def store_flags(conn, message_id: int, verdicts: list[guard.Verdict]) -> None:
    for verdict in verdicts:
        if verdict.flags:
            for category in verdict.categories or ("unspecified",):
                await conn.execute(
                    "INSERT INTO flags (message_id, category, action) VALUES (%s, %s, %s)",
                    (message_id, category, verdict.action))


async def history(conn, conversation_id: int, instructions: str) -> list[dict]:
    """What a worker is sent: the class instructions, then this
    conversation's approved prompts and the reply text students were shown.
    Never a pending, blocked or failed prompt, and never any identity."""
    cur = await conn.execute(
        "SELECT role, text, shown_length FROM messages WHERE conversation_id = %s"
        " AND ((role = 'user' AND status = 'ok') OR (role = 'assistant' AND shown_length > 0))"
        " ORDER BY id",
        (conversation_id,),
    )
    turns = [{"role": "system", "content": instructions}] if instructions.strip() else []
    for role, text, shown_length in await cur.fetchall():
        content = text if role == "user" else text[:shown_length]
        if content:
            turns.append({"role": role, "content": content})
    return turns


def sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


class Retry(Exception):
    """This worker failed before any text was shown; try another."""


class CutOff(Exception):
    """The reply stopped early after text was shown."""


async def deltas(response: httpx.Response):
    """Content pieces from an OpenAI-compatible stream. Ends normally on
    `[DONE]` or a finish_reason; raises CutOff if the stream just stops."""
    async for line in response.aiter_lines():
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if data == "[DONE]":
            return
        try:
            choice = json.loads(data)["choices"][0]
            if not isinstance(choice, dict):
                raise TypeError
            delta = choice.get("delta")
            if delta is not None and not isinstance(delta, dict):
                raise TypeError
            piece = (delta or {}).get("content")
            if piece is not None and not isinstance(piece, str):
                raise TypeError
        except (ValueError, KeyError, IndexError, TypeError):
            raise CutOff() from None
        if piece:
            yield piece
        if choice.get("finish_reason"):
            return
    raise CutOff()


async def send_message(request: Request):
    student = await require_student(request, sending=True)
    conversation_id = path_id(request)
    text = (await json_body(request)).get("text")
    if not isinstance(text, str) or not 1 <= len(text.strip()) <= MAX_TEXT:
        raise HTTPError(400, "bad_text")
    text = text.strip()
    app, pool = request.app, request.app.state.pool

    message_id, model, instructions, lesson_id = await admit(request, student, conversation_id, text)
    ctx = guard.Context(conversation_id, message_id, text)

    # Step 2, shielded so a disconnect cannot leave the verdict half stored.
    with anyio.CancelScope(shield=True):
        try:
            verdict = await guard.check_prompt(app, ctx)
        except guard.GuardUnavailable:
            async with pool.connection() as conn, conn.transaction():
                await conn.execute("UPDATE messages SET status = 'error' WHERE id = %s", (message_id,))
                await publish(conn, lesson_id, "message", student=student["id"], message=message_id)
            log.warning("guard unavailable; message %s refused", message_id)
            raise HTTPError(503, "chat_unavailable") from None
        status = "blocked" if verdict.blocks else "ok"
        async with pool.connection() as conn, conn.transaction():
            await store_verdict(conn, message_id, verdict, status)
            await publish(conn, lesson_id, "message", student=student["id"], message=message_id)
            body = {"model": model, "stream": True, "messages": await history(conn, conversation_id, instructions)}
    if verdict.blocks:
        return JSONResponse({"blocked": verdict.message})

    return ClosingStreamingResponse(reply(app, pool, ctx, student, lesson_id, model, body),
                                    media_type="text/event-stream", headers={"Cache-Control": "no-store"})


class ClosingStreamingResponse(StreamingResponse):
    """Close the chat generator when an ASGI send is cancelled.

    Uvicorn currently advertises ASGI 2.3, where Starlette watches for a
    disconnect in a sibling task. If that task cancels a backpressured send,
    explicitly closing the iterator is what releases the worker lease, closes
    the upstream response and stores the partial reply.
    """

    async def stream_response(self, send) -> None:
        iterator = self.body_iterator
        try:
            await send({"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers})
            async for chunk in iterator:
                if not isinstance(chunk, (bytes, memoryview)):
                    chunk = chunk.encode(self.charset)
                await send({"type": "http.response.body", "body": chunk, "more_body": True})
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            close = getattr(iterator, "aclose", None)
            if close is not None:
                with anyio.CancelScope(shield=True):
                    await close()


async def reply(app, pool, ctx: guard.Context, student: dict, lesson_id: int, model: str, body: dict):
    """Steps 3 and 4: stream the reply, then store it."""
    try:
        async with pool.connection() as conn:
            cur = await conn.execute(
                "INSERT INTO messages (conversation_id, role, text, status, shown_length)"
                " VALUES (%s, 'assistant', '', 'pending', 0) RETURNING id",
                (ctx.conversation_id,),
            )
            reply_id = (await cur.fetchone())[0]
    except Exception as exc:
        log.error("could not start reply: %s", type(exc).__name__)
        yield sse("error", {"message": NOT_SAVED})
        return

    generated: list[str] = []
    released: list[str] = []
    gate: guard.ReplyGate | None = None
    worker_id: str | None = None
    tried: set[str] = set()
    outcome = ("error", None)  # (status, event to send) if nothing better happens
    stored = False

    async def store(status: str) -> bool:
        verdicts = gate.verdicts if gate else []
        deciding = next((v for v in reversed(verdicts) if v.blocks), verdicts[-1] if verdicts else None)
        with anyio.CancelScope(shield=True):
            try:
                async with pool.connection() as conn, conn.transaction():
                    await conn.execute(
                        "UPDATE messages SET text = %s, status = %s, shown_length = %s, worker_id = %s,"
                        " verdict = %s WHERE id = %s",
                        ("".join(generated), status, len("".join(released)), worker_id,
                         Jsonb(deciding.to_json()) if deciding else None, reply_id),
                    )
                    await store_flags(conn, reply_id, verdicts)
                    await publish(conn, lesson_id, "message", student=student["id"], message=reply_id)
                return True
            except Exception as exc:
                log.error("could not store reply %s: %s", reply_id, type(exc).__name__)
                return False

    try:
        for _ in range(MAX_TRIES):
            lease = app.state.router.lease(pool, model, app.state.clock(), frozenset(tried))
            try:
                worker = await lease.__aenter__()
            except workers.NoWorker:
                break
            try:
                tried.add(worker.id)
                worker_id = worker.id
                # Persist the selected worker before any chat content leaves
                # the server. A crash during generation then leaves an
                # attributable pending row for startup recovery.
                async with pool.connection() as conn:
                    await conn.execute(
                        "UPDATE messages SET worker_id = %s WHERE id = %s",
                        (worker_id, reply_id),
                    )
                gate = guard.reply_gate(app, ctx)
                generated, released = [], []
                stream = stream_from(app, worker, body, gate, generated)
                try:
                    try:
                        async for out in stream:
                            released.append(out)
                            yield sse("delta", {"text": out})
                        tail = await gate.finish()
                        if tail:
                            released.append(tail)
                            yield sse("delta", {"text": tail})
                    finally:
                        await stream.aclose()
                except (httpx.ConnectError, httpx.ConnectTimeout):
                    await workers.mark_down(pool, worker.id)
                    log.warning("worker %s refused a connection; marked down", worker.id)
                    continue
                except Retry:
                    log.warning("worker %s failed before replying; trying another", worker.id)
                    continue
                except (CutOff, httpx.HTTPError, TimeoutError):
                    if not released:
                        log.warning("worker %s failed before any text was shown; trying another", worker.id)
                        continue
                    log.warning("reply %s cut off from worker %s", reply_id, worker.id)
                    outcome = ("error", ("error", {"message": CUT_OFF}))
                    break
                except guard.ReplyBlocked as blocked:
                    outcome = ("blocked", ("blocked", {"message": blocked.verdict.message}))
                    break
                except guard.GuardUnavailable:
                    log.warning("guard unavailable during reply %s", reply_id)
                    outcome = ("error", ("error", {"message": UNAVAILABLE}))
                    break
                outcome = ("ok", ("done", {"id": reply_id}))
                break
            finally:
                await lease.__aexit__(None, None, None)
        status, event = outcome
        if event is None:
            event = ("error", {"message": NO_MODEL})
            generated, released = [], []
        stored = await store(status)
        if stored:
            yield sse(*event)
        else:
            yield sse("error", {"message": NOT_SAVED})
    except BaseException:
        # The student went away (or something unexpected broke): keep what
        # was generated, marked as an error, unless it was already stored.
        if not stored:
            await store("error")
        raise


async def stream_from(app, worker: workers.Worker, body: dict, gate: guard.ReplyGate, generated: list[str]):
    """Released text from one worker attempt. Raises Retry or httpx errors
    before the first chunk, CutOff after."""
    client: httpx.AsyncClient = app.state.worker_client
    timeout = httpx.Timeout(BETWEEN_CHUNKS_TIMEOUT, connect=CONNECT_TIMEOUT)
    request = client.build_request(
        "POST",
        f"{worker.address}/v1/chat/completions",
        json=body,
        headers={"authorization": f"Bearer {worker.api_key}"},
        timeout=timeout,
    )
    response: httpx.Response | None = None
    try:
        # One deadline covers dispatch, response headers and the first content
        # chunk. Later gaps use the client's 120-second read timeout.
        async with asyncio.timeout(first_chunk_timeout()):
            response = await client.send(request, stream=True)
            if response.status_code != 200:
                raise Retry()
            pieces = deltas(response).__aiter__()
            try:
                piece = await pieces.__anext__()
            except StopAsyncIteration:
                return
        generated.append(piece)
        out = await gate.feed(piece)
        if out:
            yield out
        async for piece in pieces:
            generated.append(piece)
            out = await gate.feed(piece)
            if out:
                yield out
    finally:
        if response is not None:
            # This generator can be cancelled while it is blocked on an
            # upstream read, before an outer response-close shield can run.
            with anyio.CancelScope(shield=True):
                await response.aclose()


routes = [
    Route("/api/student/models", models, methods=["GET"]),
    Route("/api/student/conversations", list_conversations, methods=["GET"]),
    Route("/api/student/conversations", new_conversation, methods=["POST"]),
    Route("/api/student/conversations/{id}", get_conversation, methods=["GET"]),
    Route("/api/student/conversations/{id}/messages", send_message, methods=["POST"]),
]
