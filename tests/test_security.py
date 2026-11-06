"""Security, session hardening, and boundary fuzz testing (cai-1ir.56).

Verifies timing-safe secret comparisons, brute-force boundaries, CSRF/Origin enforcement,
cross-origin stream hijacking defenses, session lifecycle fuzzing, unicode normalization,
prompt/name boundaries, and fail-closed logging/database invariants.

Note on architecture:
Sessions in classroom-ai are opaque database-backed tokens (auth_sessions table),
not client-side signed JWTs or base64 serialized cookies. Malformed, truncated,
or tampered tokens fail closed against the database index with zero 500 errors.
"""

import asyncio
import hmac
import json
import logging
from pathlib import Path
import secrets
import socket
import unicodedata

import httpx
import psycopg
import pytest
import uvicorn
from starlette.requests import Request
from starlette.routing import Route

from app import auth, chat, guard, live, students
from app.main import DropQueryStrings, PoolErrorDetails, create_app
from tests.helpers import PASSWORD, csrf, make_staff, new_client, post, sign_in


@pytest.fixture
async def teacher(client, dsn):
    await sign_in(client, await make_staff(dsn, "teacher"))
    return client


@pytest.fixture
async def admin(client, dsn):
    await sign_in(client, await make_staff(dsn, "admin"))
    return client


async def new_lesson_with_roster(client, count: int = 5) -> tuple[int, list[dict]]:
    class_body = {
        "name": "Security Test Class",
        "instructions": "Be safe.",
        "message_limit": 20,
    }
    res = await post(client, "/api/classes", class_body)
    assert res.status_code == 201, res.text
    class_id = res.json()["id"]

    res = await post(client, f"/api/classes/{class_id}/sessions", {"count": count})
    assert res.status_code == 201, res.text
    session_id = res.json()["id"]

    roster = (await client.get(f"/api/sessions/{session_id}")).json()["roster"]
    return session_id, roster


# ==============================================================================
# 1. Timing-attack resistance and token brute-force boundaries
# ==============================================================================


def test_timing_safe_token_comparisons(monkeypatch):
    """same_token delegates to hmac.compare_digest for constant-time comparisons."""
    compare_calls: list[tuple[bytes, bytes]] = []
    real_compare = hmac.compare_digest

    def spy_compare(a, b):
        compare_calls.append((a, b))
        return real_compare(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy_compare)

    # Identical tokens match and call hmac.compare_digest
    token = secrets.token_urlsafe(32)
    assert auth.same_token(token, token) is True
    assert len(compare_calls) == 1
    assert compare_calls[-1] == (token.encode(), token.encode())

    # Differing tokens of the same length
    other = secrets.token_urlsafe(32)
    assert auth.same_token(token, other) is False
    assert compare_calls[-1] == (token.encode(), other.encode())

    # Tokens of differing lengths
    assert auth.same_token(token, token[:16]) is False
    assert auth.same_token(token[:16], token) is False

    # Single-character divergence at boundaries
    prefix = "a" * 31
    assert auth.same_token(prefix + "x", prefix + "y") is False
    assert auth.same_token("x" + prefix, "y" + prefix) is False
    assert auth.same_token("a" * 15 + "x" + "a" * 16, "a" * 15 + "y" + "a" * 16) is False

    # Special characters and unicode
    assert auth.same_token("token-with-unicode-key", "token-with-unicode-key") is True
    assert auth.same_token("token-with-unicode-key", "token-with-unicode-diff") is False

    calls_before_empty = len(compare_calls)
    # Empty strings and None must fail closed without invoking compare_digest
    assert auth.same_token("", "") is False
    assert auth.same_token(token, "") is False
    assert auth.same_token("", token) is False
    assert auth.same_token(None, token) is False
    assert auth.same_token(token, None) is False
    assert auth.same_token(None, None) is False
    assert len(compare_calls) == calls_before_empty, "Empty/None inputs must short-circuit"


async def test_password_dummy_verify_timing_protection(client, monkeypatch):
    """Non-existent username attempts execute dummy_verify to protect against timing attacks."""
    called_dummy = False
    original_dummy = auth.dummy_verify

    async def spy_dummy(password: str) -> None:
        nonlocal called_dummy
        called_dummy = True
        await original_dummy(password)

    monkeypatch.setattr(auth, "dummy_verify", spy_dummy)

    # Attempt login with a non-existent staff username
    response = await post(
        client,
        "/api/login",
        {"username": "non-existent-user-xyz", "password": PASSWORD},
    )
    assert response.status_code == 401
    assert response.json() == {"error": "bad_login"}
    assert called_dummy is True, "dummy_verify must be invoked when username is not found"


async def test_login_rate_limiting_brute_force_boundary(client, app, monkeypatch):
    """Login rate limiter enforces 10 attempts per minute per IP, protecting scrypt CPU budget."""
    async def fast_dummy(_pwd):
        return None

    monkeypatch.setattr(auth, "dummy_verify", fast_dummy)
    ip = "192.168.10.1"
    headers = {**await csrf(client), "X-Forwarded-For": ip}

    # 10 invalid attempts within the sliding window return 401
    for i in range(10):
        resp = await client.post(
            "/api/login",
            json={"username": f"baduser_{i}", "password": "wrongpassword"},
            headers=headers,
        )
        assert resp.status_code == 401, f"Attempt {i + 1} should be 401"
        assert resp.json() == {"error": "bad_login"}

    # 11th attempt must fail closed with 429 too_many_attempts without running scrypt
    resp_11 = await client.post(
        "/api/login",
        json={"username": "baduser_11", "password": "wrongpassword"},
        headers=headers,
    )
    assert resp_11.status_code == 429
    assert resp_11.json() == {"error": "too_many_attempts"}

    # Another IP address is isolated and still admitted
    other_ip_headers = {**await csrf(client), "X-Forwarded-For": "192.168.10.2"}
    resp_other = await client.post(
        "/api/login",
        json={"username": "baduser_other", "password": "wrongpassword"},
        headers=other_ip_headers,
    )
    assert resp_other.status_code == 401
    assert resp_other.json() == {"error": "bad_login"}

    # Resetting the limiter clears the window
    app.state.limiter.reset()
    resp_after_reset = await client.post(
        "/api/login",
        json={"username": "baduser_reset", "password": "wrongpassword"},
        headers=headers,
    )
    assert resp_after_reset.status_code == 401


