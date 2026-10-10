import json
import logging

import pytest

from app.main import JsonLines, from_env, setup_logging


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
