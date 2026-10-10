"""Records: retention, and exporting or deleting one student (R7.2, R7.3).

Plus the admin endpoints for the audit log, retention setting, student list,
export, delete and backup download. Restore is scripts/restore.sh.
"""

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from psycopg.conninfo import conninfo_to_dict, make_conninfo
from psycopg.types.json import Jsonb
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from app import auth
from app.classes import path_id

log = logging.getLogger("app.records")


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat()

DEFAULT_RETENTION_DAYS = 30
MAX_RETENTION_DAYS = 3650
RETENTION_EVERY_SECONDS = 3600
# Two-key advisory lock: (namespace, id). Keeps retention to one process at a time.
LOCK_NAMESPACE = 7_311
RETENTION_LOCK = 8


async def retention_days(conn) -> int:
    cur = await conn.execute("SELECT value FROM settings WHERE key = 'retention_days'")
    row = await cur.fetchone()
    if row is None:
        return DEFAULT_RETENTION_DAYS
    days = row[0]
    if type(days) is not int or not 1 <= days <= MAX_RETENTION_DAYS:
        log.error("retention_days setting %r is not 1 to %d; using %d",
                  days, MAX_RETENTION_DAYS, DEFAULT_RETENTION_DAYS)
        return DEFAULT_RETENTION_DAYS
    return days


async def run_retention(pool, now: datetime) -> dict[str, int]:
    """Delete what is older than the retention period. Returns counts.

    It never waits for a lock: rows another transaction holds (a message being
    written, a roster edit, a flag being reviewed, a cookie session in use)
    are skipped this run and deleted on a later one, so retention does not
    wait on them. That covers every row a delete cascades to as well.
    What it locked is checked again in a fresh statement before deleting.
    """
    counts = {"messages": 0, "conversations": 0, "sessions": 0}
    async with pool.connection() as conn, conn.transaction():
        # Anything SKIP LOCKED cannot skip (a table lock, say) fails the run
        # after this long instead of waiting: everything rolls back and the
        # next run tries again. If a deadlock ever did form, PostgreSQL would
        # abort one side; when that is retention, it simply retries next hour.
        await conn.execute("SET LOCAL lock_timeout = '200ms'")
        cur = await conn.execute("SELECT pg_try_advisory_xact_lock(%s, %s)", (LOCK_NAMESPACE, RETENTION_LOCK))
        if not (await cur.fetchone())[0]:
            log.info("retention already running elsewhere; skipped")
            return counts
        cutoff = now - timedelta(days=await retention_days(conn))

        # Messages, with their flags (ON DELETE CASCADE).
        cur = await conn.execute(
            "SELECT id FROM messages WHERE created_at < %s ORDER BY id FOR UPDATE SKIP LOCKED", (cutoff,))
        message_ids = {r[0] for r in await cur.fetchall()}
        message_ids -= await partly_locked(
            conn, message_ids, "SELECT message_id, id FROM flags WHERE message_id = ANY(%s)", "flags")
        cur = await conn.execute("DELETE FROM messages WHERE id = ANY(%s)", (list(message_ids),))
        counts["messages"] = cur.rowcount

        # Empty conversations. A message insert holds a lock on its
        # conversation, so one getting its first message is skipped.
        cur = await conn.execute(
            "SELECT c.id FROM conversations c WHERE c.started_at < %s"
            " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)"
            " ORDER BY c.id FOR UPDATE SKIP LOCKED", (cutoff,))
        candidates = [r[0] for r in await cur.fetchall()]
        cur = await conn.execute(
            "DELETE FROM conversations c WHERE c.id = ANY(%s)"
            " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)", (candidates,))
        counts["conversations"] = cur.rowcount

        # Closed sessions with no messages left, with their students, the
        # students' conversations and cookie sessions (all ON DELETE CASCADE).
        # A session goes only if all of those could be locked; holding them
        # also blocks new students, conversations and messages under it.
        cur = await conn.execute(
            "SELECT s.id FROM lesson_sessions s WHERE s.state = 'closed' AND s.closed_at < %s"
            " AND NOT EXISTS (SELECT 1 FROM students st JOIN conversations c ON c.student_id = st.id"
            "  JOIN messages m ON m.conversation_id = c.id WHERE st.lesson_session_id = s.id)"
            " ORDER BY s.id FOR UPDATE SKIP LOCKED", (cutoff,))
        session_ids = {r[0] for r in await cur.fetchall()}
        session_ids -= await partly_locked(
            conn, session_ids, "SELECT lesson_session_id, id FROM students WHERE lesson_session_id = ANY(%s)",
            "students")
        session_ids -= await partly_locked(
            conn, session_ids,
            "SELECT st.lesson_session_id, c.id FROM conversations c JOIN students st ON st.id = c.student_id"
            " WHERE st.lesson_session_id = ANY(%s)", "c")
        session_ids -= await partly_locked(
            conn, session_ids,
            "SELECT st.lesson_session_id, a.token_hash FROM auth_sessions a JOIN students st ON st.id = a.student_id"
            " WHERE st.lesson_session_id = ANY(%s)", "a")
        cur = await conn.execute(
            "DELETE FROM lesson_sessions s WHERE s.id = ANY(%s) AND s.state = 'closed'"
            " AND NOT EXISTS (SELECT 1 FROM students st JOIN conversations c ON c.student_id = st.id"
            "  JOIN messages m ON m.conversation_id = c.id WHERE st.lesson_session_id = s.id)",
            (list(session_ids),))
        counts["sessions"] = cur.rowcount
    log.info("retention deleted %s", counts)
    return counts


