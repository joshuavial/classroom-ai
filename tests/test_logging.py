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
