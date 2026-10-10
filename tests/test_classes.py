"""Classes, lesson sessions, codes and joining (step 3)."""

import asyncio
import itertools

import psycopg
import pytest

from app import auth, classes
from tests.helpers import make_staff, new_client, post, sign_in


@pytest.fixture
async def teacher(client, dsn):
    await sign_in(client, await make_staff(dsn, "teacher"))
    return client


async def new_class(client, **fields) -> int:
    body = {"name": "Year 9 Science", "instructions": "Guide, don't answer.", "message_limit": 20, **fields}
    response = await post(client, "/api/classes", body)
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def new_lesson(client, count: int = 30) -> tuple[int, list[dict]]:
    class_id = await new_class(client)
    response = await post(client, f"/api/classes/{class_id}/sessions", {"count": count})
    assert response.status_code == 201, response.text
    session_id = response.json()["id"]
    roster = (await client.get(f"/api/sessions/{session_id}")).json()["roster"]
    return session_id, roster


_opened = []


@pytest.fixture(autouse=True)
async def close_student_clients():
    yield
    while _opened:
        await _opened.pop().aclose()


async def join(app, code: str, name: str):
    """A new device joining with this code. Closed after the test."""
    student = new_client(app)
    _opened.append(student)
    response = await post(student, "/api/join", {"code": code, "name": name})
    return student, response


# Classes.


async def test_teacher_creates_and_edits_a_class(teacher):
    class_id = await new_class(teacher)
    listed = (await teacher.get("/api/classes")).json()["classes"]
    assert [c for c in listed if c["id"] == class_id][0]["message_limit"] == 20
    body = {"name": "Year 10", "instructions": "Be brief.", "message_limit": None}
    response = await teacher.patch(f"/api/classes/{class_id}", json=body, headers={"X-CSRF-Token": teacher.cookies["csrf_token"]})
    assert response.json() == {"id": class_id, **body}


@pytest.mark.parametrize("fields,error", [
    ({"name": ""}, "bad_name"),
    ({"message_limit": 0}, "bad_message_limit"),
    ({"message_limit": "20"}, "bad_message_limit"),
    ({"instructions": "x" * 10_001}, "bad_instructions"),
])
async def test_class_fields_are_validated(teacher, fields, error):
    body = {"name": "A", "instructions": "", "message_limit": None, **fields}
    response = await post(teacher, "/api/classes", body)
    assert (response.status_code, response.json()) == (400, {"error": error})


async def test_teachers_only_see_their_own_classes_and_admins_see_all(app, teacher, dsn):
    class_id = await new_class(teacher)
    session_id = (await post(teacher, f"/api/classes/{class_id}/sessions")).json()["id"]
    async with new_client(app) as other:
        await sign_in(other, await make_staff(dsn, "teacher"))
        assert class_id not in [c["id"] for c in (await other.get("/api/classes")).json()["classes"]]
        for method, path in [("PATCH", f"/api/classes/{class_id}"), ("POST", f"/api/classes/{class_id}/sessions"),
                             ("GET", f"/api/sessions/{session_id}"), ("POST", f"/api/sessions/{session_id}/state"),
                             ("POST", f"/api/sessions/{session_id}/codes")]:
            r = await other.request(method, path, json={"name": "x", "state": "paused"},
                                    headers={"X-CSRF-Token": other.cookies["csrf_token"]})
            assert r.status_code == 404, (method, path, r.status_code)
    async with new_client(app) as admin:
        await sign_in(admin, await make_staff(dsn, "admin"))
        assert (await admin.get(f"/api/sessions/{session_id}")).status_code == 200


