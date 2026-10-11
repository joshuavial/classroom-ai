import asyncio
import shutil

import psycopg
import pytest

from app import db

ALL = [v for v, _ in db.load_migrations()]
LAST = ALL[-1]


def name(offset: int, label: str) -> str:
    """A file name numbered after the real migrations."""
    return f"{LAST + offset:03d}_{label}.sql"
TABLES = {
    "staff", "auth_sessions", "classes", "lesson_sessions", "students", "conversations",
    "messages", "guard_checks", "flags", "workers", "models", "settings", "audit", "schema_migrations",
}


def tables(dsn: str) -> set[str]:
    with psycopg.connect(dsn) as conn:
        rows = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"
        ).fetchall()
    return {r[0] for r in rows}


def versions(dsn: str) -> list[int]:
    with psycopg.connect(dsn) as conn:
        return [r[0] for r in conn.execute("SELECT version FROM schema_migrations ORDER BY 1")]


async def run(dsn: str, schema_dir=db.SCHEMA_DIR):
    pool = await db.open_pool(dsn)
    try:
        return await db.migrate(pool, schema_dir)
    finally:
        await pool.close()


async def test_empty_database_gets_every_table(empty_dsn):
    assert await run(empty_dsn) == ALL
    assert tables(empty_dsn) == TABLES
    assert versions(empty_dsn) == ALL


async def test_already_migrated_database_is_left_alone(empty_dsn):
    await run(empty_dsn)
    assert await run(empty_dsn) == []
    assert versions(empty_dsn) == ALL


async def test_guard_migration_preserves_existing_messages_and_settings(empty_dsn, schema_copy):
    guard_migration = schema_copy / "005_guard_evidence.sql"
    held = guard_migration.read_text()
    guard_migration.unlink()
    assert await run(empty_dsn, schema_copy) == [1, 2, 3, 4]
    with psycopg.connect(empty_dsn) as conn:
        staff = conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES ('t', 'x', 'teacher') RETURNING id"
        ).fetchone()[0]
        cls = conn.execute(
            "INSERT INTO classes (teacher_id, name) VALUES (%s, 'Class') RETURNING id", (staff,)
        ).fetchone()[0]
        lesson = conn.execute(
            "INSERT INTO lesson_sessions (class_id) VALUES (%s) RETURNING id", (cls,)
        ).fetchone()[0]
        student = conn.execute(
            "INSERT INTO students (lesson_session_id, code) VALUES (%s, '123456') RETURNING id", (lesson,)
        ).fetchone()[0]
        conversation = conn.execute(
            "INSERT INTO conversations (student_id, model) VALUES (%s, 'm') RETURNING id", (student,)
        ).fetchone()[0]
        message = conn.execute(
            "INSERT INTO messages (conversation_id, role, text, status) VALUES (%s, 'user', 'old', 'ok') RETURNING id",
            (conversation,),
        ).fetchone()[0]
        conn.execute("INSERT INTO settings (key, value) VALUES ('guard_categories', '{\"existing\":true}')")
    guard_migration.write_text(held)
    assert await run(empty_dsn, schema_copy) == [5]
    with psycopg.connect(empty_dsn) as conn:
        assert conn.execute("SELECT text, guard_status FROM messages WHERE id = %s", (message,)).fetchone() == (
            "old",
            None,
        )
        assert conn.execute("SELECT value FROM settings WHERE key = 'guard_categories'").fetchone()[0] == {
            "existing": True
        }


async def test_concurrent_startup_applies_once(empty_dsn):
    pools = [await db.open_pool(empty_dsn) for _ in range(3)]
    try:
        results = await asyncio.gather(*(db.migrate(p) for p in pools))
    finally:
        for p in pools:
            await p.close()
    assert sorted(results) == [[], [], ALL]
    assert versions(empty_dsn) == ALL


@pytest.fixture
def schema_copy(tmp_path):
    target = tmp_path / "schema"
    shutil.copytree(db.SCHEMA_DIR, target)
    return target