async def test_join_student_code_brute_force_boundaries(client, teacher):
    """Student code guessing rate limiter blocks an IP after 30 failed attempts.

    Uses roster codes from an explicitly closed lesson session, eliminating any flake risk
    from randomly guessing codes that might match live sessions in the test database.
    """
    session_id, roster = await new_lesson_with_roster(teacher, count=35)
    # Close the lesson session so all 35 codes are permanently invalid for joining
    close_res = await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    assert close_res.status_code == 200

    closed_codes = [member["code"] for member in roster]
    assert len(closed_codes) >= 35

    ip = "192.168.20.1"
    headers = {**await csrf(client), "X-Forwarded-For": ip}

    # 30 failed join attempts with verified dead codes
    for i in range(30):
        code = closed_codes[i]
        resp = await client.post(
            "/api/join",
            json={"code": code, "name": "Student Guess"},
            headers=headers,
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "bad_code"}

    # 31st attempt hits the join_limiter and returns 429
    resp_31 = await client.post(
        "/api/join",
        json={"code": closed_codes[30], "name": "Student Guess"},
        headers=headers,
    )
    assert resp_31.status_code == 429
    assert resp_31.json() == {"error": "too_many_attempts"}

    # An unrelated IP remains unaffected
    other_headers = {**await csrf(client), "X-Forwarded-For": "192.168.20.2"}
    resp_other = await client.post(
        "/api/join",
        json={"code": closed_codes[31], "name": "Student Guess"},
        headers=other_headers,
    )
    assert resp_other.status_code == 404


# ==============================================================================
# 2. CSRF, Origin, and Host header tampering, cross-origin stream hijacking, and CRLF
# ==============================================================================


async def test_csrf_double_submit_token_tampering(client, teacher):
    """CSRFMiddleware rejects state-changing requests with missing or tampered CSRF tokens."""
    # 1. Missing X-CSRF-Token header
    res = await client.post("/api/classes", json={"name": "Class 1"})
    assert res.status_code == 403
    assert res.json() == {"error": "csrf"}

    # 2. Empty X-CSRF-Token header
    res = await client.post("/api/classes", json={"name": "Class 1"}, headers={"X-CSRF-Token": ""})
    assert res.status_code == 403
    assert res.json() == {"error": "csrf"}

    # 3. Mismatched X-CSRF-Token header
    res = await client.post(
        "/api/classes",
        json={"name": "Class 1"},
        headers={"X-CSRF-Token": "arbitrary_token_not_matching_cookie"},
    )
    assert res.status_code == 403
    assert res.json() == {"error": "csrf"}

    # 4. Header containing null bytes, spaces, or newlines
    for bad_token in ("\x00", "   ", "token\r\n", "token\x00extra"):
        res = await client.post(
            "/api/classes",
            json={"name": "Class 1"},
            headers={"X-CSRF-Token": bad_token},
        )
        assert res.status_code == 403
        assert res.json() == {"error": "csrf"}

    # 5. Safe GET methods do not require X-CSRF-Token
    res_get = await client.get("/api/classes")
    assert res_get.status_code == 200


async def test_csrf_session_binding_tampering(client, teacher, dsn):
    """An attacker supplying matching cookie and header cannot forge CSRF for a live session."""
    session_token = client.cookies["session"]
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT csrf_token FROM auth_sessions WHERE token_hash = %s",
            (auth.token_hash(session_token),),
        ).fetchone()
    real_csrf = row[0]

    # Attacker plants their own matching pair: cookie="attacker_token", header="attacker_token"
    # Even though cookie == header passes CSRFMiddleware, check_session_csrf checks against the DB
    planted = "attacker_forged_csrf_value_12345"
    assert planted != real_csrf

    res = await client.post(
        "/api/classes",
        json={"name": "Attacker Class"},
        headers={
            "X-CSRF-Token": planted,
            "Cookie": f"session={session_token}; csrf_token={planted}",
        },
    )
    assert res.status_code == 403
    assert res.json() == {"error": "csrf"}

    # Confirm session was not corrupted
    with psycopg.connect(dsn) as conn:
        still_valid = conn.execute(
            "SELECT 1 FROM auth_sessions WHERE token_hash = %s",
            (auth.token_hash(session_token),),
        ).fetchone()
    assert still_valid is not None


