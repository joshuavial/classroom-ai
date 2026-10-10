"""Classes, lesson sessions, student codes and the roster (step 3).

Teachers see and change only their own classes; an admin may act on any.
A class or session the caller may not see answers 404, so its existence is
not confirmed. See docs/architecture.md "Identity and access".
"""

import secrets

import psycopg
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth import HTTPError, audit, json_body, require_staff
from app.students import clean_student_name, has_bad_characters

CODES_LOCK_KEY = 7_311_007
DEFAULT_CODES, MAX_CODES_PER_CALL, MAX_LIVE_CODES = 30, 200, 500
STATES = ("open", "paused", "closed")


def text_field(body: dict, name: str, low: int, high: int) -> str:
    """A trimmed one-line field; control and unassigned characters refused."""
    value = body.get(name)
    if not isinstance(value, str):
        raise HTTPError(400, f"bad_{name}")
    value = value.strip()
    if not low <= len(value) <= high or has_bad_characters(value):
        raise HTTPError(400, f"bad_{name}")
    return value


def class_fields(body: dict) -> tuple[str, str, int | None]:
    name = text_field(body, "name", 1, 100)
    instructions = body.get("instructions", "")
    if (
        not isinstance(instructions, str)
        or len(instructions) > 10_000
        or has_bad_characters(instructions.replace("\n", "").replace("\t", ""))
    ):
        raise HTTPError(400, "bad_instructions")
    limit = body.get("message_limit")
    if limit is not None and (type(limit) is not int or not 1 <= limit <= 1000):
        raise HTTPError(400, "bad_message_limit")
    return name, instructions, limit


def count_field(body: dict) -> int:
    count = body.get("count", DEFAULT_CODES)
    if type(count) is not int or not 1 <= count <= MAX_CODES_PER_CALL:
        raise HTTPError(400, "bad_count")
    return count


def path_id(request: Request) -> int:
    try:
        value = int(request.path_params["id"])
    except ValueError:
        raise HTTPError(404, "not_found") from None
    if not 0 < value < 2**63:
        raise HTTPError(404, "not_found")
    return value


# Ownership: the SQL condition a class must meet for this staff member.

def owned(staff: dict, alias: str = "c") -> tuple[str, tuple]:
    if staff["role"] == "admin":
        return "TRUE", ()
    return f"{alias}.teacher_id = %s", (staff["id"],)


