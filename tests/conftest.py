"""Test fixtures: one Postgres container per test session, a disposable
database per run, and an ASGI client for the app."""

import secrets
import shutil
import subprocess
import time

import httpx
import psycopg
import pytest

from app.main import create_app

PG_IMAGE = "postgres:18.6@sha256:74935e72241653ca55e0414067e6d8763aceb8a810eb51b452253ec3dcfc4336"
PG_PASSWORD = "test"


def _docker(*args: str) -> str:
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="session")
def pg_server() -> str:
    """Start Postgres in Docker. Yields a DSN for its maintenance database."""
    if not shutil.which("docker"):
        pytest.fail("Docker is required for the test Postgres and was not found on PATH")
    name = f"classroom-ai-test-{secrets.token_hex(4)}"
    _docker(
        "run", "-d", "--rm", "--name", name,
        "-e", f"POSTGRES_PASSWORD={PG_PASSWORD}",
        "-p", "127.0.0.1::5432",
        PG_IMAGE,
    )
    try:
        port = _docker("port", name, "5432/tcp").splitlines()[0].rsplit(":", 1)[1]
        dsn = f"postgresql://postgres:{PG_PASSWORD}@127.0.0.1:{port}/postgres"
        deadline = time.monotonic() + 60
        while True:
            try:
                psycopg.connect(dsn, connect_timeout=2).close()
                break
            except psycopg.OperationalError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.5)
        yield dsn
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True)


def _make_database(server_dsn: str) -> str:
    name = f"test_{secrets.token_hex(6)}"
    with psycopg.connect(server_dsn, autocommit=True) as conn:
        conn.execute(f'CREATE DATABASE "{name}"')
    return server_dsn.rsplit("/", 1)[0] + "/" + name


@pytest.fixture(scope="session")
def dsn(pg_server: str) -> str:
    """The disposable database for this test run. The app migrates it."""
    return _make_database(pg_server)


@pytest.fixture
def empty_dsn(pg_server: str) -> str:
    """A fresh empty database for tests that need one (migration tests)."""
    return _make_database(pg_server)


@pytest.fixture(scope="session")
async def app(dsn: str):
    application = create_app(dsn)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def client(app):
    # https so Secure cookies are sent back, as in a browser.
    app.state.limiter.reset()
    app.state.join_limiter.reset()
    app.state.join_total_limiter.reset()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        yield c


@pytest.fixture
async def fresh_app(empty_dsn):
    """An app on its own new database, for first-run setup tests."""
    application = create_app(empty_dsn)
    async with application.router.lifespan_context(application):
        yield application


@pytest.fixture
async def fresh_client(fresh_app):
    transport = httpx.ASGITransport(app=fresh_app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        yield c