async def test_teacher_endpoints_refuse_anonymous_and_students(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    async with new_client(app) as anon:
        assert (await anon.get("/api/classes")).status_code == 401
        assert (await post(anon, "/api/classes", {"name": "x"})).status_code == 401
    student, _ = await join(app, roster[0]["code"], "Aroha")
    assert (await student.get(f"/api/sessions/{session_id}")).status_code == 401
    assert (await student.get("/api/me")).json() == {"staff": None}


# Lesson sessions and codes.


async def test_start_lesson_makes_30_codes_by_default_and_one_live_session(teacher):
    class_id = await new_class(teacher)
    response = await post(teacher, f"/api/classes/{class_id}/sessions")
    assert response.status_code == 201
    roster = (await teacher.get(f"/api/sessions/{response.json()['id']}")).json()["roster"]
    assert len(roster) == 30 and all(len(r["code"]) == 6 and r["name"] is None for r in roster)
    again = await post(teacher, f"/api/classes/{class_id}/sessions")
    assert (again.status_code, again.json()) == (409, {"error": "live_session"})


async def test_codes_are_unique_among_open_sessions(teacher, monkeypatch):
    # Force collisions: the random source cycles through only 40 values.
    values = itertools.cycle(range(100_000, 100_040))
    monkeypatch.setattr(classes.secrets, "randbelow", lambda n: next(values))
    a, roster_a = await new_lesson(teacher, 20)
    b, roster_b = await new_lesson(teacher, 20)
    codes = [r["code"] for r in roster_a + roster_b]
    assert len(codes) == len(set(codes)) == 40
    # Once a session is closed, its codes may be handed out again.
    await post(teacher, f"/api/sessions/{a}/state", {"state": "closed"})
    _, roster_c = await new_lesson(teacher, 20)
    assert {r["code"] for r in roster_c} == {r["code"] for r in roster_a}


async def test_concurrent_code_generation_never_duplicates(app, teacher, monkeypatch):
    values = itertools.cycle(range(200_000, 200_100))
    monkeypatch.setattr(classes.secrets, "randbelow", lambda n: next(values))
    ids = [await new_class(teacher) for _ in range(4)]
    results = await asyncio.gather(*(post(teacher, f"/api/classes/{i}/sessions", {"count": 25}) for i in ids))
    assert all(r.status_code == 201 for r in results)
    codes = []
    for r in results:
        codes += [s["code"] for s in (await teacher.get(f"/api/sessions/{r.json()['id']}")).json()["roster"]]
    assert len(codes) == len(set(codes)) == 100


async def test_generate_more_codes(teacher):
    session_id, _ = await new_lesson(teacher, 5)
    response = await post(teacher, f"/api/sessions/{session_id}/codes", {"count": 3})
    assert response.status_code == 201 and len(response.json()["codes"]) == 3
    assert len((await teacher.get(f"/api/sessions/{session_id}")).json()["roster"]) == 8
    assert (await post(teacher, f"/api/sessions/{session_id}/codes", {"count": 201})).status_code == 400


async def test_closed_is_final(teacher):
    session_id, _ = await new_lesson(teacher, 1)
    assert (await post(teacher, f"/api/sessions/{session_id}/state", {"state": "paused"})).json()["state"] == "paused"
    assert (await post(teacher, f"/api/sessions/{session_id}/state", {"state": "open"})).json()["state"] == "open"
    assert (await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})).status_code == 200
    reopen = await post(teacher, f"/api/sessions/{session_id}/state", {"state": "open"})
    assert (reopen.status_code, reopen.json()) == (409, {"error": "closed"})
    assert (await post(teacher, f"/api/sessions/{session_id}/codes", {"count": 1})).status_code == 409


# Joining.