async def test_origin_and_host_header_tampering(client, teacher):
    """Cross-origin Origin/Referer and tampered Host headers are rejected or kept safe.

    Proves that the application does not configure CORS grants, preventing cross-origin
    pages from accessing credentialed endpoints via CORS preflights or state-changing requests.
    """
    # 1. State-changing request with cross-origin Origin and no CSRF header fails closed
    res = await client.post(
        "/api/classes",
        json={"name": "Evil Class"},
        headers={
            "Origin": "https://evil-attacker.example.com",
            "Referer": "https://evil-attacker.example.com/exploit",
        },
    )
    assert res.status_code == 403
    assert res.json() == {"error": "csrf"}

    # 2. CORS preflight OPTIONS request from malicious origin receives no CORS grant headers
    res_options = await client.options(
        "/api/classes",
        headers={
            "Origin": "https://evil-attacker.example.com",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "X-CSRF-Token",
        },
    )
    assert "access-control-allow-origin" not in res_options.headers
    assert "access-control-allow-credentials" not in res_options.headers

    # 3. Authenticated state-changing request presenting valid credentials but cross-origin Origin
    # does not echo back Access-Control-Allow-Origin or allow credentialed CORS access
    res_auth_origin = await teacher.post(
        "/api/classes",
        json={"name": "Legit Class with Origin"},
        headers={
            **await csrf(teacher),
            "Origin": "https://evil-attacker.example.com",
        },
    )
    assert res_auth_origin.status_code == 201
    assert "access-control-allow-origin" not in res_auth_origin.headers
    assert "access-control-allow-credentials" not in res_auth_origin.headers

    # 4. Tampered Host header does not result in host poisoning or external redirection
    res_host = await client.get("/api/me", headers={"Host": "evil-host.com"})
    assert res_host.status_code == 200
    assert "evil-host.com" not in res_host.headers.get("location", "")


async def test_cross_origin_sse_hijacking_attempts(app, teacher, dsn, monkeypatch):
    """Unauthorized origins, non-owners, and students cannot hijack teacher SSE streams.

    Verifies that teacher SSE streams are protected against cross-origin reads: even when
    requested with valid owner credentials and Origin header, no CORS grant is returned.
    """
    monkeypatch.setattr(live, "STREAM_SECONDS", 0.05)
    session_id, _ = await new_lesson_with_roster(teacher, count=1)

    # 1. Anonymous request without session cookie
    async with new_client(app) as anon:
        res = await anon.get(f"/api/sessions/{session_id}/events")
        assert res.status_code == 401
        assert res.json() == {"error": "not_signed_in"}

    # 2. Forged / invalid session cookie
    async with new_client(app) as forged:
        res = await forged.get(
            f"/api/sessions/{session_id}/events",
            headers={"Cookie": "session=invalid_fake_token; csrf_token=some_token"},
        )
        assert res.status_code == 401
        assert res.json() == {"error": "not_signed_in"}

    # 3. Other teacher (does not own the lesson session)
    async with new_client(app) as other_teacher:
        await sign_in(other_teacher, await make_staff(dsn, "teacher"))
        res = await other_teacher.get(f"/api/sessions/{session_id}/events")
        assert res.status_code == 404
        assert res.json() == {"error": "not_found"}

    # 4. Student session cookie attempting to access teacher events
    student_client = new_client(app)
    try:
        roster_code = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"][0]["code"]
        join_res = await post(student_client, "/api/join", {"code": roster_code, "name": "Student A"})
        assert join_res.status_code == 200

        res_student = await student_client.get(f"/api/sessions/{session_id}/events")
        assert res_student.status_code == 401
        assert res_student.json() == {"error": "not_signed_in"}
    finally:
        await student_client.aclose()

    # 5. Authenticated owner requesting SSE stream with cross-origin Origin:
    # Starlette provides no CORS headers, so the browser forbids cross-origin JavaScript from reading the stream
    res_sse_origin = await teacher.get(
        f"/api/sessions/{session_id}/events",
        headers={"Origin": "https://evil-attacker.example.com"},
    )
    assert res_sse_origin.status_code == 200
    assert "access-control-allow-origin" not in res_sse_origin.headers
    assert "access-control-allow-credentials" not in res_sse_origin.headers


async def test_crlf_injection_defenses(client):
    """CRLF injection attempts in usernames and student names are rejected or sanitized."""
    # 1. CRLF in username during login is rejected
    crlf_username = "admin\r\nSet-Cookie: evil=injected\r\n"
    res = await post(
        client,
        "/api/login",
        {"username": crlf_username, "password": PASSWORD},
    )
    assert res.status_code == 401
    assert res.json() == {"error": "bad_login"}
    assert "evil=injected" not in str(res.headers)

    # 2. clean_username enforces regex and rejects CRLF
    with pytest.raises(auth.HTTPError) as exc:
        auth.clean_username(crlf_username)
    assert exc.value.status == 400 and exc.value.code == "bad_username"

    # 3. Control characters (like null bytes) in student name are rejected with 400 bad_name
    res = await post(
        client,
        "/api/join",
        {"code": "123456", "name": "Alice\x00Injected"},
    )
    assert res.status_code == 400
    assert res.json() == {"error": "bad_name"}


# ==============================================================================
# 3. Session lifecycle boundary fuzzing
# ==============================================================================


async def test_session_cookie_replay_post_logout(client, teacher):
    """Replaying a staff session cookie post-logout fails closed."""
    session_token = teacher.cookies["session"]

    # Verify active session
    res = await teacher.get("/api/me")
    assert res.status_code == 200
    assert res.json()["staff"] is not None

    # Sign out
    logout_res = await teacher.post("/api/logout", headers=await csrf(teacher))
    assert logout_res.status_code == 204

    # Replay old session cookie
    replayed = await client.get("/api/me", headers={"Cookie": f"session={session_token}"})
    assert replayed.status_code == 200
    assert replayed.json() == {"staff": None}

    # Replay on protected endpoint
    replayed_classes = await client.get("/api/classes", headers={"Cookie": f"session={session_token}"})
    assert replayed_classes.status_code == 401
    assert replayed_classes.json() == {"error": "not_signed_in"}


