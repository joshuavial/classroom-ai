"""The Python API and gateway: /healthz and the JSON API under /api/."""

import asyncio
import contextlib
import json
import logging
import os
import sys

import httpx
import psycopg
from psycopg_pool import PoolTimeout
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import auth, chat, classes, console, db, guard, guard_settings, live, records, students, workers

log = logging.getLogger("app")


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {"time": self.formatTime(record), "level": record.levelname, "msg": record.getMessage()}
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line)


class DropQueryStrings(logging.Filter):
    """Access log lines keep the path but never the query string, which can
    hold a student code typed into the admin search."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) >= 3 and isinstance(record.args[2], str):
            args = list(record.args)
            args[2] = args[2].split("?", 1)[0]
            record.args = tuple(args)
        return True


class PoolErrorDetails(logging.Filter):
    """Keep database failure events, but never exception text or traces."""

    def filter(self, record: logging.LogRecord) -> bool:
        def safe(value):
            return type(value).__name__ if isinstance(value, BaseException) else value

        record.msg = safe(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(safe(value) for value in record.args)
        elif isinstance(record.args, dict):
            record.args = {key: safe(value) for key, value in record.args.items()}
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


def setup_logging() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLines())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    # uvicorn configures its own loggers before calling the factory; send them
    # through the same JSON handler.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers = [handler]
        logger.propagate = False
    logging.getLogger("uvicorn.access").addFilter(DropQueryStrings())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    # Filters are not inherited. The pool, its schedulers, and the driver
    # loggers each need the same one. psycopg.transaction and psycopg.waiting
    # are separate names, not records on "psycopg".
    for name in ("psycopg.pool", "psycopg_pool.sched", "psycopg_pool.sched_async",
                 "psycopg", "psycopg.transaction", "psycopg.waiting"):
        db_logger = logging.getLogger(name)
        if not any(isinstance(f, PoolErrorDetails) for f in db_logger.filters):
            db_logger.addFilter(PoolErrorDetails())


async def healthz(request: Request) -> JSONResponse:
    database, guard_ok = await asyncio.gather(
        db.check(request.app.state.pool),
        guard.health(request.app.state.guard_client, request.app.state.guard_url),
    )
    body = {
        "status": "ok" if database and guard_ok else "down",
        "db": "ok" if database else "down",
        "guard": "ok" if guard_ok else "down",
    }
    return JSONResponse(body, status_code=200 if database and guard_ok else 503)


async def unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("database unavailable: %s", type(exc).__name__)
    return JSONResponse({"error": "unavailable"}, status_code=503)


def create_app(
    dsn: str,
    worker_transport: httpx.AsyncBaseTransport | None = None,
    guard_transport: httpx.AsyncBaseTransport | None = None,
    clock=workers.utcnow,
) -> Starlette:
    """The transports and clock let tests stand in for services and time."""

    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        pool = await db.open_pool(dsn)
        try:
            applied = await db.migrate(pool)
            if applied:
                log.info("applied migrations %s", applied)
            await auth.ensure_setup_code(pool)
            await auth.prepare()
            async with pool.connection() as conn:
                await workers.ensure_join_token(conn)
                await chat.sweep_all_pending(conn)
            app.state.pool = pool
            app.state.clock = clock
            app.state.router = workers.Router()
            app.state.dsn = dsn
            app.state.guard_url = os.environ.get("GUARD_URL", "http://guard:8080")
            retention = asyncio.create_task(records.retention_loop(pool, clock))
            app.state.hub = live.Hub(dsn)
            await app.state.hub.start()
            try:
                async with (
                    httpx.AsyncClient(transport=worker_transport) as worker_client,
                    httpx.AsyncClient(transport=guard_transport) as guard_client,
                ):
                    app.state.worker_client = worker_client
                    app.state.guard_client = guard_client
                    yield
            finally:
                retention.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await retention
                await app.state.hub.stop()
        finally:
            await pool.close()

    app = Starlette(
        routes=[Route("/healthz", healthz), *auth.routes, *classes.routes, *students.routes,
                *workers.routes, *guard_settings.routes, *live.routes, *console.routes,
                *records.routes, *chat.routes],
        middleware=[Middleware(auth.CSRFMiddleware)],
        exception_handlers={
            auth.HTTPError: auth.http_error,
            psycopg.Error: unavailable,
            PoolTimeout: unavailable,
        },
        lifespan=lifespan,
    )
    app.state.limiter = auth.RateLimiter()
    # Student codes can be guessed: 30 tries a minute per address (architecture).
    app.state.join_limiter = auth.RateLimiter(limit=30)  # failed attempts
    app.state.join_total_limiter = auth.RateLimiter(limit=300)  # every attempt
    return app


def from_env() -> Starlette:
    """Entry point for uvicorn --factory."""
    setup_logging()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set; run scripts/init-env.sh first")
    return create_app(dsn)
