import json
import logging

import httpx
import psycopg
import pytest
from starlette.routing import Route

from app.main import JsonLines, create_app, from_env, setup_logging


def test_uvicorn_loggers_write_json():
    setup_logging()
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        handlers = logging.getLogger(name).handlers
        assert handlers and all(isinstance(h.formatter, JsonLines) for h in handlers)
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "GET /healthz %s", (200,), None)
    assert json.loads(JsonLines().format(record))["msg"] == "GET /healthz 200"


def test_missing_database_url_stops_with_a_message(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(SystemExit, match="init-env.sh"):
        from_env()


def test_access_log_drops_query_strings(caplog):
    """A student code typed into the admin search must not reach the log."""
    setup_logging()
    access = logging.getLogger("uvicorn.access")
    access.addHandler(caplog.handler)
    try:
        access.info('%s - "%s %s HTTP/%s" %d', "10.0.0.5:5000", "GET", "/api/admin/students?q=123456", "1.1", 200)
    finally:
        access.removeHandler(caplog.handler)
    assert "123456" not in caplog.text
    assert "/api/admin/students" in caplog.text


async def test_database_error_detail_stays_out_of_logs(caplog):
    secret = "DETAIL: failing row contains private chat text"

    async def fail(_request):
        raise psycopg.errors.CheckViolation(secret)

    app = create_app("postgresql://unused")
    app.router.routes.append(Route("/_test/database-error", fail))
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    caplog.set_level(logging.ERROR, logger="app")
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        response = await client.get("/_test/database-error")

    assert (response.status_code, response.json()) == (503, {"error": "unavailable"})
    assert "CheckViolation" in caplog.text
    assert secret not in caplog.text


async def test_pool_connection_error_keeps_only_the_exception_class(monkeypatch, capsys):
    from psycopg_pool import AsyncConnectionPool

    secret = "DETAIL: private prompt; QUERY: SELECT secret_chat FROM messages"

    async def fail_connect(*args, **kwargs):
        raise psycopg.OperationalError(secret)

    setup_logging()
    pool = AsyncConnectionPool("", min_size=0, max_size=1, open=False)
    monkeypatch.setattr(pool, "_connect", fail_connect)
    await pool.open()
    try:
        # Exercise the installed pool's real exception/logging branch.
        await pool._add_connection(None)
    finally:
        await pool.close()
    output = capsys.readouterr().out
    assert "error connecting" in output
    assert "OperationalError" in output
    assert secret not in output and "secret_chat" not in output
    assert all(json.loads(line)["level"] in {"INFO", "WARNING"} for line in output.splitlines())


async def test_pool_reset_error_keeps_only_the_exception_class(capsys):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from psycopg.pq import TransactionStatus
    from psycopg_pool import AsyncConnectionPool

    secret = "DETAIL: private reply; QUERY: INSERT INTO messages VALUES ('secret_chat')"

    async def fail_reset(conn):
        raise psycopg.errors.CheckViolation(secret)

    setup_logging()
    pool = AsyncConnectionPool("", min_size=0, max_size=1, open=False, reset=fail_reset)
    conn = SimpleNamespace(pgconn=SimpleNamespace(transaction_status=TransactionStatus.IDLE),
                           _pool=pool, close=AsyncMock())
    await pool._reset_connection(conn)
    output = capsys.readouterr().out
    assert "error resetting connection" in output
    assert "CheckViolation" in output
    assert secret not in output and "secret_chat" not in output
    conn.close.assert_awaited_once()
    assert conn._pool is None


@pytest.mark.parametrize("logger_name", ["psycopg.pool", "psycopg_pool.sched", "psycopg_pool.sched_async"])
def test_pool_exception_trace_and_mapping_args_stay_out_of_json(capsys, logger_name):
    setup_logging()
    error = psycopg.OperationalError("DETAIL: secret_chat in query")
    logger = logging.getLogger(logger_name)
    filters = len(logger.filters)
    setup_logging()
    assert len(logger.filters) == filters
    logger.warning("pool operation failed: %(error)s", {"error": error},
                   exc_info=(type(error), error, None), stack_info=True)
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert len(lines) == 1
    assert lines[0]["level"] == "WARNING"
    assert lines[0]["msg"] == "pool operation failed: OperationalError"
    assert "secret_chat" not in json.dumps(lines)
    assert "exc" not in lines[0]
