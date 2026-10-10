"""Backup, wipe and restore round trip (acceptance 10), through the real
scripts. A docker shim sends `docker compose exec db` to the test Postgres
container and records `docker compose stop/start app`."""

import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app import db
from tests.test_records import NOW, Data, populated_student

ROOT = Path(__file__).parents[1]

SHIM = """#!/bin/sh
if [ "$1" = compose ]; then
    shift
    case "$1" in
    exec)
        shift
        [ "$1" = -T ] && shift
        shift  # the service name
        exec "$REAL_DOCKER" exec -i -e POSTGRES_USER=postgres -e POSTGRES_DB="$TEST_DB" "$TEST_CONTAINER" "$@"
        ;;
    stop|start)
        echo "$1 $2" >> "$SHIM_LOG"
        exit 0
        ;;
    esac
    echo "shim: unexpected compose $*" >&2
    exit 99
fi
exec "$REAL_DOCKER" "$@"
"""


@pytest.fixture
def stack(empty_dsn, tmp_path):
    """Run scripts against the empty_dsn database as if it were the stack's."""
    port = empty_dsn.rsplit(":", 1)[1].split("/")[0]
    names = subprocess.run(["docker", "ps", "--filter", "name=classroom-ai-test-", "--format", "{{.Names}}"],
                           check=True, capture_output=True, text=True).stdout.split()
    container = next(n for n in names if subprocess.run(
        ["docker", "port", n, "5432/tcp"], capture_output=True, text=True).stdout.strip().endswith(f":{port}"))
    shim = tmp_path / "bin"
    shim.mkdir()
    (shim / "docker").write_text(SHIM)
    (shim / "docker").chmod(0o755)
    log = tmp_path / "compose.log"
    env = {**os.environ, "PATH": f"{shim}:{os.environ['PATH']}", "REAL_DOCKER": shutil.which("docker"),
           "TEST_CONTAINER": container, "TEST_DB": empty_dsn.rsplit("/", 1)[1], "SHIM_LOG": str(log)}

    def run(script, *args, stdin=""):
        return subprocess.run([str(ROOT / "scripts" / script), *args], env=env, cwd=tmp_path,
                              input=stdin, capture_output=True, text=True)

    run.log = log
    run.container = container
    return run


