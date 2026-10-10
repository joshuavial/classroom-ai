"""Retention with a fake clock, and exporting and deleting one student."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app import db, records

NOW = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
DAY = timedelta(days=1)


@pytest.fixture
async def pool(empty_dsn):
    pool = await db.open_pool(empty_dsn)
    await db.migrate(pool)
    yield pool
    await pool.close()


class Data:
    """Builds rows for tests. Returns ids."""

    def __init__(self, conn):
        self.conn = conn
        self.n = 0

    async def one(self, sql, params):
        cur = await self.conn.execute(sql + " RETURNING id", params)
        return (await cur.fetchone())[0]

    async def session(self, state="open", closed_at=None, opened_at=NOW - 60 * DAY):
        self.n += 1
        teacher = await self.one(
            "INSERT INTO staff (username, password_hash, role) VALUES (%s, 'x', 'teacher')",
            (f"t{self.n}",))
        class_id = await self.one("INSERT INTO classes (teacher_id, name) VALUES (%s, %s)",
                                  (teacher, f"Class {self.n}"))
        return await self.one(
            "INSERT INTO lesson_sessions (class_id, state, opened_at, closed_at) VALUES (%s, %s, %s, %s)",
            (class_id, state, opened_at, closed_at))

    async def student(self, session_id, code="123456", name="Aroha"):
        return await self.one(
            "INSERT INTO students (lesson_session_id, code, name, bound_at) VALUES (%s, %s, %s, %s)",
            (session_id, code, name, NOW - 50 * DAY))

    async def conversation(self, student_id, started_at):
        return await self.one(
            "INSERT INTO conversations (student_id, model, started_at) VALUES (%s, 'gemma-4-e2b-it', %s)",
            (student_id, started_at))

    async def message(self, conversation_id, created_at, text="hi", role="user", status="ok"):
        return await self.one(
            "INSERT INTO messages (conversation_id, role, text, status, created_at)"
            " VALUES (%s, %s, %s, %s, %s)", (conversation_id, role, text, status, created_at))

    async def flag(self, message_id, created_at=NOW - 40 * DAY):
        return await self.one(
            "INSERT INTO flags (message_id, category, action, created_at) VALUES (%s, 'violence', 'allow_flag', %s)",
            (message_id, created_at))


async def ids(conn, table):
    cur = await conn.execute(f"SELECT id FROM {table} ORDER BY id")
    return [r[0] for r in await cur.fetchall()]


# Retention


async def test_retention_deletes_only_what_is_past_the_cutoff(pool):
    cutoff = NOW - 30 * DAY
    just = timedelta(microseconds=1)
    async with pool.connection() as conn:
        d = Data(conn)
        session = await d.session()
        student = await d.student(session)
        conv = await d.conversation(student, cutoff - DAY)
        old = await d.message(conv, cutoff - just)
        old_flag = await d.flag(old)
        kept = await d.message(conv, cutoff)
        kept_flag = await d.flag(kept)
        # A conversation whose every message is past the cutoff goes too.
        gone_conv = await d.conversation(student, cutoff - 5 * DAY)
        await d.message(gone_conv, cutoff - 4 * DAY)
        # An empty conversation started inside the period stays.
        new_conv = await d.conversation(student, cutoff + DAY)

    counts = await records.run_retention(pool, NOW)

    assert counts == {"messages": 2, "conversations": 1, "sessions": 0}
    async with pool.connection() as conn:
        assert await ids(conn, "messages") == [kept]
        assert await ids(conn, "flags") == [kept_flag]
        assert old_flag not in await ids(conn, "flags")
        assert await ids(conn, "conversations") == [conv, new_conv]
        assert await ids(conn, "lesson_sessions") == [session]


async def test_retention_deletes_closed_sessions_with_nothing_left(pool):
    cutoff = NOW - 30 * DAY
    async with pool.connection() as conn:
        d = Data(conn)
        closed_old = await d.session("closed", cutoff - DAY)
        await d.student(closed_old)
        closed_old_with_message = await d.session("closed", cutoff - DAY)
        conv = await d.conversation(await d.student(closed_old_with_message), cutoff - DAY)
        await d.message(conv, cutoff + DAY)
        closed_recent = await d.session("closed", cutoff + DAY)
        open_old = await d.session("open", opened_at=NOW - 400 * DAY)
        paused_old = await d.session("paused", opened_at=NOW - 400 * DAY)
        await d.conversation(await d.student(paused_old), NOW - 400 * DAY)
        closed_at_cutoff = await d.session("closed", cutoff)
        await conn.execute(
            "INSERT INTO auth_sessions (token_hash, student_id, expires_at, csrf_token)"
            " SELECT sha256(id::text::bytea), id, %s, 'x' FROM students", (NOW + DAY,))

    counts = await records.run_retention(pool, NOW)

    async with pool.connection() as conn:
        assert sorted(await ids(conn, "lesson_sessions")) == sorted(
            [closed_old_with_message, closed_recent, open_old, paused_old, closed_at_cutoff])
        cur = await conn.execute("SELECT count(*) FROM students WHERE lesson_session_id = %s", (closed_old,))
        assert (await cur.fetchone())[0] == 0
        cur = await conn.execute("SELECT count(*) FROM auth_sessions")
        assert (await cur.fetchone())[0] == 2  # the deleted session's student lost theirs
    assert counts["sessions"] == 1


@pytest.mark.parametrize("stored,days", [
    (None, 30), (7, 7), (3650, 3650), (0, 30), (-5, 30), (3651, 30), ("7", 30), (7.5, 30), (True, 30),
])
async def test_retention_setting(pool, stored, days):
    async with pool.connection() as conn:
        if stored is not None:
            await conn.execute("INSERT INTO settings (key, value) VALUES ('retention_days', %s)",
                               (Jsonb(stored),))
        assert await records.retention_days(conn) == days
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        inside = await d.message(conv, NOW - days * DAY)
        await d.message(conv, NOW - days * DAY - timedelta(seconds=1))
    await records.run_retention(pool, NOW)
    async with pool.connection() as conn:
        assert await ids(conn, "messages") == [inside]


async def test_retention_keeps_a_message_written_during_the_run(pool):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        await d.message(conv, NOW - 40 * DAY)
        fresh = await d.message(conv, NOW)
    await records.run_retention(pool, NOW)
    async with pool.connection() as conn:
        assert await ids(conn, "messages") == [fresh]
        assert await ids(conn, "conversations") == [conv]


async def test_retention_loop_runs_now_then_hourly_and_survives_errors(pool, monkeypatch, caplog):
    runs, sleeps = [], []
    clock_times = iter([NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2)])

    async def fake_run(p, now):
        runs.append(now)
        if len(runs) == 2:
            raise RuntimeError("database went away")
        return {}

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(records, "run_retention", fake_run)
    with pytest.raises(asyncio.CancelledError):
        await records.retention_loop(pool, lambda: next(clock_times), sleep=fake_sleep)
    assert runs == [NOW, NOW + timedelta(hours=1), NOW + timedelta(hours=2)]
    assert sleeps == [3600, 3600, 3600]
    assert "retention failed" in caplog.text


async def test_retention_log_has_counts_not_content(pool, caplog):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        await d.message(conv, NOW - 40 * DAY, text="my secret question")
    caplog.set_level("INFO")
    await records.run_retention(pool, NOW)
    assert "retention deleted" in caplog.text
    assert "secret" not in caplog.text and "Aroha" not in caplog.text


async def test_app_runs_retention_at_startup(empty_dsn):
    from app.main import create_app

    pool = await db.open_pool(empty_dsn)
    await db.migrate(pool)
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        await d.message(conv, NOW - 40 * DAY)
    application = create_app(empty_dsn, clock=lambda: NOW)
    async with application.router.lifespan_context(application):
        for _ in range(50):
            async with pool.connection() as conn:
                if not await ids(conn, "messages"):
                    break
            await asyncio.sleep(0.05)
    async with pool.connection() as conn:
        assert await ids(conn, "messages") == []
    await pool.close()


# Export and delete


async def populated_student(conn):
    d = Data(conn)
    session = await d.session()
    student = await d.student(session, "111111", "Aroha")
    other = await d.student(session, "222222", "Ben")
    conv = await d.conversation(student, NOW - 2 * DAY)
    asked = await d.message(conv, NOW - 2 * DAY, "What is a volcano?")
    await d.flag(asked, NOW - 2 * DAY)
    await d.message(conv, NOW - 2 * DAY + timedelta(seconds=5), "A mountain that erupts.", role="assistant")
    other_conv = await d.conversation(other, NOW - DAY)
    await d.flag(await d.message(other_conv, NOW - DAY, "Ben's question"))
    await conn.execute(
        "INSERT INTO auth_sessions (token_hash, student_id, expires_at, csrf_token)"
        " SELECT sha256(id::text::bytea), id, %s, 'x' FROM students", (NOW + DAY,))
    return student, other


async def test_export_contains_the_student_and_nobody_else(pool):
    async with pool.connection() as conn:
        student, _ = await populated_student(conn)
        exported = await records.export_student(conn, student)
    t0 = (NOW - 2 * DAY).isoformat()
    assert exported == {
        "student": {"id": student, "code": "111111", "name": "Aroha", "removed": False,
                    "joined": (NOW - 50 * DAY).isoformat()},
        "lesson_session": {"id": exported["lesson_session"]["id"],
                           "opened": (NOW - 60 * DAY).isoformat(), "class": "Class 1"},
        "conversations": [{
            "model": "gemma-4-e2b-it", "started": t0,
            "messages": [
                {"role": "user", "text": "What is a volcano?", "status": "ok", "time": t0,
                 "flags": [{"category": "violence", "action": "allow_flag", "time": t0, "reviewed": False}]},
                {"role": "assistant", "text": "A mountain that erupts.", "status": "ok",
                 "time": (NOW - 2 * DAY + timedelta(seconds=5)).isoformat(), "flags": []},
            ],
        }],
    }
    assert "Ben" not in json.dumps(exported)


async def test_export_unknown_student(pool):
    async with pool.connection() as conn:
        assert await records.export_student(conn, 999) is None


async def test_delete_removes_the_student_and_everything_theirs(pool):
    async with pool.connection() as conn:
        student, other = await populated_student(conn)
        await conn.execute("INSERT INTO audit (username, action) VALUES ('admin', 'export_student')")
        assert await records.delete_student(conn, student)
        cur = await conn.execute(
            "SELECT (SELECT count(*) FROM students), (SELECT count(*) FROM conversations),"
            " (SELECT count(*) FROM messages), (SELECT count(*) FROM flags),"
            " (SELECT count(*) FROM auth_sessions), (SELECT count(*) FROM audit)")
        assert await cur.fetchone() == (1, 1, 1, 1, 1, 1)
        assert (await records.export_student(conn, other))["student"]["name"] == "Ben"
        assert not await records.delete_student(conn, student)


# A message written while retention runs is never lost through a cascade.


async def uncommitted_message(dsn, conversation_id):
    """A writer that has inserted a message and not yet committed."""
    writer = await psycopg.AsyncConnection.connect(dsn)
    await writer.execute(
        "INSERT INTO messages (conversation_id, role, text, status, created_at)"
        " VALUES (%s, 'user', 'fresh', 'ok', %s)", (conversation_id, NOW))
    return writer


async def run_promptly(pool):
    """Retention never waits on another transaction's locks."""
    return await asyncio.wait_for(records.run_retention(pool, NOW), 2)


