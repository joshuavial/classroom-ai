"""Teacher console reads: student cards, usage summary, transcripts, and
marking flags reviewed (step 7). Teachers see only their own classes; an
admin may see any. See docs/architecture.md "Teacher console".
"""

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth import HTTPError, audit, require_staff
from app.classes import iso, owned, owned_session, path_id
from app.live import publish

ACTIVE_MINUTES = 5


async def students(request: Request) -> JSONResponse:
    """One card per bound student on the roster."""
    staff = await require_staff(request)
    session_id = path_id(request)
    async with request.app.state.pool.connection() as conn:
        await owned_session(conn, staff, session_id)
        cur = await conn.execute(
            """
            SELECT st.id, st.name, st.code,
                   (SELECT count(*) FROM messages m JOIN conversations c ON c.id = m.conversation_id
                     WHERE c.student_id = st.id AND m.role = 'user'),
                   latest.role, latest.text, latest.status, latest.created_at,
                   (SELECT count(*) FROM flags f JOIN messages m ON m.id = f.message_id
                     JOIN conversations c ON c.id = m.conversation_id
                     WHERE c.student_id = st.id AND f.reviewed_at IS NULL),
                   GREATEST(st.bound_at, latest.created_at)
              FROM students st
              LEFT JOIN LATERAL (
                SELECT m.role, m.text, m.status, m.created_at FROM messages m
                  JOIN conversations c ON c.id = m.conversation_id
                 WHERE c.student_id = st.id ORDER BY m.id DESC LIMIT 1) latest ON TRUE
             WHERE st.lesson_session_id = %s AND NOT st.removed AND st.name IS NOT NULL
             ORDER BY st.name, st.id
            """,
            (session_id,),
        )
        rows = await cur.fetchall()
    return JSONResponse({"students": [
        {
            "id": r[0], "name": r[1], "code": r[2], "messages": r[3],
            "latest": None if r[4] is None else {"role": r[4], "text": r[5], "status": r[6], "at": iso(r[7])},
            "unreviewed_flags": r[8], "last_active": iso(r[9]),
        }
        for r in rows
    ]})


async def usage(request: Request) -> JSONResponse:
    """The D1 usage summary for one lesson session."""
    staff = await require_staff(request)
    session_id = path_id(request)
    async with request.app.state.pool.connection() as conn:
        await owned_session(conn, staff, session_id)
        base = (" FROM messages m JOIN conversations c ON c.id = m.conversation_id"
                " JOIN students st ON st.id = c.student_id WHERE st.lesson_session_id = %s")
        cur = await conn.execute(
            f"SELECT count(DISTINCT st.id){base} AND m.role = 'user'"
            " AND m.created_at > now() - make_interval(mins => %s)",
            (session_id, ACTIVE_MINUTES),
        )
        active = (await cur.fetchone())[0]
        cur = await conn.execute(
            f"SELECT st.id, st.name, count(*){base} AND m.role = 'user' GROUP BY st.id, st.name ORDER BY st.name",
            (session_id,),
        )
        per_student = [{"id": r[0], "name": r[1], "messages": r[2]} for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT c.model, count(*) FROM conversations c JOIN students st ON st.id = c.student_id"
            " WHERE st.lesson_session_id = %s GROUP BY c.model ORDER BY count(*) DESC, c.model",
            (session_id,),
        )
        models = [{"model": r[0], "conversations": r[1]} for r in await cur.fetchall()]
        cur = await conn.execute(
            "SELECT f.category, count(*) FROM flags f JOIN messages m ON m.id = f.message_id"
            " JOIN conversations c ON c.id = m.conversation_id JOIN students st ON st.id = c.student_id"
            " WHERE st.lesson_session_id = %s GROUP BY f.category ORDER BY count(*) DESC, f.category",
            (session_id,),
        )
        flags = [{"category": r[0], "flags": r[1]} for r in await cur.fetchall()]
    return JSONResponse({
        "active_students": active,
        "total_messages": sum(s["messages"] for s in per_student),
        "per_student": per_student,
        "models": models,
        "flags_by_category": flags,
    })


