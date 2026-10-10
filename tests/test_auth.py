"""Staff accounts: setup, login, sessions, roles, CSRF and audit (step 1)."""

import asyncio
import logging
import secrets

import httpx
import psycopg
import pytest

from app import auth
from app.main import create_app

PASSWORD = "correct horse battery"


@pytest.fixture(scope="session")
async def password_hash():
    return await auth.hash_password(PASSWORD)


@pytest.fixture
def make_staff(dsn, password_hash):
    def make(role: str = "teacher") -> str:
        username = f"{role}-{secrets.token_hex(4)}"
        with psycopg.connect(dsn) as conn:
            conn.execute(
                "INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, %s)",
                (username, password_hash, role),
            )
        return username

    return make


async def csrf(client: httpx.AsyncClient) -> dict[str, str]:
    """Headers for a state-changing request, fetching a token first if needed."""
    if "csrf_token" not in client.cookies:
        await client.get("/api/me")
    return {"X-CSRF-Token": client.cookies["csrf_token"]}


async def sign_in(client, username: str) -> None:
    response = await client.post("/api/login", json={"username": username, "password": PASSWORD},
                                 headers=await csrf(client))
    assert response.status_code == 200, response.text


def audit_rows(dsn: str, username: str) -> list[tuple]:
    with psycopg.connect(dsn) as conn:
        return conn.execute(
            "SELECT username, action, detail FROM audit WHERE detail->>'username' = %s", (username,)
        ).fetchall()


def setup_code(dsn: str) -> str:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT value FROM settings WHERE key = 'setup_code'").fetchone()[0]


# Passwords.


async def test_password_hash_round_trip_and_cost():
    stored = await auth.hash_password("a long password")
    assert stored.startswith("scrypt$131072$8$1$")
    assert await auth.verify_password("a long password", stored)
    assert not await auth.verify_password("another password", stored)
    assert not await auth.verify_password("a long password", "garbage")


# First-run setup.


async def test_setup_code_is_logged_and_setup_works_once(fresh_client, empty_dsn, caplog):
    code = setup_code(empty_dsn)
    assert len(code) == 12
    assert (await fresh_client.get("/api/setup")).json() == {"needed": True}
    typed = f"{code[:4].lower()} {code[4:8]}-{code[8:]}"  # spaces, dashes and case are forgiven
    body = {"code": typed, "username": "Admin", "password": PASSWORD}
    response = await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))
    assert response.status_code == 200
    assert response.json() == {"staff": {"username": "admin", "role": "admin"}}
    assert (await fresh_client.get("/api/me")).json()["staff"]["role"] == "admin"
    assert (await fresh_client.get("/api/setup")).json() == {"needed": False}
    again = await fresh_client.post("/api/setup", json={**body, "username": "second"},
                                    headers=await csrf(fresh_client))
    assert again.status_code == 409
    assert audit_rows(empty_dsn, "admin") == [("admin", "staff.create", {"username": "admin", "role": "admin"})]
    assert audit_rows(empty_dsn, "second") == []


async def test_setup_code_is_printed_on_start(empty_dsn, caplog):
    app = create_app(empty_dsn)
    with caplog.at_level(logging.WARNING, logger="app.auth"):
        async with app.router.lifespan_context(app):
            pass
    code = setup_code(empty_dsn)
    assert f"{code[:4]}-{code[4:8]}-{code[8:]}" in caplog.text


async def test_wrong_setup_code_is_refused_and_code_still_works(fresh_client, empty_dsn):
    body = {"code": "WRONGWRONG22", "username": "admin", "password": PASSWORD}
    response = await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))
    assert response.status_code == 403
    assert response.json() == {"error": "wrong_code"}
    with psycopg.connect(empty_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM staff").fetchone()[0] == 0
    body["code"] = setup_code(empty_dsn)
    assert (await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))).status_code == 200


async def test_non_ascii_setup_code_is_refused_not_an_error(fresh_client):
    body = {"code": "ÄÖÜ", "username": "admin", "password": PASSWORD}
    response = await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))
    assert response.status_code == 403


