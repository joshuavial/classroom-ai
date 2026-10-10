"""Records: retention, and exporting or deleting one student (R7.2, R7.3).

Backup and restore are scripts/backup.sh and scripts/restore.sh.
"""

import asyncio
import json
import logging
import os
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from psycopg.types.json import Jsonb
from starlette.background import BackgroundTask
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Route

from app import auth

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
    """Delete what is older than the retention period. Returns counts."""
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s, %s)", (LOCK_NAMESPACE, RETENTION_LOCK))
        cutoff = now - timedelta(days=await retention_days(conn))
        # Flags go with their messages (ON DELETE CASCADE).
        messages = await conn.execute("DELETE FROM messages WHERE created_at < %s", (cutoff,))
        conversations = await conn.execute(
            "DELETE FROM conversations c WHERE c.started_at < %s"
            " AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.id)",
            (cutoff,),
        )
        # Students, their conversations and cookie sessions go with the session.
        sessions = await conn.execute(
            "DELETE FROM lesson_sessions s WHERE s.state = 'closed' AND s.closed_at < %s"
            " AND NOT EXISTS (SELECT 1 FROM students st JOIN conversations c ON c.student_id = st.id"
            "  JOIN messages m ON m.conversation_id = c.id WHERE st.lesson_session_id = s.id)",
            (cutoff,),
        )
    counts = {"messages": messages.rowcount, "conversations": conversations.rowcount,
              "sessions": sessions.rowcount}
    log.info("retention deleted %s", counts)
    return counts


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


async def delete_student(conn, student_id: int) -> bool:
    """Delete one student; conversations, messages, flags and cookie sessions cascade."""
    cur = await conn.execute("DELETE FROM students WHERE id = %s RETURNING id", (student_id,))
    return await cur.fetchone() is not None


# Admin endpoints. Audit rows for exports and deletions name the student by
# id, code and lesson session only, never by conversation text.

AUDIT_PAGE = 100


async def json_object(request: Request) -> dict:
    raw = await request.body()
    if len(raw) > auth.MAX_BODY:
        raise auth.HTTPError(413, "too_large")
    try:
        body = json.loads(raw or b"{}")
    except ValueError:
        raise auth.HTTPError(400, "bad_request") from None
    if not isinstance(body, dict):
        raise auth.HTTPError(400, "bad_request")
    return body


def path_id(request: Request, name: str) -> int:
    try:
        return int(request.path_params[name])
    except ValueError:
        raise auth.HTTPError(404, "not_found") from None


async def get_audit(request: Request) -> JSONResponse:
    await auth.require_staff(request, "admin")
    before = request.query_params.get("before")
    try:
        before_id = int(before) if before else None
    except ValueError:
        raise auth.HTTPError(400, "bad_request") from None
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
    days = (await json_object(request)).get("days")
    if type(days) is not int or not 1 <= days <= MAX_RETENTION_DAYS:
        raise auth.HTTPError(400, "bad_days")
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES ('retention_days', %s)"
            " ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()", (Jsonb(days),))
        await auth.audit(conn, admin["id"], admin["username"], "retention.set", {"days": days})
    return JSONResponse({"days": days})


def student_ref(record: dict) -> dict:
    return {"student_id": record["student"]["id"], "code": record["student"]["code"],
            "lesson_session_id": record["lesson_session"]["id"]}


async def get_export(request: Request) -> Response:
    admin = await auth.require_staff(request, "admin")
    student_id = path_id(request, "student_id")
    async with request.app.state.pool.connection() as conn, conn.transaction():
        record = await export_student(conn, student_id)
        if record is None:
            raise auth.HTTPError(404, "not_found")
        await auth.audit(conn, admin["id"], admin["username"], "student.export", student_ref(record))
    return Response(json.dumps(record, indent=2), media_type="application/json", headers={
        "Content-Disposition": f'attachment; filename="student-{student_id}.json"',
        "Cache-Control": "no-store"})


async def delete_student_endpoint(request: Request) -> JSONResponse:
    admin = await auth.require_staff(request, "admin")
    student_id = path_id(request, "student_id")
    async with request.app.state.pool.connection() as conn, conn.transaction():
        record = await export_student(conn, student_id)
        if record is None or not await delete_student(conn, student_id):
            raise auth.HTTPError(404, "not_found")
        await auth.audit(conn, admin["id"], admin["username"], "student.delete", student_ref(record))
    return JSONResponse({"deleted": student_id})


async def get_backup(request: Request) -> Response:
    """The same dump as scripts/backup.sh, written to a temporary file first so
    a failed pg_dump never reaches the browser as a plausible download."""
    admin = await auth.require_staff(request, "admin")
    fd, path = tempfile.mkstemp(prefix="backup-", suffix=".dump")
    os.close(fd)
    try:
        proc = await asyncio.create_subprocess_exec(
            "pg_dump", "-Fc", "-f", path, request.app.state.dsn,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
        _, err = await proc.communicate()
        if proc.returncode != 0 or os.path.getsize(path) == 0:
            log.error("backup failed: pg_dump exited %s", proc.returncode)
            raise auth.HTTPError(500, "backup_failed")
        async with request.app.state.pool.connection() as conn:
            await auth.audit(conn, admin["id"], admin["username"], "backup.download", {})
    except BaseException:
        os.unlink(path)
        raise
    name = f"classroom-ai-{request.app.state.clock().strftime('%Y%m%d-%H%M%S')}.dump"
    return FileResponse(path, media_type="application/octet-stream", filename=name,
                        headers={"Cache-Control": "no-store"}, background=BackgroundTask(os.unlink, path))


routes = [
    Route("/api/admin/audit", get_audit, methods=["GET"]),
    Route("/api/admin/retention", get_retention, methods=["GET"]),
    Route("/api/admin/retention", put_retention, methods=["PUT"]),
    Route("/api/admin/students/{student_id}/export", get_export, methods=["GET"]),
    Route("/api/admin/students/{student_id}", delete_student_endpoint, methods=["DELETE"]),
    Route("/api/admin/backup", get_backup, methods=["GET"]),
]
