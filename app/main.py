"""The Python API and gateway: /healthz and the JSON API under /api/."""

import contextlib
import json
import logging
import os
import sys

import psycopg
from psycopg_pool import PoolTimeout
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app import auth, db

log = logging.getLogger("app")


class JsonLines(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line = {"time": self.formatTime(record), "level": record.levelname, "msg": record.getMessage()}
        if record.exc_info:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(line)


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


async def healthz(request: Request) -> JSONResponse:
    if await db.check(request.app.state.pool):
        return JSONResponse({"status": "ok", "db": "ok"})
    return JSONResponse({"status": "down", "db": "down"}, status_code=503)


async def unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("database unavailable: %s", type(exc).__name__)
    return JSONResponse({"error": "unavailable"}, status_code=503)


def create_app(dsn: str) -> Starlette:
    @contextlib.asynccontextmanager
    async def lifespan(app: Starlette):
        pool = await db.open_pool(dsn)
        try:
            applied = await db.migrate(pool)
            if applied:
                log.info("applied migrations %s", applied)
            await auth.ensure_setup_code(pool)
            await auth.prepare()
            app.state.pool = pool
            yield
        finally:
            await pool.close()

    app = Starlette(
        routes=[Route("/healthz", healthz), *auth.routes],
        middleware=[Middleware(auth.CSRFMiddleware)],
        exception_handlers={
            auth.HTTPError: auth.http_error,
            psycopg.OperationalError: unavailable,
            PoolTimeout: unavailable,
        },
        lifespan=lifespan,
    )
    app.state.limiter = auth.RateLimiter()
    return app


def from_env() -> Starlette:
    """Entry point for uvicorn --factory."""
    setup_logging()
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise SystemExit("DATABASE_URL is not set; run scripts/init-env.sh first")
    return create_app(dsn)

