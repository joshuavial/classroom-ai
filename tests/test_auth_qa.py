"""QA additions for step 1: gaps in CSRF, audit and cookie coverage."""

import psycopg

from app import auth
from tests.test_auth import PASSWORD, audit_rows, csrf, password_hash, make_staff, setup_code, sign_in  # noqa: F401


def audit_count(dsn: str) -> int:
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT count(*) FROM audit").fetchone()[0]


async def test_failed_setup_and_failed_create_write_no_audit_rows(fresh_client, empty_dsn, client, make_staff, dsn):
    body = {"code": "WRONGWRONG22", "username": "admin", "password": PASSWORD}
    assert (await fresh_client.post("/api/setup", json=body, headers=await csrf(fresh_client))).status_code == 403
    assert audit_count(empty_dsn) == 0
    await sign_in(client, make_staff("teacher"))
    body = {"username": "qa-denied", "password": PASSWORD, "role": "teacher"}
    assert (await client.post("/api/staff", json=body, headers=await csrf(client))).status_code == 403
    assert audit_rows(dsn, "qa-denied") == []


async def test_header_without_cookie_is_refused(client, make_staff):
    await sign_in(client, make_staff("admin"))
    token = client.cookies["csrf_token"]
    client.cookies.delete("csrf_token")
    body = {"username": "qa-nocookie", "password": PASSWORD, "role": "teacher"}
    response = await client.post("/api/staff", json=body, headers={"X-CSRF-Token": token})
    assert (response.status_code, response.json()) == (403, {"error": "csrf"})


async def test_me_replaces_a_csrf_cookie_that_is_not_the_sessions(client, make_staff):
    await sign_in(client, make_staff("admin"))
    token, session = client.cookies["csrf_token"], client.cookies["session"]
    response = await client.get("/api/me", headers={"Cookie": f"session={session}; csrf_token=planted"})
    assert response.json()["staff"] is not None
    assert f"csrf_token={token};" in ",".join(response.headers.get_list("set-cookie"))


async def test_expired_session_post_is_401(client, make_staff, dsn):
    await sign_in(client, make_staff("admin"))
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE auth_sessions SET expires_at = now() - interval '1 second'"
                     " WHERE token_hash = %s", (auth.token_hash(client.cookies["session"]),))
    body = {"username": "qa-expired", "password": PASSWORD, "role": "teacher"}
    assert (await client.post("/api/staff", json=body, headers=await csrf(client))).status_code == 401


async def test_logout_clears_cookies_with_the_attributes_they_were_set_with(client, make_staff):
    await sign_in(client, make_staff())
    response = await client.post("/api/logout", headers=await csrf(client))
    cleared = {h.split("=", 1)[0]: h.lower() for h in response.headers.get_list("set-cookie")}
    for name in ("session", "csrf_token"):
        assert "max-age=0" in cleared[name] and "path=/" in cleared[name]
        assert "secure" in cleared[name] and "samesite=lax" in cleared[name]
    assert "httponly" in cleared["session"]


async def test_logout_with_a_planted_token_that_is_not_the_sessions_is_refused(client, make_staff, dsn):
    """Plan C5: matching cookie and header but not the session's token is 403
    for each state-changing endpoint, logout included."""
    await sign_in(client, make_staff())
    session = client.cookies["session"]
    response = await client.post("/api/logout", headers={
        "X-CSRF-Token": "planted", "Cookie": f"session={session}; csrf_token=planted"})
    assert response.status_code == 403
    with psycopg.connect(dsn) as conn:
        row = conn.execute("SELECT 1 FROM auth_sessions WHERE token_hash = %s", (auth.token_hash(session),)).fetchone()
    assert row is not None


async def test_unknown_user_costs_one_hash_even_on_the_first_try(client, monkeypatch):
    """Plan: an unknown user runs one scrypt, so timing does not reveal usernames."""
    calls = []
    real = auth._scrypt
    monkeypatch.setattr(auth, "_scrypt", lambda *a: calls.append(1) or real(*a))
    assert auth._dummy_hash is not None  # built when the app started
    response = await client.post("/api/login", json={"username": "qa-nobody", "password": PASSWORD},
                                 headers=await csrf(client))
    assert response.status_code == 401
    assert len(calls) == 1


async def test_logout_really_deletes_the_session_row(client, make_staff, dsn):
    """Logout deletes the auth_sessions row, not only the cookie."""
    await sign_in(client, make_staff())
    session = client.cookies["session"]
    assert (await client.post("/api/logout", headers=await csrf(client))).status_code == 204
    response = await client.get("/api/me", headers={"Cookie": f"session={session}"})
    assert response.json() == {"staff": None}
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT 1 FROM auth_sessions WHERE token_hash = %s",
                            (auth.token_hash(session),)).fetchone() is None
