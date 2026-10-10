"""Records: retention, and exporting or deleting one student (R7.2, R7.3).

Backup and restore are scripts/backup.sh and scripts/restore.sh.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta

log = logging.getLogger("app.records")

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
            flags = [{"category": cat, "action": act, "time": t.isoformat(),
                      "reviewed": rev is not None} for cat, act, t, rev in await fl.fetchall()]
            messages.append({"role": role, "text": text, "status": status,
                             "time": created_at.isoformat(), "flags": flags})
        conversations.append({"model": model, "started": started_at.isoformat(), "messages": messages})
    return {
        "student": {"id": sid, "code": code, "name": name, "removed": removed,
                    "joined": bound_at.isoformat() if bound_at else None},
        "lesson_session": {"id": session_id, "opened": opened_at.isoformat(), "class": class_name},
        "conversations": conversations,
    }


async def delete_student(conn, student_id: int) -> bool:
    """Delete one student; conversations, messages, flags and cookie sessions cascade."""
    cur = await conn.execute("DELETE FROM students WHERE id = %s RETURNING id", (student_id,))
    return await cur.fetchone() is not None