def snapshot(dsn) -> dict:
    """Every row of every table, and the column layout."""
    with psycopg.connect(dsn) as conn:
        tables = [r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1")]
        rows = {t: conn.execute(f'SELECT * FROM "{t}" ORDER BY 1').fetchall() for t in tables}
        rows["_columns"] = conn.execute(
            "SELECT table_name, column_name, data_type, is_nullable, column_default"
            " FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2").fetchall()
        rows["_constraints"] = conn.execute(
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE connamespace = 'public'::regnamespace ORDER BY 1").fetchall()
        rows["_indexes"] = conn.execute(
            "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1").fetchall()
        # Identity counters, so rows added after a restore get fresh ids.
        rows["_sequences"] = conn.execute(
            "SELECT sequencename, last_value FROM pg_sequences WHERE schemaname = 'public' ORDER BY 1").fetchall()
    return rows


async def populate(dsn):
    pool = await db.open_pool(dsn)
    await db.migrate(pool)
    async with pool.connection() as conn:
        await populated_student(conn)
        d = Data(conn)
        d.n = 10  # populated_student used t1
        await d.session("closed", NOW)
        await conn.execute("INSERT INTO settings (key, value) VALUES ('retention_days', %s)", (Jsonb(14),))
        await conn.execute("INSERT INTO audit (staff_id, username, action, detail)"
                           " VALUES (1, 't1', 'export_student', %s)", (Jsonb({"student": 1}),))
        await conn.execute("INSERT INTO models (name, enabled) VALUES ('gemma-4-e2b-it', true)")
        await conn.execute(
            "INSERT INTO workers (id, address, api_key, models, capacity, last_heartbeat)"
            " VALUES ('w1', 'http://10.0.0.5:8081', %s, %s, 4, %s)",
            ("k" * 43, Jsonb(["gemma-4-e2b-it"]), NOW))
    await pool.close()


def wipe(dsn):
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public AUTHORIZATION pg_database_owner")


async def test_backup_wipe_restore_returns_everything(stack, empty_dsn, tmp_path):
    await populate(empty_dsn)
    before = snapshot(empty_dsn)
    assert all(before[t] for t in ("staff", "students", "conversations", "messages", "flags",
                                   "settings", "audit", "models", "workers", "schema_migrations"))

    r = stack("backup.sh", "out.dump")
    assert r.returncode == 0, r.stderr
    dump = tmp_path / "out.dump"
    assert dump.stat().st_size > 0 and oct(dump.stat().st_mode & 0o777) == "0o600"
    assert not (tmp_path / "out.dump.partial").exists()

    wipe(empty_dsn)
    assert snapshot(empty_dsn)["_columns"] == []

    r = stack("restore.sh", "--yes", "out.dump")
    assert r.returncode == 0, r.stderr
    assert snapshot(empty_dsn) == before
    assert stack.log.read_text().splitlines() == ["stop app", "start app"]

    pool = await db.open_pool(empty_dsn)
    try:
        assert await db.migrate(pool) == []  # the app starts on it with nothing to apply
    finally:
        await pool.close()


async def test_restore_replaces_newer_data(stack, empty_dsn):
    await populate(empty_dsn)
    assert stack("backup.sh", "out.dump").returncode == 0
    before = snapshot(empty_dsn)
    with psycopg.connect(empty_dsn) as conn:
        conn.execute("DELETE FROM students")
        conn.execute("CREATE TABLE stray (id int)")
    assert stack("restore.sh", "--yes", "out.dump").returncode == 0
    assert snapshot(empty_dsn) == before


async def test_restore_refuses_a_file_that_is_not_a_backup(stack, empty_dsn, tmp_path):
    await populate(empty_dsn)
    before = snapshot(empty_dsn)
    (tmp_path / "bad.dump").write_bytes(b"PGDMP not really")
    for args in (["--yes", "bad.dump"], ["--yes", "missing.dump"], []):
        r = stack("restore.sh", *args)
        assert r.returncode != 0
    assert snapshot(empty_dsn) == before
    assert not stack.log.exists()  # the app was never stopped


async def test_failed_restore_leaves_the_database_as_it_was(stack, empty_dsn, tmp_path):
    await populate(empty_dsn)
    assert stack("backup.sh", "good.dump").returncode == 0
    before = snapshot(empty_dsn)
    # A truncated dump still lists its contents but cannot be unpacked in full.
    good = (tmp_path / "good.dump").read_bytes()
    (tmp_path / "cut.dump").write_bytes(good[: len(good) - 200])
    r = stack("restore.sh", "--yes", "cut.dump")
    assert r.returncode == 1 and "as it was" in r.stderr
    assert snapshot(empty_dsn) == before
    assert stack.log.read_text().splitlines() == ["stop app", "start app"]


async def test_the_load_is_one_transaction(stack, empty_dsn):
    """The drop, create and load in restore.sh commit together or not at all."""
    await populate(empty_dsn)
    before = snapshot(empty_dsn)
    r = subprocess.run(
        ["docker", "exec", "-i", stack.container, "psql", "-X", "-q", "-v", "ON_ERROR_STOP=1",
         "--single-transaction", "-U", "postgres", "-d", empty_dsn.rsplit("/", 1)[1],
         "-c", "DROP SCHEMA public CASCADE", "-c", "CREATE SCHEMA public", "-f", "-"],
        input="CREATE TABLE half (id int); SELECT no_such_function();", capture_output=True, text=True)
    assert r.returncode != 0
    assert snapshot(empty_dsn) == before


async def test_restore_asks_first(stack, empty_dsn):
    await populate(empty_dsn)
    assert stack("backup.sh", "out.dump").returncode == 0
    with psycopg.connect(empty_dsn) as conn:
        conn.execute("DELETE FROM flags")
    before = snapshot(empty_dsn)
    r = stack("restore.sh", "out.dump", stdin="no\n")
    assert r.returncode == 1
    assert snapshot(empty_dsn) == before
    assert stack("restore.sh", "out.dump", stdin="yes\n").returncode == 0
    assert snapshot(empty_dsn) != before


def test_failed_backup_leaves_no_file(stack, tmp_path, monkeypatch):
    (tmp_path / "bin" / "docker").write_text("#!/bin/sh\nexit 1\n")
    r = stack("backup.sh", "out.dump")
    assert r.returncode != 0
    assert list(tmp_path.glob("out.dump*")) == []


async def test_restore_twice_in_a_row(stack, empty_dsn):
    """A backup taken after a restore restores too."""
    await populate(empty_dsn)
    assert stack("backup.sh", "first.dump").returncode == 0
    r = stack("restore.sh", "--yes", "first.dump")
    assert r.returncode == 0, r.stderr
    before = snapshot(empty_dsn)
    assert stack("backup.sh", "second.dump").returncode == 0
    r = stack("restore.sh", "--yes", "second.dump")
    assert r.returncode == 0, r.stderr
    assert snapshot(empty_dsn) == before
    with psycopg.connect(empty_dsn) as conn:
        owner = conn.execute("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public'").fetchone()
        assert owner == ("pg_database_owner",)
        # Identity counters came back: a new row gets a fresh id.
        conn.execute("INSERT INTO audit (username, action) VALUES ('x', 'y')")


async def test_restore_a_backup_whose_schema_had_another_owner(stack, empty_dsn):
    """pg_dump writes CREATE SCHEMA public when its owner is not the default."""
    await populate(empty_dsn)
    with psycopg.connect(empty_dsn) as conn:
        conn.execute("ALTER SCHEMA public OWNER TO postgres")
    assert stack("backup.sh", "out.dump").returncode == 0
    before = snapshot(empty_dsn)
    r = stack("restore.sh", "--yes", "out.dump")
    assert r.returncode == 0, r.stderr
    assert snapshot(empty_dsn) == before