async def test_first_use_binds_and_a_second_name_gets_the_bound_one(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    code = roster[0]["code"]
    first, r1 = await join(app, code, "Aroha")
    second, r2 = await join(app, code, "Bob")
    assert r1.json()["student"]["name"] == r2.json()["student"]["name"] == "Aroha"
    # Resume on a second device: same student, its own cookie.
    assert first.cookies["session"] != second.cookies["session"]
    me = (await second.get("/api/student/me")).json()["student"]
    assert me == {"name": "Aroha", "code": code, "class_name": "Year 9 Science", "state": "open",
                  "message_limit": 20}
    roster = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"]
    assert roster[0]["name"] == "Aroha" and roster[0]["bound_at"]


async def test_concurrent_binds_give_one_name(app, teacher):
    _, roster = await new_lesson(teacher, 1)
    pairs = await asyncio.gather(*(join(app, roster[0]["code"], n) for n in ["Ana", "Ben", "Cai", "Dee"]))
    names = {r.json()["student"]["name"] for _, r in pairs}
    assert len(names) == 1


async def test_bad_codes_all_look_the_same(app, teacher):
    session_id, roster = await new_lesson(teacher, 3)
    removed, closed_code = roster[0], None
    await post(teacher, f"/api/students/{removed['id']}/remove")
    other_session, other_roster = await new_lesson(teacher, 1)
    closed_code = other_roster[0]["code"]
    await post(teacher, f"/api/sessions/{other_session}/state", {"state": "closed"})
    for code in [removed["code"], closed_code, "000000" if "000000" not in [r["code"] for r in roster] else "000001", "12ab56"]:
        c, r = await join(app, code, "Eve")
        assert (r.status_code, r.json()) == (404, {"error": "bad_code"}), code


@pytest.mark.parametrize("name", ["", "   ", "x" * 61, "a\u0007b"])
async def test_names_are_cleaned_and_bounded(app, teacher, name):
    _, roster = await new_lesson(teacher, 1)
    c, r = await join(app, roster[0]["code"], name)
    assert (r.status_code, r.json()) == (400, {"error": "bad_name"})


async def test_name_whitespace_is_collapsed(app, teacher):
    _, roster = await new_lesson(teacher, 1)
    c, r = await join(app, roster[0]["code"], "  Te   Ao  ")
    assert r.json()["student"]["name"] == "Te Ao"


async def test_join_rate_limit(app, teacher):
    async with new_client(app) as guesser:
        statuses = [(await post(guesser, "/api/join", {"code": f"{i:06d}", "name": "x"})).status_code
                    for i in range(31)]
    assert statuses[:30].count(429) == 0 and statuses[30] == 429


async def test_a_class_behind_one_address_all_joins(app, teacher):
    """Successful joins do not use up the limit, so 40 students sharing one
    NAT address all get in within the minute."""
    _, roster = await new_lesson(teacher, 40)
    for i, entry in enumerate(roster):
        _, r = await join(app, entry["code"], f"Student {i}")
        assert r.status_code == 200, i


async def test_successful_joins_have_a_looser_cap(app, teacher):
    _, roster = await new_lesson(teacher, 1)
    app.state.join_total_limiter.limit = 5
    try:
        statuses = [(await join(app, roster[0]["code"], "Aroha"))[1].status_code for _ in range(6)]
    finally:
        app.state.join_total_limiter.limit = 300
    assert statuses == [200] * 5 + [429]


async def test_wrong_codes_still_use_up_the_limit(app, teacher):
    _, roster = await new_lesson(teacher, 1)
    taken = {r["code"] for r in roster}
    wrong = [c for c in (f"{i:06d}" for i in range(100)) if c not in taken][:30]
    async with new_client(app) as guesser:
        for code in wrong:
            assert (await post(guesser, "/api/join", {"code": code, "name": "x"})).status_code == 404
        r = await post(guesser, "/api/join", {"code": roster[0]["code"], "name": "x"})
    assert (r.status_code, r.json()) == (429, {"error": "too_many_attempts"})


def test_forgive_removes_only_that_attempt():
    now = [0.0]
    limiter = auth.RateLimiter(limit=2, window=60, clock=lambda: now[0])
    first = limiter.admit("a")
    assert first and limiter.admit("a") and limiter.admit("a") is None
    limiter.forgive("a", first)
    assert limiter.admit("a") and limiter.admit("a") is None
    limiter.forgive("nobody", first)  # unknown key: nothing happens


def test_overlapping_success_cannot_free_a_failure():
    """A join admitted first and finishing last must remove its own entry,
    never a later failed guess, or more than the limit of failures get in."""
    now = [0.0]
    limiter = auth.RateLimiter(limit=30, window=60, clock=lambda: now[0])
    success = limiter.admit("nat")  # a real join, still in flight
    failures = 0
    for _ in range(40):
        now[0] += 0.01
        if limiter.admit("nat"):
            failures += 1
    assert failures == 29  # the in-flight join holds one place
    limiter.forgive("nat", success)
    now[0] += 0.01
    while limiter.admit("nat"):
        failures += 1
    assert failures == 30
    # Just after where the success's own entry would expire, all 30 failures
    # are still in the window, so nothing more gets in. (Forgiving the newest
    # entry instead would have left the success there, freeing a place now.)
    now[0] = 60.005
    assert limiter.admit("nat") is None


def test_forgiving_an_expired_attempt_leaves_newer_ones():
    now = [0.0]
    limiter = auth.RateLimiter(limit=3, window=60, clock=lambda: now[0])
    success = limiter.admit("nat")
    now[0] = 61  # the success's entry has left the window by the time it finishes
    a, b, c = limiter.admit("nat"), limiter.admit("nat"), limiter.admit("nat")
    assert a and b and c and limiter.admit("nat") is None
    limiter.forgive("nat", success)
    assert limiter.admit("nat") is None  # three failures still counted


async def test_closing_invalidates_codes_and_cookies(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 2)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    assert (await student.get("/api/student/me")).json() == {"student": None}
    with psycopg.connect(dsn) as conn:
        left = conn.execute("SELECT count(*) FROM auth_sessions a JOIN students s ON s.id = a.student_id"
                            " WHERE s.lesson_session_id = %s", (session_id,)).fetchone()[0]
    assert left == 0
    for code in [roster[0]["code"], roster[1]["code"]]:
        c, r = await join(app, code, "Aroha")
        assert r.status_code == 404


async def test_unbind_refuses_old_cookie_and_lets_the_code_bind_again(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 1)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    old_id = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"][0]["id"]
    r = await post(teacher, f"/api/students/{old_id}/unbind")
    assert r.status_code == 200 and r.json()["code"] == roster[0]["code"]
    assert (await student.get("/api/student/me")).json() == {"student": None}
    again, r = await join(app, roster[0]["code"], "Bob")
    assert r.json()["student"]["name"] == "Bob"
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT name, removed FROM students WHERE lesson_session_id = %s ORDER BY id",
                            (session_id,)).fetchall()
    assert rows == [("Aroha", True), ("Bob", False)]
    unbound = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"][0]
    await post(teacher, f"/api/students/{unbound['id']}/unbind")  # Bob unbound
    fresh = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"][0]
    r = await post(teacher, f"/api/students/{fresh['id']}/unbind")
    assert (r.status_code, r.json()) == (409, {"error": "not_bound"})