async def partly_locked(conn, group_ids: set[int], rows_sql: str, lock_of: str) -> set:
    """Groups (a message, a session) with a row under them that could not be
    locked without waiting. rows_sql selects (group id, row id) for groups in
    %s; lock_of names the row's table or alias. Rows that could be locked now
    are."""
    if not group_ids:
        return set()
    ids = list(group_ids)
    # rows_sql and lock_of are constants in this module, never request input.
    cur = await conn.execute(f"{rows_sql} ORDER BY 2 FOR UPDATE OF {lock_of} SKIP LOCKED", (ids,))
    locked = {row[1] for row in await cur.fetchall()}
    cur = await conn.execute(rows_sql, (ids,))
    return {group for group, row in await cur.fetchall() if row not in locked}


async def retention_loop(
    pool,
    clock: Callable[[], datetime],
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> None:
    """Run retention now and then every hour; a failed run waits for the next."""
    while True:
        try:
            await run_retention(pool, clock())
        except Exception:
            log.exception("retention failed")
        await sleep(RETENTION_EVERY_SECONDS)


async def export_student(conn, student_id: int) -> dict | None:
    """One student's record as JSON-ready data, or None if there is no such student."""
    cur = await conn.execute(
        "SELECT st.id, st.code, st.name, st.bound_at, st.removed, s.id, s.opened_at, c.name"
        " FROM students st JOIN lesson_sessions s ON s.id = st.lesson_session_id"
        " JOIN classes c ON c.id = s.class_id WHERE st.id = %s",
        (student_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return None
    sid, code, name, bound_at, removed, session_id, opened_at, class_name = row
    cur = await conn.execute(
        "SELECT id, model, started_at FROM conversations WHERE student_id = %s ORDER BY id", (sid,))
    conversations = []
    for conv_id, model, started_at in await cur.fetchall():
        msgs = await conn.execute(
            "SELECT id, role, text, status, created_at FROM messages"
            " WHERE conversation_id = %s ORDER BY id", (conv_id,))
        messages = []
        for msg_id, role, text, status, created_at in await msgs.fetchall():
            fl = await conn.execute(
                "SELECT category, action, created_at, reviewed_at FROM flags"
                " WHERE message_id = %s ORDER BY id", (msg_id,))
            flags = [{"category": cat, "action": act, "time": iso(t),
                      "reviewed": rev is not None} for cat, act, t, rev in await fl.fetchall()]
            messages.append({"role": role, "text": text, "status": status,
                             "time": iso(created_at), "flags": flags})
        conversations.append({"model": model, "started": iso(started_at), "messages": messages})
    return {
        "student": {"id": sid, "code": code, "name": name, "removed": removed,
                    "joined": iso(bound_at) if bound_at else None},
        "lesson_session": {"id": session_id, "opened": iso(opened_at), "class": class_name},
        "conversations": conversations,
    }


async def export_in_snapshot(conn, student_id: int) -> dict | None:
    """export_student seeing one snapshot of the database, so retention
    committing between its reads cannot drop part of the record. Must be the
    first statement of a transaction."""
    await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
    return await export_student(conn, student_id)


async def delete_student(conn, student_id: int) -> bool:
    """Delete one student; conversations, messages, flags and cookie sessions cascade."""
    cur = await conn.execute("DELETE FROM students WHERE id = %s RETURNING id", (student_id,))
    return await cur.fetchone() is not None


# Admin endpoints. Audit rows for exports and deletions name the student by
# id and lesson session only, never by code or conversation text.

AUDIT_PAGE = 100


async def get_audit(request: Request) -> JSONResponse:
    await auth.require_staff(request, "admin")
    before = request.query_params.get("before")
    try:
        before_id = int(before) if before else None
    except ValueError:
        raise auth.HTTPError(400, "bad_request") from None
    if before_id is not None and not 0 < before_id < 2**63:
        raise auth.HTTPError(400, "bad_request")
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, username, action, detail, created_at FROM audit"
            " WHERE %s::bigint IS NULL OR id < %s ORDER BY id DESC LIMIT %s",
            (before_id, before_id, AUDIT_PAGE))
        rows = await cur.fetchall()
    return JSONResponse({"audit": [
        {"id": i, "username": u, "action": a, "detail": d, "time": iso(t)} for i, u, a, d, t in rows]})


async def get_retention(request: Request) -> JSONResponse:
    await auth.require_staff(request, "admin")
    async with request.app.state.pool.connection() as conn:
        return JSONResponse({"days": await retention_days(conn)})


async def put_retention(request: Request) -> JSONResponse:
    admin = await auth.require_staff(request, "admin")
    days = (await auth.json_body(request)).get("days")
    if type(days) is not int or not 1 <= days <= MAX_RETENTION_DAYS:
        raise auth.HTTPError(400, "bad_days")
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ('retention_days', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (Jsonb(days),))
        await auth.audit(conn, admin["id"], admin["username"], "retention.set", {"days": days})
    return JSONResponse({"days": days})


async def student_identity(conn, student_id: int) -> dict | None:
    """What the audit log names a student by: id and lesson session (never the code)."""
    cur = await conn.execute(
        "SELECT id, lesson_session_id FROM students WHERE id = %s FOR UPDATE", (student_id,))
    row = await cur.fetchone()
    return {"student_id": row[0], "lesson_session_id": row[1]} if row else None


def student_ref(record: dict) -> dict:
    return {"student_id": record["student"]["id"], "lesson_session_id": record["lesson_session"]["id"]}


async def get_export(request: Request) -> Response:
    admin = await auth.require_staff(request, "admin")
    student_id = path_id(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        record = await export_in_snapshot(conn, student_id)
        if record is None:
            raise auth.HTTPError(404, "not_found")
        await auth.audit(conn, admin["id"], admin["username"], "student.export", student_ref(record))
    return Response(json.dumps(record, indent=2), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="student-{student_id}.json"',
        "Cache-Control": "no-store"})


async def delete_student_endpoint(request: Request) -> JSONResponse:
    admin = await auth.require_staff(request, "admin")
    student_id = path_id(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        ref = await student_identity(conn, student_id)
        if ref is None or not await delete_student(conn, student_id):
            raise auth.HTTPError(404, "not_found")
        await auth.audit(conn, admin["id"], admin["username"], "student.delete", ref)
    return JSONResponse({"deleted": student_id})


STUDENT_PAGE = 100


async def get_students(request: Request) -> JSONResponse:
    """Every student row, removed ones too, so any student's data can be
    exported or deleted. Newest first; q matches name or code."""
    await auth.require_staff(request, "admin")
    q = request.query_params.get("q", "")
    if len(q) > 100:
        raise auth.HTTPError(400, "bad_request")
    # Escape LIKE wildcards so q is matched as typed.
    pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT st.id, st.code, st.name, st.removed, s.id, s.opened_at, c.name,"
            "  (SELECT count(*) FROM conversations cv JOIN messages m ON m.conversation_id = cv.id"
            "   WHERE cv.student_id = st.id)"
            " FROM students st JOIN lesson_sessions s ON s.id = st.lesson_session_id"
            " JOIN classes c ON c.id = s.class_id"
            " WHERE %s = '' OR st.name ILIKE %s OR st.code LIKE %s"
            " ORDER BY st.id DESC LIMIT %s",
            (q, pattern, pattern, STUDENT_PAGE))
        rows = await cur.fetchall()
    return JSONResponse({"students": [
        {"id": i, "code": code, "name": name, "removed": removed, "lesson_session_id": sid,
         "opened": iso(opened), "class": cls, "messages": n}
        for i, code, name, removed, sid, opened, cls, n in rows]}, headers={"Cache-Control": "no-store"})


async def post_backup(request: Request) -> Response:
    """The same dump as scripts/backup.sh, written to a temporary file first so
    a failed pg_dump never reaches the browser as a plausible download."""
    admin = await auth.require_staff(request, "admin")
    # The password goes to pg_dump in its environment, not on its command line.
    params = conninfo_to_dict(request.app.state.dsn)
    password = params.pop("password", None)
    env = {**os.environ, **({"PGPASSWORD": password} if password else {})}
    fd, path = tempfile.mkstemp(prefix="backup-", suffix=".dump")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            "pg_dump", "-Fc", "-f", path, make_conninfo(**params), env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await proc.wait()
        if proc.returncode != 0 or os.path.getsize(path) == 0:
            log.error("backup failed: pg_dump exited %s", proc.returncode)
            raise auth.HTTPError(500, "backup_failed")
        async with request.app.state.pool.connection() as conn:
            await auth.audit(conn, admin["id"], admin["username"], "backup.download", {})
        # Open, then unlink at once: the open file stays readable, and nothing
        # is left on disk even if the download is cut short.
        dump = open(path, "rb")
    finally:
        os.unlink(path)
    size = os.fstat(dump.fileno()).st_size

    async def chunks():
        with dump:
            while chunk := dump.read(1 << 16):
                yield chunk

    name = f"classroom-ai-{request.app.state.clock().strftime('%Y%m%d-%H%M%S')}.dump"
    return StreamingResponse(chunks(), media_type="application/octet-stream", headers={
        "Content-Disposition": f'attachment; filename="{name}"',
        "Content-Length": str(size), "Cache-Control": "no-store"})


routes = [
    Route("/api/admin/audit", get_audit, methods=["GET"]),
    Route("/api/admin/retention", get_retention, methods=["GET"]),
    Route("/api/admin/retention", put_retention, methods=["PUT"]),
    Route("/api/admin/students/{id}/export", get_export, methods=["GET"]),
    Route("/api/admin/students/{id}", delete_student_endpoint, methods=["DELETE"]),
    Route("/api/admin/students", get_students, methods=["GET"]),
    # POST so a link on another site cannot start a dump with the admin's cookie.
    Route("/api/admin/backup", post_backup, methods=["POST"]),
]