async def test_student_session_cookie_replay_after_invalidation(app, teacher):
    """Replaying a student cookie after lesson closure or removal fails closed."""
    session_id, roster = await new_lesson_with_roster(teacher, count=1)
    code = roster[0]["code"]

    student = new_client(app)
    try:
        join_res = await post(student, "/api/join", {"code": code, "name": "Student Invalidation Test"})
        assert join_res.status_code == 200
        student_cookie = student.cookies["session"]

        # Models list is accessible while session is live
        models_res = await student.get("/api/student/models")
        assert models_res.status_code == 200

        # Teacher closes the session
        close_res = await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
        assert close_res.status_code == 200

        # Replaying the student cookie now fails with 401
        replayed = await student.get("/api/student/models", headers={"Cookie": f"session={student_cookie}"})
        assert replayed.status_code == 401
        assert replayed.json() == {"error": "not_signed_in"}
    finally:
        await student.aclose()


async def test_session_payload_boundary_fuzzing(client):
    """Fuzzing session cookie values across edge cases fails closed with no 500s."""
    fuzz_payloads = [
        "",
        "   ",
        "not-a-valid-token-format",
        "!@#$%^&*()_+-=[]{}|;':,./<>?",
        secrets.token_hex(64),
        "token_with_pct_encoded_%F0%9F%94%91",
        "valid_prefix%00null_byte",
        "A" * 4096,
        "' OR '1'='1",
        "admin'--",
        "'; DROP TABLE auth_sessions; --",
        "1' UNION SELECT 1, 'admin', 'admin', 'csrf'--",
        "../../etc/passwd",
        "%00%0d%0a",
    ]

    for payload in fuzz_payloads:
        # 1. /api/me should return {"staff": None}
        res_me = await client.get("/api/me", headers={"Cookie": f"session={payload}"})
        assert res_me.status_code == 200
        assert res_me.json() == {"staff": None}

        # 2. Protected endpoint /api/classes should return 401
        res_classes = await client.get("/api/classes", headers={"Cookie": f"session={payload}"})
        assert res_classes.status_code == 401
        assert res_classes.json() == {"error": "not_signed_in"}


async def test_expired_session_boundary(client, teacher, dsn):
    """A session expiring by even 1 millisecond is immediately invalid and purged."""
    session_token = teacher.cookies["session"]

    with psycopg.connect(dsn) as conn:
        conn.execute(
            "UPDATE auth_sessions SET expires_at = now() - interval '1 millisecond' WHERE token_hash = %s",
            (auth.token_hash(session_token),),
        )

    res = await teacher.get("/api/me")
    assert res.status_code == 200
    assert res.json() == {"staff": None}

    res_classes = await teacher.get("/api/classes")
    assert res_classes.status_code == 401
    assert res_classes.json() == {"error": "not_signed_in"}


async def test_concurrent_session_rotation(app, dsn):
    """Concurrent session rotation competing on the same existing session cookie revokes the old cookie cleanly."""
    app.state.limiter.reset()
    username = await make_staff(dsn, "teacher")

    # Establish an initial authenticated session
    async with new_client(app) as c_init:
        res = await post(c_init, "/api/login", {"username": username, "password": PASSWORD})
        assert res.status_code == 200
        old_session = c_init.cookies["session"]
        old_csrf = c_init.cookies["csrf_token"]

    # Verify old session exists in database
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM auth_sessions WHERE token_hash = %s",
            (auth.token_hash(old_session),),
        ).fetchone()
        assert row is not None

    # Competing rotations synchronized at a barrier to enforce simultaneous contention
    barrier = asyncio.Barrier(2)

    async def competing_rotation():
        await barrier.wait()
        async with new_client(app) as c:
            r = await c.post(
                "/api/login",
                json={"username": username, "password": PASSWORD},
                headers={
                    "Cookie": f"session={old_session}; csrf_token={old_csrf}",
                    "X-CSRF-Token": old_csrf,
                },
            )
            return r

    # Coordinate contention: hold row lock on auth_sessions in holder connection, then launch two competing rotations
    with psycopg.connect(dsn) as holder, psycopg.connect(dsn, autocommit=True) as observer:
        holder.execute(
            "SELECT 1 FROM auth_sessions WHERE token_hash = %s FOR UPDATE",
            (auth.token_hash(old_session),),
        )
        task1 = asyncio.create_task(competing_rotation())
        task2 = asyncio.create_task(competing_rotation())

        # Observe pg_stat_activity: both competing rotations must reach and block on the row lock on auth_sessions
        waiting = 0
        for _ in range(500):
            await asyncio.sleep(0.02)
            waiting = observer.execute(
                "SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
                " AND query LIKE 'DELETE FROM auth_sessions%%'"
            ).fetchone()[0]
            if waiting >= 2:
                break
        assert waiting >= 2, "Both competing rotating requests must block on auth_sessions row lock simultaneously"
        holder.commit()

        rot_res1, rot_res2 = await asyncio.gather(task1, task2)

    assert rot_res1.status_code == 200
    assert rot_res2.status_code == 200

    new_session1 = rot_res1.cookies.get("session")
    new_session2 = rot_res2.cookies.get("session")
    new_csrf1 = rot_res1.cookies.get("csrf_token")
    new_csrf2 = rot_res2.cookies.get("csrf_token")

    # Invariant: Both issued new, non-empty, distinct sessions and CSRF tokens
    assert new_session1 is not None and new_session2 is not None
    assert new_session1 != old_session and new_session2 != old_session
    assert new_session1 != new_session2, "Both rotations must generate distinct session tokens"

    assert new_csrf1 is not None and new_csrf2 is not None
    assert new_csrf1 != old_csrf and new_csrf2 != old_csrf
    assert new_csrf1 != new_csrf2, "Both rotations must generate distinct CSRF tokens"

    # Both replacement sessions authenticate the staff member on /api/me
    for tok in (new_session1, new_session2):
        async with new_client(app) as c_check:
            res_check = await c_check.get("/api/me", headers={"Cookie": f"session={tok}"})
            assert res_check.status_code == 200
            assert res_check.json()["staff"]["username"] == username

    # Invariant: Old session must be completely revoked from auth_sessions table
    with psycopg.connect(dsn) as conn:
        row = conn.execute(
            "SELECT 1 FROM auth_sessions WHERE token_hash = %s",
            (auth.token_hash(old_session),),
        ).fetchone()
        assert row is None, "Old session token must be revoked post-rotation"

    # Invariant: Replaying the old session cookie fails closed with not_signed_in
    async with new_client(app) as c_replay:
        replay_res = await c_replay.get("/api/classes", headers={"Cookie": f"session={old_session}"})
        assert replay_res.status_code == 401
        assert replay_res.json() == {"error": "not_signed_in"}