async def test_later_migrations_apply_in_order(empty_dsn, schema_copy):
    (schema_copy / name(2, "c")).write_text("ALTER TABLE t2 ADD COLUMN c int;")
    (schema_copy / name(1, "b")).write_text("CREATE TABLE t2 (id int);")
    assert await run(empty_dsn, schema_copy) == ALL + [LAST + 1, LAST + 2]


async def test_failing_migration_applies_nothing_and_can_be_retried(empty_dsn, schema_copy):
    bad = schema_copy / name(1, "bad")
    bad.write_text("CREATE TABLE t2 (id int); SELECT no_such_function();")
    with pytest.raises(psycopg.errors.UndefinedFunction):
        await run(empty_dsn, schema_copy)
    assert tables(empty_dsn) == set()
    bad.write_text("CREATE TABLE t2 (id int);")
    assert await run(empty_dsn, schema_copy) == ALL + [LAST + 1]


@pytest.mark.parametrize("bad_name", ["1_short.sql", "999.sql", "notes.txt"])
async def test_bad_file_name_is_refused(empty_dsn, schema_copy, bad_name):
    (schema_copy / bad_name).write_text("SELECT 1;")
    with pytest.raises(db.MigrationError, match="not a migration"):
        await run(empty_dsn, schema_copy)


async def test_dotfiles_are_ignored(empty_dsn, schema_copy):
    (schema_copy / ".DS_Store").write_bytes(b"\0")
    assert await run(empty_dsn, schema_copy) == ALL


async def test_duplicate_number_is_refused(empty_dsn, schema_copy):
    (schema_copy / "001_again.sql").write_text("SELECT 1;")
    with pytest.raises(db.MigrationError, match="two migrations"):
        await run(empty_dsn, schema_copy)


async def test_edited_applied_migration_is_refused(empty_dsn, schema_copy):
    await run(empty_dsn, schema_copy)
    init = schema_copy / "001_init.sql"
    init.write_text(init.read_text() + "\n-- edited\n")
    with pytest.raises(db.MigrationError, match="changed after"):
        await run(empty_dsn, schema_copy)


async def test_migration_older_than_applied_is_refused(empty_dsn, schema_copy):
    (schema_copy / name(2, "c")).write_text("SELECT 1;")
    await run(empty_dsn, schema_copy)
    (schema_copy / name(1, "b")).write_text("SELECT 1;")
    with pytest.raises(db.MigrationError, match="older than applied"):
        await run(empty_dsn, schema_copy)


async def test_database_newer_than_code_is_refused(empty_dsn, schema_copy):
    (schema_copy / name(1, "b")).write_text("SELECT 1;")
    await run(empty_dsn, schema_copy)
    with pytest.raises(db.MigrationError, match="lacks"):
        await run(empty_dsn)


async def test_failure_on_migrated_database_keeps_data(empty_dsn, schema_copy):
    await run(empty_dsn, schema_copy)
    with psycopg.connect(empty_dsn) as conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('retention_days', '30')")
    (schema_copy / name(1, "bad")).write_text("CREATE TABLE t2 (id int); SELECT no_such_function();")
    with pytest.raises(psycopg.errors.UndefinedFunction):
        await run(empty_dsn, schema_copy)
    assert versions(empty_dsn) == ALL
    assert "t2" not in tables(empty_dsn)
    with psycopg.connect(empty_dsn) as conn:
        assert conn.execute("SELECT value FROM settings WHERE key = 'retention_days'").fetchone() == (30,)


async def test_health_check_gives_up_on_a_hung_database(dsn):
    from psycopg_pool import AsyncConnectionPool

    pool = AsyncConnectionPool(dsn, min_size=1, max_size=1, open=False)
    await pool.open(wait=True)
    try:
        async with pool.connection():
            # The only connection is held, so the ping waits until the deadline.
            assert await db.check(pool, timeout=0.5) is False
    finally:
        await pool.close()
