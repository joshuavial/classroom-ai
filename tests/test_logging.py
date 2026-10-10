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