# ==============================================================================
# 4. Input fuzzing on prompt submission, student names, and JSON payloads
# ==============================================================================


def test_student_name_unicode_and_boundary_fuzzing():
    """clean_student_name rejects control, surrogate, private-use, unassigned, and invalid lengths."""
    clean = students.clean_student_name

    # 1. Length boundaries
    assert clean("A") == "A"
    assert clean("A" * 60) == "A" * 60

    with pytest.raises(auth.HTTPError) as exc:
        clean("")
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    with pytest.raises(auth.HTTPError) as exc:
        clean("   \t\n  ")
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    with pytest.raises(auth.HTTPError) as exc:
        clean("A" * 61)
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    with pytest.raises(auth.HTTPError) as exc:
        clean(12345)
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    # 2. Non-whitespace control characters (Cc) must be refused
    for ch in ("\x00", "\x01", "\x07", "\x1b", "\x7f", "\x80", "\x9f"):
        with pytest.raises(auth.HTTPError) as exc:
            clean(f"Student{ch}Name")
        assert exc.value.status == 400 and exc.value.code == "bad_name"

    # 3. Whitespace control characters are collapsed safely into single spaces
    assert clean("Student\r\nName") == "Student Name"
    assert clean("Student\tName") == "Student Name"

    # 4. Surrogates (Cs)
    with pytest.raises(auth.HTTPError) as exc:
        clean("Student\ud800Name")
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    # 5. Private Use (Co)
    with pytest.raises(auth.HTTPError) as exc:
        clean("Student\ue000Name")
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    # 6. Unassigned (Cn)
    with pytest.raises(auth.HTTPError) as exc:
        clean("Student\u0378Name")
    assert exc.value.status == 400 and exc.value.code == "bad_name"

    # 7. Format characters (Cf) are allowed for legitimate script joiners, zero-width spaces, and directional marks
    assert unicodedata.category("\u200b") == "Cf"  # ZERO WIDTH SPACE
    assert unicodedata.category("\u202e") == "Cf"  # RIGHT-TO-LEFT OVERRIDE
    assert clean("Alice\u200bBob") == "Alice\u200bBob"
    assert clean("Alice\u202eBob") == "Alice\u202eBob"
    assert clean("فاطمة\u200cزهرا") == "فاطمة\u200cزهرا"  # ZWNJ in Persian/Arabic
    assert clean("Tane\u200dMahuta") == "Tane\u200dMahuta"  # ZWJ

    # 8. Multi-byte international text & accents
    assert clean("Tāne Mahuta") == "Tāne Mahuta"
    assert clean("山田 太郎") == "山田 太郎"
    assert clean("Éléonore-Noémie") == "Éléonore-Noémie"
    assert clean("   Alice     Bob   ") == "Alice Bob"


async def test_student_join_fuzzing_via_api(client, teacher):
    """API-level fuzzing on student join parameters."""
    session_id, roster = await new_lesson_with_roster(teacher, count=1)
    valid_code = roster[0]["code"]

    # 1. Invalid code formats
    for bad_code in ("", "123", "1234567", "abcdef", "123 45", "12-34-56", None, 123456):
        res = await post(client, "/api/join", {"code": bad_code, "name": "Student A"})
        assert res.status_code in (400, 404)
        assert res.status_code != 500

    # 2. Invalid / fuzzed names with a valid code
    for bad_name in ("", "   ", "A" * 61, "Student\x00Null", "Student\x1bEsc", None, ["List"]):
        res = await post(client, "/api/join", {"code": valid_code, "name": bad_name})
        assert res.status_code == 400
        assert res.json() == {"error": "bad_name"}