async def test_concurrent_setups_make_one_admin(fresh_app, empty_dsn):
    code = setup_code(empty_dsn)

    async def attempt(i: int) -> int:
        transport = httpx.ASGITransport(app=fresh_app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
            body = {"code": code, "username": f"admin{i}", "password": PASSWORD}
            return (await c.post("/api/setup", json=body, headers=await csrf(c))).status_code

    statuses = await asyncio.gather(*(attempt(i) for i in range(4)))
    assert sorted(statuses) == [200, 409, 409, 409]
    with psycopg.connect(empty_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM staff").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM audit").fetchone()[0] == 1
        assert conn.execute("SELECT count(*) FROM auth_sessions").fetchone()[0] == 1


async def test_restart_after_setup_does_not_bring_the_code_back(fresh_client, empty_dsn):
    body = {"code": setup_code(empty_dsn), "username": "admin", "password": PASSWORD}
    await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))
    app = create_app(empty_dsn)
    async with app.router.lifespan_context(app):
        pass
    with psycopg.connect(empty_dsn) as conn:
        assert conn.execute("SELECT count(*) FROM settings WHERE key = 'setup_code'").fetchone()[0] == 0


# Login, sessions and cookies.


async def test_login_sets_cookies_and_me_returns_the_user(client, make_staff):
    username = make_staff("teacher")
    response = await client.post("/api/login", json={"username": username, "password": PASSWORD},
                                 headers=await csrf(client))
    assert response.status_code == 200
    assert response.json() == {"staff": {"username": username, "role": "teacher"}}
    cookies = {c.split("=", 1)[0]: c.lower() for c in response.headers.get_list("set-cookie")}
    assert "httponly" in cookies["session"] and "secure" in cookies["session"] and "samesite=lax" in cookies["session"]
    assert "httponly" not in cookies["csrf_token"] and "secure" in cookies["csrf_token"]
    assert (await client.get("/api/me")).json() == {"staff": {"username": username, "role": "teacher"}}


async def test_cookies_are_not_secure_over_plain_http(app, make_staff):
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
        await c.get("/api/me")
        response = await c.post("/api/login", json={"username": make_staff(), "password": PASSWORD},
                                headers={"X-CSRF-Token": c.cookies["csrf_token"]})
    assert response.status_code == 200
    assert all("secure" not in h.lower() for h in response.headers.get_list("set-cookie"))


async def test_wrong_password_and_unknown_user_look_the_same(client, make_staff):
    username = make_staff()
    wrong = await client.post("/api/login", json={"username": username, "password": "not the password"},
                              headers=await csrf(client))
    unknown = await client.post("/api/login", json={"username": "nobody", "password": PASSWORD},
                                headers=await csrf(client))
    assert (wrong.status_code, wrong.json()) == (unknown.status_code, unknown.json()) == (401, {"error": "bad_login"})
    assert "session" not in client.cookies


async def test_logout_ends_the_session(client, make_staff, dsn):
    await sign_in(client, make_staff())
    old_session = client.cookies["session"]
    response = await client.post("/api/logout", headers=await csrf(client))
    assert response.status_code == 204
    assert "session" not in client.cookies
    replay = await client.get("/api/me", headers={"Cookie": f"session={old_session}"})
    assert replay.json() == {"staff": None}


async def test_logout_without_a_session_still_succeeds(client):
    assert (await client.post("/api/logout", headers=await csrf(client))).status_code == 204


async def test_expired_session_is_refused(client, make_staff, dsn):
    await sign_in(client, make_staff())
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = now() - interval '1 minute'"
                     " WHERE token_hash = %s", (auth.token_hash(client.cookies["session"]),))
    assert (await client.get("/api/me")).json() == {"staff": None}
    assert (await client.get("/api/staff")).status_code == 401


async def test_deleted_session_row_is_refused(client, make_staff, dsn):
    await sign_in(client, make_staff("admin"))
    with psycopg.connect(dsn) as conn:
        conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (auth.token_hash(client.cookies["session"]),))
    assert (await client.get("/api/staff")).status_code == 401


async def test_login_rate_limit(client):
    statuses = []
    for _ in range(11):
        r = await client.post("/api/login", json={"username": "nobody", "password": "x" * 10},
                              headers=await csrf(client))
        statuses.append(r.status_code)
    assert statuses == [401] * 10 + [429]