async def test_remove_refuses_old_cookie_and_retires_the_code(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    r = await post(teacher, f"/api/students/{roster[0]['id']}/remove")
    assert r.status_code == 200
    assert (await student.get("/api/student/me")).json() == {"student": None}
    assert (await post(teacher, f"/api/students/{roster[0]['id']}/remove")).status_code == 409
    c, r = await join(app, roster[0]["code"], "Aroha")
    assert r.status_code == 404
    assert (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"] == []


async def test_rename(app, teacher):
    session_id, roster = await new_lesson(teacher, 1)
    student, _ = await join(app, roster[0]["code"], "Aroah")
    r = await teacher.patch(f"/api/students/{roster[0]['id']}", json={"name": "Aroha"},
                            headers={"X-CSRF-Token": teacher.cookies["csrf_token"]})
    assert r.json() == {"id": roster[0]["id"], "name": "Aroha"}
    assert (await student.get("/api/student/me")).json()["student"]["name"] == "Aroha"


async def test_pause_blocks_sending_but_keeps_students_signed_in(app, teacher):
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from app.auth import require_student

    async def send(request):
        await require_student(request, sending=True)
        return JSONResponse({"sent": True})

    app.router.routes.append(Route("/api/test-send", send, methods=["POST"]))
    try:
        session_id, roster = await new_lesson(teacher, 1)
        student, _ = await join(app, roster[0]["code"], "Aroha")
        assert (await post(student, "/api/test-send")).json() == {"sent": True}
        await post(teacher, f"/api/sessions/{session_id}/state", {"state": "paused"})
        r = await post(student, "/api/test-send")
        assert (r.status_code, r.json()) == (409, {"error": "paused"})
        assert (await student.get("/api/student/me")).json()["student"]["state"] == "paused"
        # Joining a paused session works (R4.6 keeps students signed in).
        c, r = await join(app, roster[0]["code"], "Aroha")
        assert r.status_code == 200
        await post(teacher, f"/api/sessions/{session_id}/state", {"state": "open"})
        assert (await post(student, "/api/test-send")).status_code == 200
    finally:
        app.router.routes.pop()


async def test_student_requests_need_the_sessions_csrf_token(app, teacher):
    from starlette.routing import Route
    from starlette.responses import JSONResponse
    from app.auth import require_student

    async def send(request):
        await require_student(request, sending=True)
        return JSONResponse({"sent": True})

    app.router.routes.append(Route("/api/test-send2", send, methods=["POST"]))
    try:
        _, roster = await new_lesson(teacher, 1)
        student, _ = await join(app, roster[0]["code"], "Aroha")
        session = student.cookies["session"]
        r = await student.post("/api/test-send2", headers={
            "X-CSRF-Token": "planted", "Cookie": f"session={session}; csrf_token=planted"})
        assert (r.status_code, r.json()) == (403, {"error": "csrf"})
    finally:
        app.router.routes.pop()


async def test_joining_replaces_a_staff_session_on_that_device(app, dsn, teacher):
    _, roster = await new_lesson(teacher, 1)
    async with new_client(app) as device:
        await sign_in(device, await make_staff(dsn, "teacher"))
        staff_cookie = device.cookies["session"]
        await post(device, "/api/join", {"code": roster[0]["code"], "name": "Aroha"})
        assert (await device.get("/api/me")).json() == {"staff": None}
        replay = await device.get("/api/me", headers={"Cookie": f"session={staff_cookie}"})
        assert replay.json() == {"staff": None}


async def test_staff_actions_are_audited(teacher, dsn, app):
    session_id, roster = await new_lesson(teacher, 2)
    student, _ = await join(app, roster[0]["code"], "Aroha")
    await post(teacher, f"/api/sessions/{session_id}/codes", {"count": 1})
    await post(teacher, f"/api/students/{roster[0]['id']}/unbind")
    await post(teacher, f"/api/students/{roster[1]['id']}/remove")
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "paused"})
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    with psycopg.connect(dsn) as conn:
        actions = [r[0] for r in conn.execute(
            "SELECT action FROM audit WHERE (detail->>'session_id')::bigint = %s"
            " OR (detail->>'student_id')::bigint = ANY(%s) ORDER BY id",
            (session_id, [roster[0]["id"], roster[1]["id"]])).fetchall()]
    assert actions == ["session.start", "session.codes", "student.unbind", "student.remove",
                       "session.paused", "session.closed"]