async def test_prompt_submission_boundaries(app, teacher, dsn, monkeypatch):
    """Prompt submission accepts valid boundaries (4000 chars, unicode, Cf) and rejects over-limit/empty."""
    model_name = "security-test-model"
    # Seed worker and model in database so a live conversation can be created
    with psycopg.connect(dsn) as conn:
        generation = conn.execute("SELECT value->>'generation' FROM settings WHERE key = 'join_token'").fetchone()[0]
        conn.execute(
            "INSERT INTO workers (id, address, api_key, models, capacity, last_heartbeat, token_generation)"
            " VALUES (%s, %s, %s, %s, 4, now(), %s)"
            " ON CONFLICT (id) DO UPDATE SET last_heartbeat = now()",
            ("00000000-0000-4000-8000-000000000099", "http://test-worker:8081", "key-sec",
             json.dumps([model_name]), int(generation)),
        )
        conn.execute(
            "INSERT INTO models (name, enabled) VALUES (%s, true)"
            " ON CONFLICT (name) DO UPDATE SET enabled = true",
            (model_name,),
        )

    # Stub guard check and streaming reply so the endpoint tests prompt boundaries independently of shared settings/workers
    async def dummy_check(app, ctx):
        return guard.Verdict(
            action="allow",
            raw_response="Safety: Safe\nCategories: None",
            safe=True,
            duration_ms=10,
        )

    monkeypatch.setattr(guard, "check_prompt", dummy_check)

    async def dummy_reply(app, pool, ctx, student, lesson_id, model, body):
        yield chat.sse("done", {})

    monkeypatch.setattr(chat, "reply", dummy_reply)

    session_id, roster = await new_lesson_with_roster(teacher, count=1)
    code = roster[0]["code"]

    student = new_client(app)
    try:
        join_res = await post(student, "/api/join", {"code": code, "name": "Prompt Boundary Student"})
        assert join_res.status_code == 200

        # Start a real conversation
        convo_res = await post(student, "/api/student/conversations", {"model": model_name})
        assert convo_res.status_code == 201, convo_res.text
        convo_id = convo_res.json()["id"]

        # 1. Accepted boundary: Exactly 4000 characters (MAX_TEXT)
        assert chat.MAX_TEXT == 4000
        boundary_text = "A" * 4000
        res_4000 = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": boundary_text})
        assert res_4000.status_code == 200

        # Verify stored message in Postgres
        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT text, status FROM messages WHERE conversation_id = %s ORDER BY id DESC LIMIT 1",
                (convo_id,),
            ).fetchone()
            assert row is not None
            assert len(row[0]) == 4000
            assert row[0] == boundary_text

        # 2. Accepted Unicode, Zero-Width Space (U+200B), and RTL Override (U+202E)
        unicode_prompt = "Māori: Kia ora! \u200b RTL: \u202e Reversed text \u202c 日本語: 教室 🚀"
        res_unicode = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": unicode_prompt})
        assert res_unicode.status_code == 200

        with psycopg.connect(dsn) as conn:
            row = conn.execute(
                "SELECT text FROM messages WHERE conversation_id = %s ORDER BY id DESC LIMIT 1",
                (convo_id,),
            ).fetchone()
            assert row[0] == unicode_prompt

        # 3. Rejected boundary: 4001 characters
        over_max = "A" * 4001
        res_over = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": over_max})
        assert res_over.status_code == 400
        assert res_over.json() == {"error": "bad_text"}

        # 4. Empty / whitespace-only text
        for bad_text in ("", "   ", "   \t\n  "):
            res = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": bad_text})
            assert res.status_code == 400
            assert res.json() == {"error": "bad_text"}

        # 5. Non-string text
        for bad_text in (12345, None, ["hello"], {"key": "val"}):
            res = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": bad_text})
            assert res.status_code == 400
            assert res.json() == {"error": "bad_text"}

        # 6. Malformed Unicode with JSON-escaped lone surrogates fails closed with 400 bad_text, never 500
        res_surrogate = await student.post(
            f"/api/student/conversations/{convo_id}/messages",
            content=b'{"text": "hello\\ud800world"}',
            headers={"Content-Type": "application/json", **await csrf(student)},
        )
        assert res_surrogate.status_code == 400
        assert res_surrogate.json() == {"error": "bad_text"}

        # 7. Control characters: embedded NUL byte fails closed with 400 bad_text, never 503 or 500
        res_nul_raw = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": "hello\x00world"})
        assert res_nul_raw.status_code == 400
        assert res_nul_raw.json() == {"error": "bad_text"}

        res_nul_escaped = await student.post(
            f"/api/student/conversations/{convo_id}/messages",
            content=b'{"text": "hello\\u0000world"}',
            headers={"Content-Type": "application/json", **await csrf(student)},
        )
        assert res_nul_escaped.status_code == 400
        assert res_nul_escaped.json() == {"error": "bad_text"}

        # 8. Oversized request body exceeding MAX_BODY (64KB)
        huge_body = "A" * 65537
        res_huge = await post(student, f"/api/student/conversations/{convo_id}/messages", {"text": huge_body})
        assert res_huge.status_code in (400, 413)
        assert res_huge.status_code != 500
    finally:
        with psycopg.connect(dsn) as conn:
            conn.execute("DELETE FROM workers WHERE id = '00000000-0000-4000-8000-000000000099'")
            conn.execute("DELETE FROM models WHERE name = %s", (model_name,))
        await student.aclose()


