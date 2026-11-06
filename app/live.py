"""Live updates for the teacher console (step 7).

Writers call `publish` inside their transaction; Postgres delivers the
NOTIFY only if that transaction commits. Each app process runs one `Hub`
that LISTENs on a dedicated connection and passes events to the SSE
clients watching that lesson session, so several app processes all see
every event. See docs/architecture.md "Teacher console".
"""

import asyncio
import contextlib
import json
import logging
from typing import Any

import anyio
import psycopg
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route
from starlette.types import Send

from app.auth import SESSION_COOKIE, find_session, require_staff

log = logging.getLogger("app.live")

CHANNEL = "classroom_live"
QUEUE_SIZE = 100
PING_SECONDS = 15.0
SESSION_CHECK_SECONDS = 5.0
SESSION_CHECK_TIMEOUT_SECONDS = 2.0
# A stream ends after this long; EventSource reconnects at once, which checks
# the session and ownership again.
STREAM_SECONDS = 600.0
RESYNC = {"k": "resync"}


async def publish(conn, session_id: int, kind: str, **ids: int) -> None:
    """Queue an event for this lesson session. Ids only: never names, codes
    or chat text, which the console fetches over JSON."""
    payload = json.dumps({"s": session_id, "k": kind, **ids})
    await conn.execute("SELECT pg_notify(%s, %s)", (CHANNEL, payload))