def test_rate_limiter_window_and_memory_bound():
    now = [0.0]
    limiter = auth.RateLimiter(limit=2, window=60, clock=lambda: now[0], max_keys=3)
    assert limiter.allow("a") and limiter.allow("a") and not limiter.allow("a")
    now[0] = 60.0
    assert limiter.allow("a")
    for ip in "bcd":
        limiter.allow(ip)
    assert len(limiter.hits) <= 3
    now[0] = 500.0
    limiter.allow("e")
    assert list(limiter.hits) == ["e"]


# Roles.


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/staff", None),
    ("POST", "/api/staff", {"username": "new-teacher", "password": PASSWORD, "role": "teacher"}),
])
async def test_admin_endpoints_need_an_admin(client, app, make_staff, method, path, body):
    # Anonymous
    anon = await client.request(method, path, json=body, headers=await csrf(client))
    assert anon.status_code == 401
    # Teacher
    await sign_in(client, make_staff("teacher"))
    teacher = await client.request(method, path, json=body, headers=await csrf(client))
    assert (teacher.status_code, teacher.json()) == (403, {"error": "forbidden"})


async def test_admin_creates_a_teacher_who_can_sign_in(client, app, make_staff, dsn):
    admin = make_staff("admin")
    await sign_in(client, admin)
    username = f"t-{secrets.token_hex(3)}"
    body = {"username": username, "password": PASSWORD, "role": "teacher"}
    response = await client.post("/api/staff", json=body, headers=await csrf(client))
    assert response.status_code == 201
    listed = (await client.get("/api/staff")).json()["staff"]
    assert {"username": username, "role": "teacher"}.items() <= next(s for s in listed if s["username"] == username).items()
    assert audit_rows(dsn, username) == [(admin, "staff.create", {"username": username, "role": "teacher"})]
    duplicate = await client.post("/api/staff", json=body, headers=await csrf(client))
    assert duplicate.status_code == 409
    assert len(audit_rows(dsn, username)) == 1
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as teacher:
        await sign_in(teacher, username)
        assert (await teacher.get("/api/me")).json()["staff"] == {"username": username, "role": "teacher"}


@pytest.mark.parametrize("body,error", [
    ({"username": "bad name!", "password": PASSWORD, "role": "teacher"}, "bad_username"),
    ({"username": "ok", "password": "short", "role": "teacher"}, "bad_password"),
    ({"username": "ok2", "password": PASSWORD, "role": "owner"}, "bad_role"),
    ({"username": "ok3", "password": 12345678901, "role": "teacher"}, "bad_request"),
])
async def test_staff_create_validates_input(client, make_staff, body, error):
    await sign_in(client, make_staff("admin"))
    response = await client.post("/api/staff", json=body, headers=await csrf(client))
    assert (response.status_code, response.json()) == (400, {"error": error})


async def test_non_object_body_is_400(client, make_staff):
    await sign_in(client, make_staff("admin"))
    response = await client.post("/api/staff", content=b"[1, 2]", headers=await csrf(client))
    assert response.status_code == 400


# CSRF.


async def test_state_changing_requests_need_the_csrf_header(client, make_staff):
    await sign_in(client, make_staff("admin"))
    body = {"username": "x-teacher", "password": PASSWORD, "role": "teacher"}
    for path in ["/api/staff", "/api/logout", "/api/login", "/api/setup"]:
        no_header = await client.post(path, json=body)
        assert (no_header.status_code, no_header.json()) == (403, {"error": "csrf"}), path
        wrong = await client.post(path, json=body, headers={"X-CSRF-Token": "not-the-token"})
        assert wrong.status_code == 403, path


async def test_matching_cookie_and_header_but_not_the_sessions_token_is_refused(client, make_staff):
    await sign_in(client, make_staff("admin"))
    session = client.cookies["session"]
    body = {"username": "planted-teacher", "password": PASSWORD, "role": "teacher"}
    response = await client.post("/api/staff", json=body, headers={
        "X-CSRF-Token": "planted", "Cookie": f"session={session}; csrf_token=planted"})
    assert (response.status_code, response.json()) == (403, {"error": "csrf"})


async def test_me_restores_the_sessions_csrf_cookie(client, make_staff, dsn):
    await sign_in(client, make_staff("admin"))
    token = client.cookies["csrf_token"]
    client.cookies.delete("csrf_token")
    response = await client.get("/api/me")
    set_cookies = [h for h in response.headers.get_list("set-cookie") if h.startswith("csrf_token=")]
    assert len(set_cookies) == 1
    assert client.cookies["csrf_token"] == token
    body = {"username": f"r-{secrets.token_hex(3)}", "password": PASSWORD, "role": "teacher"}
    assert (await client.post("/api/staff", json=body, headers=await csrf(client))).status_code == 201