async def owned_class(conn, staff: dict, class_id: int) -> dict:
    cond, args = owned(staff)
    cur = await conn.execute(
        f"SELECT c.id, c.name, c.instructions, c.message_limit FROM classes c WHERE c.id = %s AND {cond}",
        (class_id, *args),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPError(404, "not_found")
    return {"id": row[0], "name": row[1], "instructions": row[2], "message_limit": row[3]}


async def owned_session(conn, staff: dict, session_id: int) -> dict:
    cond, args = owned(staff)
    cur = await conn.execute(
        "SELECT l.id, l.state, l.opened_at, l.closed_at, c.id, c.name FROM lesson_sessions l"
        f" JOIN classes c ON c.id = l.class_id WHERE l.id = %s AND {cond}",
        (session_id, *args),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPError(404, "not_found")
    return {"id": row[0], "state": row[1], "opened_at": row[2], "closed_at": row[3],
            "class_id": row[4], "class_name": row[5]}


async def generate_codes(conn, session_id: int, count: int) -> list[str]:
    """Add `count` unbound students with fresh codes to a live session.

    Codes are unique among students that are not removed in sessions that are
    not closed, and never repeat a code this session retired. Every code generator takes the same advisory lock, so two
    sessions can never be handed the same live code.
    """
    await conn.execute("SELECT pg_advisory_xact_lock(%s)", (CODES_LOCK_KEY,))
    cur = await conn.execute(
        "SELECT count(*) FROM students WHERE lesson_session_id = %s AND NOT removed", (session_id,)
    )
    if (await cur.fetchone())[0] + count > MAX_LIVE_CODES:
        raise HTTPError(409, "too_many_codes")
    # Taken: every live code, and every code this session ever used, so a
    # retired code (a lost slip) is never handed out again in its session.
    cur = await conn.execute(
        "SELECT st.code FROM students st JOIN lesson_sessions l ON l.id = st.lesson_session_id"
        " WHERE (NOT st.removed AND l.state <> 'closed') OR st.lesson_session_id = %s",
        (session_id,),
    )
    taken = {row[0] for row in await cur.fetchall()}
    codes: list[str] = []
    for _ in range(count * 1000):
        code = f"{secrets.randbelow(10**6):06d}"
        if code not in taken:
            taken.add(code)
            codes.append(code)
            if len(codes) == count:
                break
    else:
        # Only reachable if nearly every six-digit code is live at once.
        raise HTTPError(503, "no_free_codes")
    async with conn.cursor() as cur:
        await cur.executemany(
            "INSERT INTO students (lesson_session_id, code) VALUES (%s, %s)", [(session_id, c) for c in codes]
        )
    return codes


def iso(value) -> str | None:
    return value.isoformat() if value is not None else None


# Classes.

async def list_classes(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    cond, args = owned(staff)
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT c.id, c.name, c.instructions, c.message_limit, l.id, l.state, t.username FROM classes c"
            " JOIN staff t ON t.id = c.teacher_id"
            " LEFT JOIN lesson_sessions l ON l.class_id = c.id AND l.state <> 'closed'"
            f" WHERE {cond} ORDER BY c.name, c.id",
            args,
        )
        rows = await cur.fetchall()
    return JSONResponse({"classes": [
        {"id": r[0], "name": r[1], "instructions": r[2], "message_limit": r[3],
         "live_session": None if r[4] is None else {"id": r[4], "state": r[5]}, "teacher": r[6]}
        for r in rows
    ]})


async def create_class(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    name, instructions, limit = class_fields(await json_body(request))
    async with request.app.state.pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO classes (teacher_id, name, instructions, message_limit) VALUES (%s, %s, %s, %s)"
            " RETURNING id",
            (staff["id"], name, instructions, limit),
        )
        class_id = (await cur.fetchone())[0]
        await audit(conn, staff["id"], staff["username"], "class.create", {"class_id": class_id, "name": name})
    return JSONResponse({"id": class_id, "name": name, "instructions": instructions, "message_limit": limit},
                        status_code=201)


async def edit_class(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    class_id = path_id(request)
    name, instructions, limit = class_fields(await json_body(request))
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await owned_class(conn, staff, class_id)
        await conn.execute(
            "UPDATE classes SET name = %s, instructions = %s, message_limit = %s WHERE id = %s",
            (name, instructions, limit, class_id),
        )
        await audit(conn, staff["id"], staff["username"], "class.edit", {"class_id": class_id, "name": name})
    return JSONResponse({"id": class_id, "name": name, "instructions": instructions, "message_limit": limit})


# Lesson sessions.

async def start_lesson(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    class_id = path_id(request)
    count = count_field(await json_body(request))
    async with request.app.state.pool.connection() as conn:
        try:
            async with conn.transaction():
                await owned_class(conn, staff, class_id)
                cur = await conn.execute(
                    "INSERT INTO lesson_sessions (class_id) VALUES (%s) RETURNING id", (class_id,)
                )
                session_id = (await cur.fetchone())[0]
                await generate_codes(conn, session_id, count)
                await audit(conn, staff["id"], staff["username"], "session.start",
                            {"class_id": class_id, "session_id": session_id, "codes": count})
        except psycopg.errors.UniqueViolation:
            raise HTTPError(409, "live_session") from None
    return JSONResponse({"id": session_id, "state": "open"}, status_code=201)


async def get_session(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    session_id = path_id(request)
    async with request.app.state.pool.connection() as conn:
        session = await owned_session(conn, staff, session_id)
        cur = await conn.execute(
            "SELECT id, code, name, bound_at FROM students WHERE lesson_session_id = %s AND NOT removed"
            " ORDER BY id",
            (session_id,),
        )
        roster = [{"id": r[0], "code": r[1], "name": r[2], "bound_at": iso(r[3])} for r in await cur.fetchall()]
    return JSONResponse({
        "id": session["id"], "state": session["state"], "class_id": session["class_id"],
        "class_name": session["class_name"], "opened_at": iso(session["opened_at"]),
        "closed_at": iso(session["closed_at"]), "roster": roster,
    })


async def set_state(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    session_id = path_id(request)
    state = (await json_body(request)).get("state")
    if state not in STATES:
        raise HTTPError(400, "bad_state")
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await owned_session(conn, staff, session_id)
        cur = await conn.execute(
            "UPDATE lesson_sessions SET state = %s,"
            " closed_at = CASE WHEN %s = 'closed' THEN now() ELSE NULL END"
            " WHERE id = %s AND state <> 'closed' RETURNING state",
            (state, state, session_id),
        )
        if await cur.fetchone() is None:
            raise HTTPError(409, "closed")
        if state == "closed":
            await conn.execute(
                "DELETE FROM auth_sessions WHERE student_id IN"
                " (SELECT id FROM students WHERE lesson_session_id = %s)",
                (session_id,),
            )
        await audit(conn, staff["id"], staff["username"], f"session.{state}", {"session_id": session_id})
    return JSONResponse({"id": session_id, "state": state})


async def more_codes(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    session_id = path_id(request)
    count = count_field(await json_body(request))
    async with request.app.state.pool.connection() as conn, conn.transaction():
        session = await owned_session(conn, staff, session_id)
        if session["state"] == "closed":
            raise HTTPError(409, "closed")
        # Lock the session row so a concurrent close waits for this to finish.
        cur = await conn.execute(
            "SELECT 1 FROM lesson_sessions WHERE id = %s AND state <> 'closed' FOR UPDATE", (session_id,)
        )
        if await cur.fetchone() is None:
            raise HTTPError(409, "closed")
        codes = await generate_codes(conn, session_id, count)
        await audit(conn, staff["id"], staff["username"], "session.codes",
                    {"session_id": session_id, "codes": count})
    return JSONResponse({"codes": codes}, status_code=201)


# Students on the roster.

async def owned_student(conn, staff: dict, student_id: int) -> dict:
    """A student of a class this staff member may see (else 404), still on
    the roster of a live session (else 409), locked for this transaction."""
    cond, args = owned(staff)
    cur = await conn.execute(
        "SELECT st.id, st.code, st.name, st.lesson_session_id, st.removed OR l.state = 'closed'"
        " FROM students st JOIN lesson_sessions l ON l.id = st.lesson_session_id"
        f" JOIN classes c ON c.id = l.class_id WHERE st.id = %s AND {cond} FOR UPDATE OF st, l",
        (student_id, *args),
    )
    row = await cur.fetchone()
    if row is None:
        raise HTTPError(404, "not_found")
    if row[4]:
        raise HTTPError(409, "not_live")
    return {"id": row[0], "code": row[1], "name": row[2], "lesson_session_id": row[3]}


async def rename_student(request: Request) -> JSONResponse:
    staff = await require_staff(request)
    student_id = path_id(request)
    name = clean_student_name((await json_body(request)).get("name"))
    async with request.app.state.pool.connection() as conn, conn.transaction():
        student = await owned_student(conn, staff, student_id)
        if student["name"] is None:
            raise HTTPError(409, "not_bound")
        await conn.execute("UPDATE students SET name = %s WHERE id = %s", (name, student_id))
        await audit(conn, staff["id"], staff["username"], "student.rename",
                    {"student_id": student_id, "from": student["name"], "to": name})
    return JSONResponse({"id": student_id, "name": name})


async def end_student_sessions(conn, student_id: int) -> None:
    await conn.execute("DELETE FROM auth_sessions WHERE student_id = %s", (student_id,))


async def unbind_student(request: Request) -> JSONResponse:
    """Retire this student row (keeping its conversations under the old name)
    and put a fresh unbound row with the same code on the roster."""
    staff = await require_staff(request)
    student_id = path_id(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        student = await owned_student(conn, staff, student_id)
        if student["name"] is None:
            raise HTTPError(409, "not_bound")
        await conn.execute("UPDATE students SET removed = true WHERE id = %s", (student_id,))
        cur = await conn.execute(
            "INSERT INTO students (lesson_session_id, code) VALUES (%s, %s) RETURNING id",
            (student["lesson_session_id"], student["code"]),
        )
        fresh = (await cur.fetchone())[0]
        await end_student_sessions(conn, student_id)
        await audit(conn, staff["id"], staff["username"], "student.unbind",
                    {"student_id": student_id, "name": student["name"]})
    return JSONResponse({"id": fresh, "code": student["code"], "name": None})


async def remove_student(request: Request) -> JSONResponse:
    """Retire this student row and its code."""
    staff = await require_staff(request)
    student_id = path_id(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        student = await owned_student(conn, staff, student_id)
        await conn.execute("UPDATE students SET removed = true WHERE id = %s", (student_id,))
        await end_student_sessions(conn, student_id)
        await audit(conn, staff["id"], staff["username"], "student.remove",
                    {"student_id": student_id, "name": student["name"]})
    return JSONResponse({"id": student_id, "removed": True})


routes = [
    Route("/api/classes", list_classes, methods=["GET"]),
    Route("/api/classes", create_class, methods=["POST"]),
    Route("/api/classes/{id}", edit_class, methods=["PATCH"]),
    Route("/api/classes/{id}/sessions", start_lesson, methods=["POST"]),
    Route("/api/sessions/{id}", get_session, methods=["GET"]),
    Route("/api/sessions/{id}/state", set_state, methods=["POST"]),
    Route("/api/sessions/{id}/codes", more_codes, methods=["POST"]),
    Route("/api/students/{id}", rename_student, methods=["PATCH"]),
    Route("/api/students/{id}/unbind", unbind_student, methods=["POST"]),
    Route("/api/students/{id}/remove", remove_student, methods=["POST"]),
]
