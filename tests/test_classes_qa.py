"""QA checks for step 3: cross-teacher roster actions, stale cookie rows,
codes kept out of audit and logs, odd path ids."""

import logging

import psycopg
import pytest
from starlette.responses import JSONResponse
from starlette.routing import Route

from app.auth import require_student
from tests.helpers import make_staff, new_client, post, sign_in
from tests.test_classes import close_student_clients, join, new_lesson  # noqa: F401


@pytest.fixture
async def teacher(client, dsn):
    await sign_in(client, await make_staff(dsn, "teacher"))
    return client


async def test_other_teacher_cannot_touch_roster(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 2)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    sid = roster[0]["id"]
    async with new_client(app) as other:
        await sign_in(other, await make_staff(dsn, "teacher"))
        h = {"X-CSRF-Token": other.cookies["csrf_token"]}
        for method, path in [("PATCH", f"/api/students/{sid}"), ("POST", f"/api/students/{sid}/unbind"),
                             ("POST", f"/api/students/{sid}/remove"),
                             ("POST", f"/api/students/{roster[1]['id']}/remove")]:
            r = await other.request(method, path, json={"name": "Hacked"}, headers=h)
            assert (r.status_code, r.json()) == (404, {"error": "not_found"}), (method, path)
    me = (await student.get("/api/student/me")).json()["student"]
    assert me["name"] == "Aroha"
    assert len((await teacher.get(f"/api/sessions/{session_id}")).json()["roster"]) == 2


async def test_admin_can_run_another_teachers_lesson(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 1)
    await join(app, roster[0]["code"], "Aroha")
    async with new_client(app) as admin:
        await sign_in(admin, await make_staff(dsn, "admin"))
        assert (await post(admin, f"/api/sessions/{session_id}/codes", {"count": 1})).status_code == 201
        assert (await post(admin, f"/api/students/{roster[0]['id']}/unbind")).status_code == 200
        assert (await post(admin, f"/api/sessions/{session_id}/state", {"state": "closed"})).status_code == 200


async def test_student_cookie_refused_on_teacher_writes(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    for path, body in [(f"/api/sessions/{session_id}/state", {"state": "closed"}),
                       (f"/api/students/{roster[0]['id']}/unbind", {}),
                       (f"/api/sessions/{session_id}/codes", {"count": 1}), ("/api/classes", {"name": "x"})]:
        r = await post(student, path, body)
        assert r.status_code == 401, path
    assert (await student.get("/api/student/me")).json()["student"]["state"] == "open"


async def test_staff_and_anonymous_refused_by_require_student(app, teacher):
    async def send(request):
        await require_student(request, sending=True)
        return JSONResponse({"sent": True})

    app.router.routes.append(Route("/api/test-send-qa", send, methods=["POST"]))
    try:
        assert (await post(teacher, "/api/test-send-qa")).status_code == 401
        async with new_client(app) as anon:
            assert (await post(anon, "/api/test-send-qa")).status_code == 401
    finally:
        app.router.routes.pop()


@pytest.mark.parametrize("change", [
    "UPDATE lesson_sessions SET state = 'closed', closed_at = now() WHERE id = (SELECT lesson_session_id FROM students WHERE id = %s)",
    "UPDATE students SET removed = true WHERE id = %s",
    "UPDATE students SET name = NULL, bound_at = NULL WHERE id = %s",
])
async def test_cookie_row_left_behind_still_refused(app, teacher, dsn, change):
    _, roster = await new_lesson(teacher, 1)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    with psycopg.connect(dsn) as conn:
        conn.execute(change, (roster[0]["id"],))
    assert (await student.get("/api/student/me")).json() == {"student": None}


async def test_roster_actions_on_closed_session_are_refused(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    await join(app, roster[0]["code"], "Aroha")
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    for path in ["unbind", "remove"]:
        r = await post(teacher, f"/api/students/{roster[0]['id']}/{path}")
        assert (r.status_code, r.json()) == (409, {"error": "not_live"})


@pytest.mark.parametrize("raw", ["99999999999999999999", "-1", "abc"])
async def test_odd_path_ids_are_404(teacher, raw):
    assert (await teacher.get(f"/api/sessions/{raw}")).status_code == 404
    assert (await post(teacher, f"/api/students/{raw}/remove")).status_code == 404


async def test_codes_stay_out_of_audit_and_logs(app, teacher, dsn, caplog):
    caplog.set_level(logging.DEBUG)
    session_id, roster = await new_lesson(teacher, 3)
    await join(app, roster[0]["code"], "Aroha")
    await join(app, roster[0]["code"], "Bob")
    await join(app, "000000" if roster[0]["code"] != "000000" else "000001", "Eve")
    more = (await post(teacher, f"/api/sessions/{session_id}/codes", {"count": 2})).json()["codes"]
    await post(teacher, f"/api/students/{roster[0]['id']}/unbind")
    await post(teacher, f"/api/students/{roster[1]['id']}/remove")
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    codes = [r["code"] for r in roster] + more
    with psycopg.connect(dsn) as conn:
        details = [r[0] for r in conn.execute("SELECT detail::text FROM audit").fetchall()]
    for code in codes:
        assert not any(code in d for d in details), code
        assert code not in caplog.text, code
