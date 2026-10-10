"""Students joining a lesson with a code from their slip (step 3, PRD D1).

The first use of a code binds it to the name typed. Entering a bound code
again, on any device, signs in as that student under the bound name. See
docs/architecture.md "Identity and access".
"""

import logging
import re
import unicodedata

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth import (
    STUDENT_COLUMNS,
    STUDENT_LIVE,
    HTTPError,
    check_session_csrf,
    client_ip,
    json_body,
    load_session,
    start_session,
    student_from_row,
)

log = logging.getLogger("app.students")
CODE = re.compile(r"^[0-9]{6}$")


def has_bad_characters(text: str) -> bool:
    """Control, surrogate, private-use or unassigned characters. Format
    characters (Cf) are allowed: names in some scripts need joiners."""
    return any(unicodedata.category(ch) in ("Cc", "Cs", "Co", "Cn") for ch in text)


def clean_student_name(value) -> str:
    """Trimmed, inner whitespace collapsed, no control characters, 1-60 long."""
    if not isinstance(value, str):
        raise HTTPError(400, "bad_name")
    name = " ".join(value.split())
    if not 1 <= len(name) <= 60 or has_bad_characters(name):
        raise HTTPError(400, "bad_name")
    return name


def public(student: dict | None) -> dict:
    if student is None or student["kind"] != "student":
        return {"student": None}
    return {"student": {k: student[k] for k in ("name", "code", "class_name", "state", "message_limit")}}


async def find_by_code(conn, code: str) -> list[tuple]:
    """Live student rows (not removed, session not closed) holding this code.

    Locks the student row and holds a share lock on its lesson session, so a
    close, unbind or remove (which lock those rows for update) either finishes
    first, and this sees the row gone, or waits until this join has committed
    and then deletes the cookie it created.
    """
    cur = await conn.execute(
        f"SELECT {STUDENT_COLUMNS} FROM students st"
        " JOIN lesson_sessions l ON l.id = st.lesson_session_id JOIN classes c ON c.id = l.class_id"
        " WHERE st.code = %s AND NOT st.removed AND l.state <> 'closed' FOR UPDATE OF st FOR SHARE OF l",
        (code,),
    )
    return await cur.fetchall()


async def join(request: Request) -> JSONResponse:
    # Every attempt counts when admitted, so a burst cannot get past the limit,
    # and a successful join is forgiven at the end: only wrong codes use up
    # the limit, so a class behind one address is not refused.
    ip = client_ip(request)
    limiter = request.app.state.join_limiter
    # A looser cap on every attempt, so one valid code cannot be used to
    # create cookie sessions without limit.
    if not request.app.state.join_total_limiter.allow(ip):
        raise HTTPError(429, "too_many_attempts")
    attempt = limiter.admit(ip)
    if attempt is None:
        raise HTTPError(429, "too_many_attempts")
    body = await json_body(request)
    code = body.get("code")
    code = re.sub(r"\s", "", code) if isinstance(code, str) else ""
    name = clean_student_name(body.get("name"))
    if not CODE.match(code):
        raise HTTPError(404, "bad_code")
    # Load any session this device has before taking a connection, so the
    # CSRF check in start_session is a cache hit (one connection per request),
    # and a planted cookie is refused before any row is locked.
    existing = await load_session(request)
    if existing is not None:
        check_session_csrf(request, existing)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        rows = await find_by_code(conn, code)
        if len(rows) > 1:
            log.error("live code held by several students: %s", [r[0] for r in rows])
            raise HTTPError(500, "code_conflict")
        if not rows:
            raise HTTPError(404, "bad_code")
        student = student_from_row(rows[0])
        if student["name"] is None:
            # The row is locked FOR UPDATE above, so no other device binds it
            # in between; a device waiting on the lock sees it bound.
            await conn.execute(
                "UPDATE students SET name = %s, bound_at = now() WHERE id = %s", (name, student["id"])
            )
            student["name"] = name
        response = JSONResponse(public(student))
        await start_session(conn, request, response, student_id=student["id"])
    limiter.forgive(ip, attempt)
    return response


async def me(request: Request) -> JSONResponse:
    return JSONResponse(public(await load_session(request)))


routes = [
    Route("/api/join", join, methods=["POST"]),
    Route("/api/student/me", me, methods=["GET"]),
]