async def test_malformed_json_payloads(app, client, teacher):
    """Malformed JSON payloads, primitives, and arrays fail closed with 400/413 and never 500."""
    app.state.limiter.reset()
    app.state.join_limiter.reset()
    app.state.join_total_limiter.reset()
    endpoints = [(client, "/api/login"), (teacher, "/api/classes"), (client, "/api/join")]

    for target_client, path in endpoints:
        csrf_hdrs = await csrf(target_client)

        # Non-JSON syntax
        res = await target_client.post(
            path,
            content=b"this is totally not json {[[",
            headers={"Content-Type": "application/json", **csrf_hdrs},
        )
        assert res.status_code == 400
        assert res.json() == {"error": "bad_request"}

        # JSON array instead of object
        res = await target_client.post(
            path,
            content=b"[1, 2, 3]",
            headers={"Content-Type": "application/json", **csrf_hdrs},
        )
        assert res.status_code == 400
        assert res.json() == {"error": "bad_request"}

        # JSON primitive (string, number, boolean)
        for prim in (b'"plain_string"', b"12345", b"true", b"null"):
            res = await target_client.post(
                path,
                content=prim,
                headers={"Content-Type": "application/json", **csrf_hdrs},
            )
            assert res.status_code == 400
            assert res.json() == {"error": "bad_request"}

        # Body exceeding MAX_BODY (64KB)
        oversized = b"{" + b'"a": "b",' * 8000 + b'"c": "d"}'
        assert len(oversized) > auth.MAX_BODY
        res = await target_client.post(
            path,
            content=oversized,
            headers={"Content-Type": "application/json", **csrf_hdrs},
        )
        assert res.status_code == 413
        assert res.json() == {"error": "too_large"}

    # Regression: Lone surrogates and embedded NUL characters fail closed with 400 bad_request, never 500
    app.state.limiter.reset()
    app.state.join_limiter.reset()
    app.state.join_total_limiter.reset()
    login_csrf = await csrf(client)
    for bad_payload in (
        b'{"username": "user\\ud800name", "password": "wrongpassword"}',
        b'{"username": "validuser", "password": "pwd\\ud800"}',
        b'{"username": "user\\u0000name", "password": "wrongpassword"}',
        b'{"username": "validuser", "password": "pwd\\u0000"}',
    ):
        res = await client.post(
            "/api/login",
            content=bad_payload,
            headers={"Content-Type": "application/json", **login_csrf},
        )
        assert res.status_code == 400
        assert res.json() == {"error": "bad_request"}

    # NUL byte in JSON body dictionary
    res_nul = await post(client, "/api/login", {"username": "user\x00name", "password": "pwd"})
    assert res_nul.status_code == 400
    assert res_nul.json() == {"error": "bad_request"}

    # Join endpoint with surrogate or NUL in student name fails closed
    join_csrf = await csrf(client)
    for bad_join in (
        b'{"code": "123456", "name": "student\\ud800name"}',
        b'{"code": "123456", "name": "student\\u0000name"}',
    ):
        res = await client.post(
            "/api/join",
            content=bad_join,
            headers={"Content-Type": "application/json", **join_csrf},
        )
        assert res.status_code == 400
        assert res.json() in ({"error": "bad_request"}, {"error": "bad_name"})


# ==============================================================================
# 5. Invariants: Fail-closed, zero detail leaked, zero 500s, clean pool release
# ==============================================================================


async def test_fail_closed_and_zero_server_detail_leaked(app, client, teacher):
    """Error responses maintain strict JSON shape with exact HTTP status codes and zero detail leakage."""
    csrf_headers = await csrf(teacher)

    # 400: Malformed JSON syntax with valid CSRF
    res_400 = await teacher.post(
        "/api/classes",
        content=b"{",
        headers={"Content-Type": "application/json", **csrf_headers},
    )
    assert res_400.status_code == 400
    assert res_400.json() == {"error": "bad_request"}

    # 401: Unauthenticated request to protected endpoint
    async with new_client(app) as anon:
        res_401 = await anon.get("/api/classes")
    assert res_401.status_code == 401
    assert res_401.json() == {"error": "not_signed_in"}

    # 403: Invalid CSRF token on state-changing request
    res_403 = await teacher.post("/api/classes", json={"name": "x"}, headers={"X-CSRF-Token": "bad"})
    assert res_403.status_code == 403
    assert res_403.json() == {"error": "csrf"}

    # 404: Nonexistent resource
    res_404 = await teacher.get("/api/sessions/999999")
    assert res_404.status_code == 404
    assert res_404.json() == {"error": "not_found"}

    for res in (res_400, res_401, res_403, res_404):
        body = res.json()
        assert "error" in body, f"Response {res.status_code} must contain 'error' key"

        # Check absence of internal leakages in body
        text = res.text
        assert "Traceback" not in text
        assert "psycopg" not in text
        assert "auth_sessions" not in text
        assert "SELECT" not in text
        assert "syntax error" not in text
        assert "/Users/" not in text
        assert ".py" not in text

        # Check absence of framework details in headers
        server_header = res.headers.get("server", "").lower()
        assert "uvicorn" not in server_header
        assert "starlette" not in server_header


