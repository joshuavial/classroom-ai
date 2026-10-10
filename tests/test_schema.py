"""The schema's constraints and delete behaviour that later steps rely on."""

import hashlib
import secrets

import psycopg
import pytest


@pytest.fixture
def conn(dsn, app):
    # app fixture has migrated the run database.
    with psycopg.connect(dsn) as c:
        yield c
        c.rollback()


def lesson(conn) -> int:
    staff = conn.execute(
        "INSERT INTO staff (username, password_hash, role) VALUES (%s, 'x', 'teacher') RETURNING id",
        (f"teacher-{secrets.token_hex(4)}",),
    ).fetchone()[0]
    cls = conn.execute(
        "INSERT INTO classes (teacher_id, name) VALUES (%s, 'Year 9') RETURNING id", (staff,)
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO lesson_sessions (class_id) VALUES (%s) RETURNING id", (cls,)
    ).fetchone()[0]


def test_unbind_keeps_old_row_and_adds_fresh_one(conn):
    session = lesson(conn)
    old = conn.execute(
        "INSERT INTO students (lesson_session_id, code, name, bound_at)"
        " VALUES (%s, '123456', 'Aroha', now()) RETURNING id", (session,)
    ).fetchone()[0]
    conn.execute("UPDATE students SET removed = true WHERE id = %s", (old,))
    conn.execute("INSERT INTO students (lesson_session_id, code) VALUES (%s, '123456')", (session,))


def test_two_live_rows_with_one_code_are_refused(conn):
    session = lesson(conn)
    conn.execute("INSERT INTO students (lesson_session_id, code) VALUES (%s, '123456')", (session,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO students (lesson_session_id, code) VALUES (%s, '123456')", (session,))


def test_deleting_a_student_removes_their_data_and_cookies(conn):
    session = lesson(conn)
    student = conn.execute(
        "INSERT INTO students (lesson_session_id, code) VALUES (%s, '654321') RETURNING id", (session,)
    ).fetchone()[0]
    convo = conn.execute(
        "INSERT INTO conversations (student_id, model) VALUES (%s, 'm') RETURNING id", (student,)
    ).fetchone()[0]
    msg = conn.execute(
        "INSERT INTO messages (conversation_id, role, text, status, worker_id)"
        " VALUES (%s, 'user', 'hi', 'ok', 'gone-worker') RETURNING id", (convo,)
    ).fetchone()[0]
    conn.execute("INSERT INTO flags (message_id, category, action) VALUES (%s, 'Violent', 'block_flag')", (msg,))
    conn.execute(
        "INSERT INTO auth_sessions (token_hash, student_id, expires_at) VALUES (%s, %s, now())",
        (hashlib.sha256(b"t").digest(), student),
    )
    conn.execute("DELETE FROM students WHERE id = %s", (student,))
    for table, col, value in [("conversations", "student_id", student), ("messages", "conversation_id", convo),
                              ("flags", "message_id", msg), ("auth_sessions", "student_id", student)]:
        assert conn.execute(f"SELECT count(*) FROM {table} WHERE {col} = %s", (value,)).fetchone()[0] == 0


def test_auth_session_needs_exactly_one_principal(conn):
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, expires_at) VALUES (%s, now())",
            (hashlib.sha256(b"u").digest(),),
        )


def test_auth_session_refuses_two_principals(conn):
    session = lesson(conn)
    staff = conn.execute("SELECT teacher_id FROM classes JOIN lesson_sessions l ON l.class_id = classes.id"
                         " WHERE l.id = %s", (session,)).fetchone()[0]
    student = conn.execute(
        "INSERT INTO students (lesson_session_id, code) VALUES (%s, '111111') RETURNING id", (session,)
    ).fetchone()[0]
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO auth_sessions (token_hash, staff_id, student_id, expires_at) VALUES (%s, %s, %s, now())",
            (hashlib.sha256(b"v").digest(), staff, student),
        )


def test_deleting_staff_keeps_audit_and_flag_reviews(conn):
    staff = conn.execute(
        "INSERT INTO staff (username, password_hash, role) VALUES ('gone', 'x', 'admin') RETURNING id"
    ).fetchone()[0]
    conn.execute("INSERT INTO audit (staff_id, username, action) VALUES (%s, 'gone', 'model.enable')", (staff,))
    session = lesson(conn)
    student = conn.execute(
        "INSERT INTO students (lesson_session_id, code) VALUES (%s, '222222') RETURNING id", (session,)
    ).fetchone()[0]
    convo = conn.execute(
        "INSERT INTO conversations (student_id, model) VALUES (%s, 'm') RETURNING id", (student,)
    ).fetchone()[0]
    msg = conn.execute(
        "INSERT INTO messages (conversation_id, role, status) VALUES (%s, 'user', 'ok') RETURNING id", (convo,)
    ).fetchone()[0]
    flag = conn.execute(
        "INSERT INTO flags (message_id, category, action, reviewed_by, reviewed_at)"
        " VALUES (%s, 'Violent', 'allow_flag', %s, now()) RETURNING id", (msg, staff)
    ).fetchone()[0]
    conn.execute("DELETE FROM staff WHERE id = %s", (staff,))
    row = conn.execute("SELECT staff_id, username FROM audit WHERE username = 'gone'").fetchone()
    assert row == (None, "gone")
    reviewed = conn.execute("SELECT reviewed_by, reviewed_at IS NOT NULL FROM flags WHERE id = %s", (flag,)).fetchone()
    assert reviewed == (None, True)


def test_closed_state_needs_closed_time(conn):
    session = lesson(conn)
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("UPDATE lesson_sessions SET state = 'closed' WHERE id = %s", (session,))