async def test_running_out_of_codes_is_an_error_not_a_hang(teacher, monkeypatch):
    monkeypatch.setattr(classes.secrets, "randbelow", lambda n: 300_000)
    class_id = await new_class(teacher)
    response = await post(teacher, f"/api/classes/{class_id}/sessions", {"count": 2})
    assert (response.status_code, response.json()) == (503, {"error": "no_free_codes"})


async def test_a_retired_code_is_never_handed_out_again_in_its_session(teacher, monkeypatch):
    session_id, roster = await new_lesson(teacher, 1)
    retired = roster[0]["code"]
    await post(teacher, f"/api/students/{roster[0]['id']}/remove")
    draws = iter([int(retired), int(retired), 400_123])
    monkeypatch.setattr(classes.secrets, "randbelow", lambda n: next(draws))
    response = await post(teacher, f"/api/sessions/{session_id}/codes", {"count": 1})
    assert response.json()["codes"] == ["400123"]


async def test_names_may_use_joiners(app, teacher):
    _, roster = await new_lesson(teacher, 1)
    c, r = await join(app, roster[0]["code"], "Sh‌irin")
    assert r.status_code == 200 and r.json()["student"]["name"] == "Sh‌irin"


async def test_admin_sees_the_owner_of_each_class(app, teacher, dsn):
    class_id = await new_class(teacher)
    async with new_client(app) as admin:
        await sign_in(admin, await make_staff(dsn, "admin"))
        listed = {c["id"]: c for c in (await admin.get("/api/classes")).json()["classes"]}
    assert listed[class_id]["teacher"].startswith("teacher-")


async def test_class_names_refuse_control_characters_and_odd_ids_are_404(teacher):
    response = await post(teacher, "/api/classes", {"name": "Year\u00079", "instructions": "line one\nline two"})
    assert (response.status_code, response.json()) == (400, {"error": "bad_name"})
    ok = await post(teacher, "/api/classes", {"name": "Year 9", "instructions": "line one\nline two"})
    assert ok.status_code == 201
    assert (await teacher.get(f"/api/sessions/{2**64}")).status_code == 404