async def transcript(request: Request) -> JSONResponse:
    """Everything one student wrote and was sent, blocked messages and flag
    reasons included. Readable after the student is removed or the lesson
    closed."""
    staff = await require_staff(request)
    student_id = path_id(request)
    cond, args = owned(staff)
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT st.id, st.name, st.code, st.removed, st.lesson_session_id FROM students st"
            " JOIN lesson_sessions l ON l.id = st.lesson_session_id JOIN classes c ON c.id = l.class_id"
            f" WHERE st.id = %s AND {cond}",
            (student_id, *args),
        )
        student = await cur.fetchone()
        if student is None:
            raise HTTPError(404, "not_found")
        cur = await conn.execute(
            "SELECT c.id, c.model, c.started_at, m.id, m.role, m.text, m.status, m.created_at"
            " FROM conversations c LEFT JOIN messages m ON m.conversation_id = c.id"
            " WHERE c.student_id = %s ORDER BY c.id, m.id",
            (student_id,),
        )
        rows = await cur.fetchall()
        cur = await conn.execute(
            "SELECT f.id, f.message_id, f.category, f.action, f.created_at, s.username, f.reviewed_at"
            " FROM flags f JOIN messages m ON m.id = f.message_id"
            " JOIN conversations c ON c.id = m.conversation_id LEFT JOIN staff s ON s.id = f.reviewed_by"
            " WHERE c.student_id = %s ORDER BY f.id",
            (student_id,),
        )
        flags: dict[int, list] = {}
        for f in await cur.fetchall():
            flags.setdefault(f[1], []).append({
                "id": f[0], "category": f[2], "action": f[3], "at": iso(f[4]),
                "reviewed_by": f[5], "reviewed_at": iso(f[6]),
            })
    conversations: dict[int, dict] = {}
    for r in rows:
        convo = conversations.setdefault(r[0], {"id": r[0], "model": r[1], "started_at": iso(r[2]), "messages": []})
        if r[3] is not None:
            convo["messages"].append({
                "id": r[3], "role": r[4], "text": r[5], "status": r[6], "at": iso(r[7]),
                "flags": flags.get(r[3], []),
            })
    return JSONResponse({
        "student": {"id": student[0], "name": student[1], "code": student[2], "removed": student[3]},
        "conversations": list(conversations.values()),
    })


async def review_flag(request: Request) -> JSONResponse:
    """Mark a flag reviewed by this staff member (R6.7). Once only."""
    staff = await require_staff(request)
    flag_id = path_id(request)
    cond, args = owned(staff)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT f.id, f.message_id, st.lesson_session_id, st.id FROM flags f"
            " JOIN messages m ON m.id = f.message_id"
            " JOIN conversations cv ON cv.id = m.conversation_id JOIN students st ON st.id = cv.student_id"
            " JOIN lesson_sessions l ON l.id = st.lesson_session_id JOIN classes c ON c.id = l.class_id"
            f" WHERE f.id = %s AND {cond} FOR UPDATE OF f",
            (flag_id, *args),
        )
        found = await cur.fetchone()
        if found is None:
            raise HTTPError(404, "not_found")
        cur = await conn.execute(
            "UPDATE flags SET reviewed_by = %s, reviewed_at = now() WHERE id = %s AND reviewed_at IS NULL"
            " RETURNING reviewed_at",
            (staff["id"], flag_id),
        )
        row = await cur.fetchone()
        if row is None:
            raise HTTPError(409, "already_reviewed")
        await audit(conn, staff["id"], staff["username"], "flag.review",
                    {"flag_id": flag_id, "message_id": found[1]})
        await publish(conn, found[2], "flag", flag=flag_id, student=found[3])
    return JSONResponse({"id": flag_id, "reviewed_by": staff["username"], "reviewed_at": iso(row[0])})


routes = [
    Route("/api/sessions/{id}/students", students, methods=["GET"]),
    Route("/api/sessions/{id}/usage", usage, methods=["GET"]),
    Route("/api/students/{id}/transcript", transcript, methods=["GET"]),
    Route("/api/flags/{id}/review", review_flag, methods=["POST"]),
]