class Hub:
    """Fans NOTIFY events out to the SSE clients of one app process."""

    def __init__(self, dsn: str, retry_seconds: float = 1.0):
        self.dsn, self.retry_seconds = dsn, retry_seconds
        self.subscribers: dict[int, set[asyncio.Queue]] = {}
        self.connected = asyncio.Event()
        self.task: asyncio.Task | None = None
        self.backend_pid: int | None = None

    async def start(self) -> None:
        self.task = asyncio.create_task(self.run())
        try:
            await asyncio.wait_for(self.connected.wait(), 30)
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    def subscribe(self, session_id: int) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(QUEUE_SIZE)
        self.subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: int, queue: asyncio.Queue) -> None:
        queues = self.subscribers.get(session_id, set())
        queues.discard(queue)
        if not queues:
            self.subscribers.pop(session_id, None)

    def deliver(self, session_id: int, event: dict) -> None:
        for queue in list(self.subscribers.get(session_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A client this far behind gets one resync in place of its
                # backlog and fetches fresh state.
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(RESYNC)

    def resync_all(self) -> None:
        for session_id in list(self.subscribers):
            self.deliver(session_id, RESYNC)

    async def run(self) -> None:
        first = True
        while True:
            try:
                # TCP keepalives notice a connection that died silently.
                async with await psycopg.AsyncConnection.connect(
                    self.dsn, autocommit=True, keepalives=1, keepalives_idle=30,
                    keepalives_interval=10, keepalives_count=3,
                ) as conn:
                    await conn.execute(f"LISTEN {CHANNEL}")
                    self.backend_pid = conn.info.backend_pid
                    self.connected.set()
                    if not first:
                        self.resync_all()
                    first = False
                    async for note in conn.notifies():
                        try:
                            event = json.loads(note.payload)
                            if not isinstance(event, dict):
                                raise ValueError("not an object")
                            self.deliver(int(event.pop("s")), event)
                        except (ValueError, KeyError, TypeError):
                            log.warning("ignored a malformed live event")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # Class name only: a psycopg traceback can include DETAIL.
                log.warning("live connection lost; reconnecting: %s", type(exc).__name__)
            self.connected.clear()
            # Anything published while we were away is lost: tell clients.
            self.resync_all()
            await asyncio.sleep(self.retry_seconds)


def sse(event: str, data: dict | None = None) -> str:
    return f"event: {event}\ndata: {json.dumps(data or {})}\n\n"


def finish_cancelled_check(task: asyncio.Task) -> None:
    """Consume a timed-out database check after its driver cleanup finishes."""
    try:
        task.result()
    except asyncio.CancelledError:
        pass
    except Exception as exc:
        log.warning("staff session check cleanup failed: %s", type(exc).__name__)


def find_uvicorn_cycle(send: Send):
    seen = set()
    queue = [send]
    while queue:
        item = queue.pop(0)
        item_id = id(item)
        if item_id in seen:
            continue
        seen.add(item_id)
        cycle = getattr(item, "__self__", None)
        if cycle is not None and hasattr(cycle, "transport"):
            return cycle
        if hasattr(item, "transport"):
            return item
        closure = getattr(item, "__closure__", None)
        if closure:
            for cell in closure:
                try:
                    val = cell.cell_contents
                except ValueError:
                    continue
                if val is not None and id(val) not in seen:
                    queue.append(val)
    return None


class LiveEventsResponse(StreamingResponse):
    def __init__(
        self,
        content,
        pool,
        session_token: str | None,
        staff_id: int,
        stop_event: asyncio.Event,
        **kwargs,
    ) -> None:
        super().__init__(content, **kwargs)
        self.pool = pool
        self.session_token = session_token
        self.staff_id = staff_id
        self.stop_event = stop_event

    async def stream_response(self, send: Send) -> None:
        iterator = self.body_iterator
        loop = asyncio.get_running_loop()
        ends = loop.time() + STREAM_SECONDS
        stop_event = self.stop_event
        response_started = False
        cycle = find_uvicorn_cycle(send)
        orig_write = None
        if cycle is not None and hasattr(cycle, "transport"):
            orig_write = getattr(cycle.transport, "write", None)
            if callable(orig_write):
                def guarded_write(data: Any) -> Any:
                    if (loop.time() >= ends or stop_event.is_set()) and (b"event:" in data or b"data:" in data):
                        return None
                    return orig_write(data)

                cycle.transport.write = guarded_write

        async def bounded_send(message: Any) -> bool:
            nonlocal response_started
            remaining = ends - loop.time()
            if remaining <= 0 or stop_event.is_set():
                return False
            send_task = asyncio.create_task(send(message))
            stop_task = asyncio.create_task(stop_event.wait())
            try:
                done, _ = await asyncio.wait(
                    {send_task, stop_task},
                    timeout=max(0.001, remaining),
                    return_when=asyncio.FIRST_COMPLETED,
                )
                if send_task not in done:
                    return False
                exc = send_task.exception()
                if exc is not None:
                    raise exc
                if message.get("type") == "http.response.start":
                    response_started = True
                return True
            finally:
                for t in (send_task, stop_task):
                    if not t.done():
                        t.cancel()
                with anyio.CancelScope(shield=True):
                    await asyncio.gather(send_task, stop_task, return_exceptions=True)

        async def send_body():
            nonlocal response_started
            try:
                if not await bounded_send(
                    {"type": "http.response.start", "status": self.status_code, "headers": self.raw_headers}
                ):
                    return
                response_started = True
                async for chunk in iterator:
                    if stop_event.is_set() or loop.time() >= ends:
                        break
                    if not isinstance(chunk, (bytes, memoryview)):
                        chunk = chunk.encode(self.charset)
                    if not await bounded_send({"type": "http.response.body", "body": chunk, "more_body": True}):
                        break
                    if stop_event.is_set() or loop.time() >= ends:
                        break
            finally:
                stop_event.set()

        async def supervise():
            next_session_check = loop.time() + SESSION_CHECK_SECONDS
            while loop.time() < ends and not stop_event.is_set():
                now = loop.time()
                wait_time = max(0.01, min(ends - now, next_session_check - now))
                try:
                    await asyncio.wait_for(stop_event.wait(), wait_time)
                    return
                except TimeoutError:
                    pass
                now = loop.time()
                if now >= ends or stop_event.is_set():
                    break
                if now >= next_session_check:
                    check = asyncio.create_task(find_session(self.pool, self.session_token))
                    try:
                        done, _ = await asyncio.wait(
                            {check}, timeout=max(0.01, min(SESSION_CHECK_TIMEOUT_SECONDS, ends - now))
                        )
                    except BaseException:
                        check.cancel()
                        check.add_done_callback(finish_cancelled_check)
                        raise
                    if not done:
                        check.cancel()
                        check.add_done_callback(finish_cancelled_check)
                        log.warning("staff session check failed: TimeoutError")
                        break
                    try:
                        current = check.result()
                    except psycopg.Error as exc:
                        log.warning("staff session check failed: %s", type(exc).__name__)
                        break
                    except Exception as exc:
                        log.warning("staff session check failed: %s", type(exc).__name__)
                        break
                    now = loop.time()
                    if now >= ends or stop_event.is_set():
                        break
                    if current is None or current["kind"] != "staff" or current["id"] != self.staff_id:
                        break
                    next_session_check += SESSION_CHECK_SECONDS
                    while next_session_check <= loop.time():
                        next_session_check += SESSION_CHECK_SECONDS
            stop_event.set()

        sender = asyncio.create_task(send_body())
        supervisor = asyncio.create_task(supervise())
        try:
            await asyncio.wait({sender, supervisor}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            stop_event.set()
            with anyio.CancelScope(shield=True):
                for t in (sender, supervisor):
                    if not t.done():
                        t.cancel()
                await asyncio.gather(sender, supervisor, return_exceptions=True)
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()
                if response_started:
                    try:
                        with anyio.fail_after(0.2):
                            await send({"type": "http.response.body", "body": b"", "more_body": False})
                    except Exception:
                        pass
                else:
                    if cycle is not None:
                        if hasattr(cycle, "disconnected"):
                            cycle.disconnected = True
                        transport = getattr(cycle, "transport", None)
                        if transport is not None and hasattr(transport, "close"):
                            transport.close()
                if orig_write is not None and cycle is not None and hasattr(cycle, "transport"):
                    cycle.transport.write = orig_write
            if supervisor.done() and not supervisor.cancelled():
                exc = supervisor.exception()
                if exc is not None:
                    log.exception("unexpected error in live stream supervisor", exc_info=exc)
            if sender.done() and not sender.cancelled():
                exc = sender.exception()
                if exc is not None:
                    raise exc


async def events(request: Request) -> StreamingResponse:
    from app.classes import owned_session, path_id  # classes imports publish from here

    staff = await require_staff(request)
    session_token = request.cookies.get(SESSION_COOKIE)
    session_id = path_id(request)
    async with request.app.state.pool.connection() as conn:
        await owned_session(conn, staff, session_id)
    hub: Hub = request.app.state.hub
    stop_event = asyncio.Event()

    async def stream():
        # Subscribe inside the generator, so the finally below always runs
        # for a queue that was added.
        queue = hub.subscribe(session_id)
        loop = asyncio.get_running_loop()
        ends = loop.time() + STREAM_SECONDS
        next_ping = loop.time() + PING_SECONDS
        try:
            yield sse("hello")
            while loop.time() < ends and not stop_event.is_set():
                now = loop.time()
                try:
                    event = await asyncio.wait_for(
                        queue.get(),
                        max(0.01, min(ends - now, next_ping - now)),
                    )
                except TimeoutError:
                    event = None
                now = loop.time()
                if now >= ends or stop_event.is_set():
                    break
                if event is not None:
                    data = dict(event)
                    yield sse(data.pop("k"), data)
                now = loop.time()
                if now >= next_ping and now < ends and not stop_event.is_set():
                    yield ": ping\n\n"
                    next_ping = now + PING_SECONDS
        finally:
            hub.unsubscribe(session_id, queue)

    return LiveEventsResponse(
        stream(),
        pool=request.app.state.pool,
        session_token=session_token,
        staff_id=staff["id"],
        stop_event=stop_event,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


routes = [Route("/api/sessions/{id}/events", events, methods=["GET"])]