async def test_join_racing_a_close_leaves_no_cookie(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 1)
    with psycopg.connect(dsn) as holder:
        # Hold the lesson session row the way a close does, then close it while
        # the join waits on its share lock.
        holder.execute("SELECT 1 FROM lesson_sessions WHERE id = %s FOR UPDATE", (session_id,))
        joining = asyncio.create_task(join(app, roster[0]["code"], "Aroha"))
        # Wait until the join is actually blocked on the lesson row lock.
        for _ in range(100):
            await asyncio.sleep(0.05)
            waiting = holder.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                " AND query LIKE %s", ("%FOR SHARE OF l%",)).fetchone()[0]
            if waiting:
                break
        assert waiting and not joining.done()
        holder.execute("UPDATE lesson_sessions SET state = 'closed', closed_at = now() WHERE id = %s",
                       (session_id,))
        holder.commit()
        _, response = await joining
    assert response.status_code == 404
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM auth_sessions a JOIN students s ON s.id = a.student_id"
                            " WHERE s.lesson_session_id = %s", (session_id,)).fetchone()[0] == 0


async def test_close_after_a_join_ends_its_cookie(app, teacher, dsn):
    session_id, roster = await new_lesson(teacher, 1)
    student, response = await join(app, roster[0]["code"], "Aroha")
    assert response.status_code == 200
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    assert (await student.get("/api/student/me")).json() == {"student": None}


@pytest.mark.parametrize("first", ["staff", "student"])
async def test_joining_over_a_live_session_needs_that_sessions_csrf_token(app, teacher, dsn, first):
    _, roster = await new_lesson(teacher, 2)
    async with new_client(app) as device:
        if first == "staff":
            await sign_in(device, await make_staff(dsn, "teacher"))
        else:
            await post(device, "/api/join", {"code": roster[0]["code"], "name": "Aroha"})
        session = device.cookies["session"]
        planted = await device.post("/api/join", json={"code": roster[1]["code"], "name": "Bob"}, headers={
            "X-CSRF-Token": "planted", "Cookie": f"session={session}; csrf_token=planted"})
        assert (planted.status_code, planted.json()) == (403, {"error": "csrf"})
        assert (await post(device, "/api/join", {"code": roster[1]["code"], "name": "Bob"})).status_code == 200


async def test_close_waiting_on_a_join_deletes_the_new_cookie(app, teacher, dsn):
    # A join that holds its locks until commit makes a close wait; the close
    # then removes the cookie row the join created.
    session_id, roster = await new_lesson(teacher, 1)
    with psycopg.connect(dsn) as joiner:
        joiner.execute(
            "SELECT 1 FROM students st JOIN lesson_sessions l ON l.id = st.lesson_session_id"
            " WHERE st.id = %s FOR UPDATE OF st FOR SHARE OF l", (roster[0]["id"],))
        joiner.execute("UPDATE students SET name = 'Aroha', bound_at = now() WHERE id = %s", (roster[0]["id"],))
        joiner.execute("INSERT INTO auth_sessions (token_hash, student_id, csrf_token, expires_at)"
                       " VALUES (%s, %s, 'c', now() + interval '1 hour')", (b"j" * 32, roster[0]["id"]))
        closing = asyncio.create_task(post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"}))
        for _ in range(100):
            await asyncio.sleep(0.05)
            waiting = joiner.execute("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                                     " AND query LIKE 'UPDATE lesson_sessions%%'").fetchone()[0]
            if waiting:
                break
        assert waiting and not closing.done()
        joiner.commit()
        assert (await closing).status_code == 200
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM auth_sessions WHERE student_id = %s",
                            (roster[0]["id"],)).fetchone()[0] == 0


async def test_join_over_a_live_session_uses_one_pool_connection(dsn, teacher, monkeypatch):
    from app import db
    from app.main import create_app
    from psycopg_pool import AsyncConnectionPool

    _, roster = await new_lesson(teacher, 2)

    async def tiny_pool(url):
        pool = AsyncConnectionPool(url, min_size=1, max_size=1, open=False, timeout=5)
        await pool.open(wait=True)
        return pool

    monkeypatch.setattr(db, "open_pool", tiny_pool)
    small = create_app(dsn)
    async with small.router.lifespan_context(small):
        async with new_client(small) as device:
            assert (await post(device, "/api/join", {"code": roster[0]["code"], "name": "Aroha"})).status_code == 200
            again = await asyncio.wait_for(post(device, "/api/join", {"code": roster[1]["code"], "name": "Bob"}), 30)
    assert again.status_code == 200