async def test_retention_skips_a_conversation_getting_its_first_message(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
    writer = await uncommitted_message(empty_dsn, conv)
    assert (await run_promptly(pool))["conversations"] == 0
    await writer.commit()
    await writer.close()
    await run_promptly(pool)  # the next run sees the message and keeps it too
    async with pool.connection() as conn:
        assert await ids(conn, "conversations") == [conv]
        cur = await conn.execute("SELECT text FROM messages")
        assert await cur.fetchall() == [("fresh",)]


async def test_retention_skips_a_closed_session_getting_a_message(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        session = await d.session("closed", NOW - 40 * DAY)
        conv = await d.conversation(await d.student(session), NOW - 2 * DAY)
    writer = await uncommitted_message(empty_dsn, conv)
    assert (await run_promptly(pool))["sessions"] == 0
    await writer.commit()
    await writer.close()
    async with pool.connection() as conn:
        assert await ids(conn, "lesson_sessions") == [session]
        assert len(await ids(conn, "messages")) == 1


async def test_retention_skips_a_session_in_a_roster_edit_then_deletes_it_later(pool, empty_dsn):
    """A rename, unbind or remove locks the student and its session; retention
    must not wait for it (that could deadlock), only skip it this run."""
    async with pool.connection() as conn:
        d = Data(conn)
        session = await d.session("closed", NOW - 40 * DAY)
        student = await d.student(session)
        other = await d.session("closed", NOW - 40 * DAY)
    editor = await psycopg.AsyncConnection.connect(empty_dsn)
    await editor.execute(
        "SELECT 1 FROM students st JOIN lesson_sessions l ON l.id = st.lesson_session_id"
        " WHERE st.id = %s FOR UPDATE OF st FOR SHARE OF l", (student,))
    counts = await run_promptly(pool)
    assert counts["sessions"] == 1  # the other, idle session goes
    async with pool.connection() as conn:
        assert await ids(conn, "lesson_sessions") == [session]
    await editor.commit()
    await editor.close()
    await run_promptly(pool)
    async with pool.connection() as conn:
        assert await ids(conn, "lesson_sessions") == []


async def test_retention_skips_a_locked_message_then_deletes_it_later(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        old = await d.message(conv, NOW - 40 * DAY)
    reviewer = await psycopg.AsyncConnection.connect(empty_dsn)
    await reviewer.execute("SELECT 1 FROM messages WHERE id = %s FOR UPDATE", (old,))
    assert (await run_promptly(pool))["messages"] == 0
    await reviewer.commit()
    await reviewer.close()
    assert (await run_promptly(pool))["messages"] == 1


async def test_a_writer_that_loses_the_race_gets_an_error_not_a_silent_loss(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
    # Retention holds its locks; a message for that conversation waits, then fails.
    async with pool.connection() as conn:
        tx = conn.transaction()
        await tx.__aenter__()
        await conn.execute("SELECT id FROM conversations WHERE id = %s FOR UPDATE", (conv,))
        await conn.execute("DELETE FROM conversations WHERE id = %s", (conv,))
        writer = await psycopg.AsyncConnection.connect(empty_dsn)
        insert = asyncio.create_task(writer.execute(
            "INSERT INTO messages (conversation_id, role, text, status) VALUES (%s, 'user', 'late', 'ok')",
            (conv,)))
        await asyncio.sleep(0.3)
        assert not insert.done()
        await tx.__aexit__(None, None, None)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        await insert
    await writer.close()


class RetentionMidExport:
    """Wraps a connection; after the export reads messages, another
    connection deletes them (and their flags) and commits, as retention would."""

    def __init__(self, conn, dsn):
        self.conn, self.dsn, self.fired = conn, dsn, False

    def __getattr__(self, name):
        return getattr(self.conn, name)

    async def execute(self, sql, params=()):
        cur = await self.conn.execute(sql, params)
        if "FROM messages" in sql and not self.fired:
            self.fired = True
            await cur.fetchall()
            async with await psycopg.AsyncConnection.connect(self.dsn, autocommit=True) as other:
                await other.execute("DELETE FROM messages")
            await cur.scroll(0, mode="absolute")
        return cur


async def test_export_sees_one_snapshot_while_retention_commits(pool, empty_dsn):
    async with pool.connection() as conn:
        student, _ = await populated_student(conn)
    async with pool.connection() as conn, conn.transaction():
        racing = RetentionMidExport(conn, empty_dsn)
        record = await records.export_in_snapshot(racing, student)
    assert racing.fired
    first = record["conversations"][0]["messages"][0]
    assert first["text"] == "What is a volcano?"
    assert first["flags"] == [{"category": "violence", "action": "allow_flag",
                               "time": (NOW - 2 * DAY).isoformat(), "reviewed": False}]


async def hold(dsn, sql, params):
    """Another transaction holding a lock, left open until committed."""
    other = await psycopg.AsyncConnection.connect(dsn)
    await other.execute(sql, params)
    return other


async def release(other):
    await other.commit()
    await other.close()


async def test_retention_skips_a_message_whose_flag_is_being_reviewed(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        reviewed = await d.message(conv, NOW - 40 * DAY)
        flag = await d.flag(reviewed)
        plain = await d.message(conv, NOW - 40 * DAY)
    reviewer = await hold(empty_dsn, "UPDATE flags SET reviewed_at = now() WHERE id = %s", (flag,))
    assert (await run_promptly(pool))["messages"] == 1  # the unflagged one goes
    async with pool.connection() as conn:
        assert await ids(conn, "messages") == [reviewed]
    await release(reviewer)
    assert (await run_promptly(pool))["messages"] == 1


async def test_retention_skips_a_session_whose_cookie_is_in_use(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        session = await d.session("closed", NOW - 40 * DAY)
        student = await d.student(session)
        await conn.execute(
            "INSERT INTO auth_sessions (token_hash, student_id, expires_at, csrf_token)"
            " VALUES (sha256('t'), %s, %s, 'x')", (student, NOW + DAY))
    user = await hold(empty_dsn, "SELECT 1 FROM auth_sessions FOR UPDATE", ())
    assert (await run_promptly(pool))["sessions"] == 0
    await release(user)
    assert (await run_promptly(pool))["sessions"] == 1


async def test_retention_skips_a_session_with_one_student_locked(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        busy = await d.session("closed", NOW - 40 * DAY)
        locked = await d.student(busy, "111111")
        await d.student(busy, "222222")
        idle = await d.session("closed", NOW - 40 * DAY)
    editor = await hold(empty_dsn, "SELECT 1 FROM students WHERE id = %s FOR UPDATE", (locked,))
    assert (await run_promptly(pool))["sessions"] == 1
    async with pool.connection() as conn:
        assert await ids(conn, "lesson_sessions") == [busy]
        cur = await conn.execute("SELECT count(*) FROM students WHERE lesson_session_id = %s", (busy,))
        assert (await cur.fetchone())[0] == 2
    await release(editor)
    assert (await run_promptly(pool))["sessions"] == 1


async def test_retention_skips_a_run_while_another_holds_its_lock(pool, empty_dsn):
    async with pool.connection() as conn:
        d = Data(conn)
        conv = await d.conversation(await d.student(await d.session()), NOW - 400 * DAY)
        await d.message(conv, NOW - 40 * DAY)
    other = await hold(empty_dsn, "SELECT pg_advisory_xact_lock(%s, %s)",
                       (records.LOCK_NAMESPACE, records.RETENTION_LOCK))
    assert await run_promptly(pool) == {"messages": 0, "conversations": 0, "sessions": 0}
    await release(other)
    assert (await run_promptly(pool))["messages"] == 1