async def test_fresh_browser_gets_a_pre_session_token(client):
    response = await client.get("/api/setup")
    assert "csrf_token" in client.cookies
    assert len([h for h in response.headers.get_list("set-cookie") if h.startswith("csrf_token=")]) == 1


async def test_login_issues_a_new_session_token(client, make_staff):
    headers = {**(await csrf(client)), "Cookie": f"session=attacker-chosen; csrf_token={client.cookies['csrf_token']}"}
    response = await client.post("/api/login", json={"username": make_staff(), "password": PASSWORD}, headers=headers)
    issued = next(h for h in response.headers.get_list("set-cookie") if h.startswith("session="))
    assert not issued.startswith("session=attacker-chosen;")


async def test_heartbeat_path_is_exempt_from_cookie_csrf(client):
    # The middleware lets it through; the join token check refuses it.
    response = await client.post("/api/workers/heartbeat", json={})
    assert (response.status_code, response.json()) == (401, {"error": "unauthorised"})


async def test_database_down_is_503(dsn):
    app = create_app(dsn)
    async with app.router.lifespan_context(app):
        await app.state.pool.close()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
            await c.get("/healthz")
            response = await c.get("/api/setup")
    assert (response.status_code, response.json()) == (503, {"error": "unavailable"})


async def test_oversized_body_is_413(client, make_staff):
    await sign_in(client, make_staff("admin"))
    body = {"username": "big", "password": "x" * 70_000, "role": "teacher"}
    response = await client.post("/api/staff", json=body, headers=await csrf(client))
    assert response.status_code == 413


async def test_wrong_setup_code_is_refused_before_any_hashing(fresh_client, monkeypatch):
    calls = []
    monkeypatch.setattr(auth, "_scrypt", lambda *a: calls.append(1))
    body = {"code": "WRONGWRONG22", "username": "admin", "password": PASSWORD}
    response = await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))
    assert response.status_code == 403 and calls == []


async def test_heartbeat_path_gets_no_csrf_cookie(client):
    response = await client.post("/api/workers/heartbeat", json={})
    assert not [h for h in response.headers.get_list("set-cookie") if h.startswith("csrf_token=")]


async def test_login_over_a_live_session_needs_that_sessions_csrf_token(client, make_staff):
    await sign_in(client, make_staff("teacher"))
    session = client.cookies["session"]
    response = await client.post("/api/login", json={"username": make_staff(), "password": PASSWORD}, headers={
        "X-CSRF-Token": "planted", "Cookie": f"session={session}; csrf_token=planted"})
    assert (response.status_code, response.json()) == (403, {"error": "csrf"})


async def test_login_over_a_live_session_with_its_token_rotates_both_cookies(client, make_staff, dsn):
    await sign_in(client, make_staff("teacher"))
    old_session, old_csrf = client.cookies["session"], client.cookies["csrf_token"]
    await sign_in(client, make_staff("admin"))
    assert client.cookies["session"] != old_session and client.cookies["csrf_token"] != old_csrf
    assert (await client.get("/api/me")).json()["staff"]["role"] == "admin"
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM auth_sessions WHERE token_hash = %s",
                            (auth.token_hash(old_session),)).fetchone()[0] == 0


async def test_login_over_a_live_session_uses_one_pool_connection(dsn, make_staff, monkeypatch):
    # With a one-connection pool, a sign-in that takes a second connection
    # while holding the first can never finish.
    from app import db
    from psycopg_pool import AsyncConnectionPool

    async def tiny_pool(url):
        pool = AsyncConnectionPool(url, min_size=1, max_size=1, open=False, timeout=5)
        await pool.open(wait=True)
        return pool

    monkeypatch.setattr(db, "open_pool", tiny_pool)
    app = create_app(dsn)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as c:
            await sign_in(c, make_staff())
            response = await asyncio.wait_for(
                c.post("/api/login", json={"username": make_staff(), "password": PASSWORD},
                       headers=await csrf(c)), timeout=30)
    assert response.status_code == 200