async def test_server_header_suppression_at_http_boundary(app):
    """Uvicorn server suppresses server header at the real HTTP boundary when server_header=False."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        lifespan="off",
        access_log=False,
        log_level="error",
        server_header=False,
    )
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(50):
            if server.started:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("uvicorn did not start")

        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as c:
            # 1. Normal endpoint
            res = await c.get("/healthz")
            assert res.status_code == 200
            assert res.headers.get("server") is None

            # 2. Malformed request / error endpoint
            res_err = await c.post("/api/classes", content=b"{malformed_json", headers={"Content-Type": "application/json"})
            assert res_err.status_code in (400, 401, 403)
            assert res_err.headers.get("server") is None

        # Verify Dockerfile production command line contains --no-server-header
        dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
        assert "--no-server-header" in dockerfile
    finally:
        server.should_exit = True
        await task


async def test_database_error_logging_invariants(caplog):
    """PoolErrorDetails strips queries and DETAIL from logs, retaining exception class only."""
    sensitive_detail = "DETAIL: Key (student_id)=(999) already exists; QUERY: INSERT INTO students..."

    # 1. Directly test PoolErrorDetails filter on LogRecord
    record = logging.LogRecord(
        name="app",
        level=logging.ERROR,
        pathname="",
        lineno=0,
        msg="database query failed: %s",
        args=(psycopg.OperationalError(sensitive_detail),),
        exc_info=None,
    )
    PoolErrorDetails().filter(record)
    formatted = record.msg % record.args if record.args else record.msg
    assert "OperationalError" in formatted
    assert sensitive_detail not in formatted
    assert "QUERY:" not in formatted
    assert "student_id" not in formatted

    # 2. Test application exception handler mapping psycopg OperationalError to 503 unavailable
    async def fail_route(_req: Request):
        raise psycopg.OperationalError(sensitive_detail)

    app = create_app("postgresql://unused")
    app.router.routes.append(Route("/_test/db-error", fail_route))

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    caplog.set_level(logging.ERROR, logger="app")

    async with httpx.AsyncClient(transport=transport, base_url="https://test") as c:
        res = await c.get("/_test/db-error")

    assert res.status_code == 503
    assert res.json() == {"error": "unavailable"}

    # In log: exception class name is logged, sensitive query/detail is never leaked
    assert "OperationalError" in caplog.text
    assert sensitive_detail not in caplog.text
    assert "QUERY:" not in caplog.text
    assert "student_id" not in caplog.text


def test_access_log_strips_sensitive_query_strings():
    """DropQueryStrings strips query strings containing codes or tokens from access logs."""
    filter_instance = DropQueryStrings()
    record = logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname="",
        lineno=0,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("10.0.0.1", "GET", "/api/admin/students?q=SECRET_CODE_123456", "1.1", 200),
        exc_info=None,
    )

    filter_instance.filter(record)
    formatted = record.args[2]
    assert "SECRET_CODE_123456" not in formatted
    assert formatted == "/api/admin/students"


async def test_clean_database_pool_release_after_security_fuzzing(app, teacher):
    """Connection pool returns all connections cleanly with zero leaked connections after database operations and rollbacks."""
    pool = app.state.pool

    # Create a dedicated class and session to test transactions and rollbacks
    class_res = await post(teacher, "/api/classes", {"name": "Pool Test Class", "instructions": "test", "message_limit": 10})
    assert class_res.status_code == 201
    class_id = class_res.json()["id"]

    sess_res = await post(teacher, f"/api/classes/{class_id}/sessions", {"count": 2})
    assert sess_res.status_code == 201
    session_id = sess_res.json()["id"]

    roster = (await teacher.get(f"/api/sessions/{session_id}")).json()["roster"]
    code = roster[0]["code"]
    with psycopg.connect(app.state.pool.conninfo) as conn:
        all_live_codes = {r[0] for r in conn.execute("SELECT code FROM students WHERE NOT removed").fetchall()}
    invalid_code = "000000" if "000000" not in all_live_codes else "999999"

    student = new_client(app)
    anon = new_client(app)
    try:
        join_res = await post(student, "/api/join", {"code": code, "name": "Pool Tester"})
        assert join_res.status_code == 200

        # Send a burst of requests exercising real database queries, rollbacks, and error paths
        for _ in range(5):
            # 1. Authenticated database read
            r1 = await teacher.get(f"/api/sessions/{session_id}")
            assert r1.status_code == 200

            # 2. Authenticated database query for missing record (404)
            r2 = await teacher.get("/api/sessions/999999")
            assert r2.status_code == 404

            # 3. Authenticated student database read
            r3 = await student.get("/api/student/models")
            assert r3.status_code == 200

            # 4. Authenticated database write transaction rollback (duplicate live session start on same class)
            r4 = await post(teacher, f"/api/classes/{class_id}/sessions", {"count": 1})
            assert r4.status_code == 409
            assert r4.json() == {"error": "live_session"}

            # 5. Database transaction rollback on student join with non-existent code (404)
            r5 = await post(anon, "/api/join", {"code": invalid_code, "name": "Nonexistent"})
            assert r5.status_code == 404
            assert r5.json() == {"error": "bad_code"}

            # 6. Unauthenticated request to authenticated endpoint returns 401
            r6 = await anon.get("/api/classes")
            assert r6.status_code == 401
    finally:
        await student.aclose()
        await anon.aclose()

    # Verify pool statistics: no queries queued or waiting
    stats = pool.get_stats()
    assert stats.get("requests_waiting", 0) == 0

    # Acquire ALL connections across total pool capacity simultaneously to prove zero leaked connections
    barrier = asyncio.Barrier(pool.max_size)

    async def acquire_and_hold():
        async with pool.connection() as conn:
            assert conn.info.transaction_status == psycopg.pq.TransactionStatus.IDLE
            await barrier.wait()
            cur = await conn.execute("SELECT 1")
            assert (await cur.fetchone()) == (1,)

    await asyncio.wait_for(
        asyncio.gather(*(acquire_and_hold() for _ in range(pool.max_size))),
        timeout=10.0,
    )

    # Health check passes cleanly proving responsive pool
    async with new_client(app) as health_client:
        health_res = await health_client.get("/healthz")
        assert health_res.status_code == 200
        assert health_res.json()["status"] == "ok"
        assert health_res.json()["db"] == "ok"
