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

import psycopg
from starlette.requests import Request
from starlette.responses import StreamingResponse
from starlette.routing import Route

from app.auth import require_staff

log = logging.getLogger("app.live")

CHANNEL = "classroom_live"
QUEUE_SIZE = 100
PING_SECONDS = 15.0
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
            except Exception:
                log.warning("live connection lost; reconnecting", exc_info=True)
            self.connected.clear()
            # Anything published while we were away is lost: tell clients.
            self.resync_all()
            await asyncio.sleep(self.retry_seconds)


def sse(event: str, data: dict | None = None) -> str:
    return f"event: {event}\ndata: {json.dumps(data or {})}\n\n"


async def events(request: Request) -> StreamingResponse:
    from app.classes import owned_session, path_id  # classes imports publish from here

    staff = await require_staff(request)
    session_id = path_id(request)
    async with request.app.state.pool.connection() as conn:
        await owned_session(conn, staff, session_id)
    hub: Hub = request.app.state.hub

    async def stream():
        # Subscribe inside the generator, so the finally below always runs
        # for a queue that was added.
        queue = hub.subscribe(session_id)
        loop = asyncio.get_running_loop()
        ends = loop.time() + STREAM_SECONDS
        try:
            yield sse("hello")
            while loop.time() < ends:
                try:
                    left = ends - loop.time()
                    event = await asyncio.wait_for(queue.get(), max(0.01, min(PING_SECONDS, left)))
                except TimeoutError:
                    if loop.time() < ends:
                        yield ": ping\n\n"
                    continue
                data = dict(event)
                yield sse(data.pop("k"), data)
        finally:
            hub.unsubscribe(session_id, queue)

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )


routes = [Route("/api/sessions/{id}/events", events, methods=["GET"])]
