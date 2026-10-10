"""Staff accounts: first-run setup, login, cookie sessions, roles and CSRF.

See docs/architecture.md "Identity and access".
"""

import asyncio
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
from collections import deque
from http.cookies import SimpleCookie

from psycopg.types.json import Jsonb
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

log = logging.getLogger("app.auth")

SESSION_COOKIE = "session"
CSRF_COOKIE = "csrf_token"
CSRF_HEADER = "x-csrf-token"
SESSION_HOURS = 12
# Paths authenticated by a bearer token, never by cookie, so they skip the
# cookie CSRF check. Never exempt a path because a request has no cookies:
# SameSite=Lax strips cookies from cross-site POSTs.
CSRF_EXEMPT = frozenset({"/api/workers/heartbeat"})
SAFE_METHODS = frozenset({"GET", "HEAD"})
SESSION_KEY = "classroom_ai.session"  # request scope key for the loaded session

ROLES = ("admin", "teacher")
USERNAME = re.compile(r"^[a-z0-9._-]{1,64}$")
PASSWORD_MIN, PASSWORD_MAX = 10, 256

SETUP_KEY = "setup_code"
SETUP_LOCK_KEY = 7_311_006
SETUP_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


class HTTPError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code


async def http_error(request: Request, exc: HTTPError) -> JSONResponse:
    return JSONResponse({"error": exc.code}, status_code=exc.status)


# Passwords: scrypt at OWASP's preferred cost. Each hash takes 128 MiB, so at
# most two run at once. The permit is taken inside the worker thread, so a
# cancelled request cannot free it while its hash is still running.

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**17, 8, 1
SCRYPT_MAXMEM = 2**28
_hash_slots = threading.BoundedSemaphore(2)


def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    with _hash_slots:
        return hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, maxmem=SCRYPT_MAXMEM, dklen=32
        )


async def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = await asyncio.to_thread(_scrypt, password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${key.hex()}"


async def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, key = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = bytes.fromhex(key)
        actual = await asyncio.to_thread(_scrypt, password, bytes.fromhex(salt), int(n), int(r), int(p))
    except ValueError:
        return False
    return hmac.compare_digest(actual, expected)


_dummy_hash: str | None = None


async def prepare() -> None:
    """Build the dummy hash at startup, so even the first unknown-user login
    costs exactly one hash."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = await hash_password(secrets.token_hex(16))


async def dummy_verify(password: str) -> None:
    """Spend the same time as a real check, so timing does not reveal usernames."""
    await prepare()
    await verify_password(password, _dummy_hash)


# Rate limit for login and setup attempts, per client address.


class RateLimiter:
    """Sliding window, counted when an attempt is admitted.

    ponytail: in memory and per process, cleared wholesale above max_keys.
    Fine for one app process on a school LAN; move it to Postgres if the app
    runs several processes.
    """

    def __init__(self, limit: int = 10, window: float = 60.0, clock=time.monotonic, max_keys: int = 10_000):
        self.limit, self.window, self.clock, self.max_keys = limit, window, clock, max_keys
        self.hits: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = self.clock()
        for k in list(self.hits):
            q = self.hits[k]
            while q and q[0] <= now - self.window:
                q.popleft()
            if not q:
                del self.hits[k]
        if len(self.hits) >= self.max_keys:
            self.hits.clear()
        q = self.hits.setdefault(key, deque())
        if len(q) >= self.limit:
            return False
        q.append(now)
        return True

    def forgive(self, key: str) -> None:
        """Drop this key's newest attempt: for limits on failures, the caller
        counts every attempt when admitted and forgives the ones that succeed."""
        q = self.hits.get(key)
        if q:
            q.pop()

    def reset(self) -> None:
        self.hits.clear()


def client_ip(request: Request) -> str:
    # Caddy (no trusted_proxies) replaces any client-supplied X-Forwarded-For
    # with the real peer, and the app publishes no port, so this is trusted.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def check_rate(request: Request) -> None:
    if not request.app.state.limiter.allow(client_ip(request)):
        raise HTTPError(429, "too_many_attempts")


# Cookies and sessions.


def is_https(request: Request) -> bool:
    return request.headers.get("x-forwarded-proto") == "https" or request.url.scheme == "https"


def token_hash(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()


def set_cookie(response: Response, request: Request, name: str, value: str, http_only: bool) -> None:
    response.set_cookie(
        name, value, path="/", httponly=http_only, secure=is_https(request), samesite="lax",
        max_age=SESSION_HOURS * 3600 if name == SESSION_COOKIE else None,
    )


def clear_cookie(response: Response, request: Request, name: str) -> None:
    response.delete_cookie(
        name, path="/", httponly=name == SESSION_COOKIE, secure=is_https(request), samesite="lax"
    )


async def start_session(
    conn, request: Request, response: Response, *, staff_id: int | None = None, student_id: int | None = None
) -> None:
    """Sign this device in as one staff member or one student. Any session the
    device already had is ended: a device is a staff device or a student one."""
    # Replacing a live session is a signed-in request like any other: it
    # needs that session's own CSRF token, not just a matching cookie.
    # Callers load the session before opening `conn`, so this is a cache hit
    # and never takes a second pool connection.
    existing = await load_session(request)
    if existing is not None:
        check_session_csrf(request, existing)
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    await conn.execute("DELETE FROM auth_sessions WHERE expires_at < now()")
    if old := request.cookies.get(SESSION_COOKIE):
        await conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash(old),))
    await conn.execute(
        "INSERT INTO auth_sessions (token_hash, staff_id, student_id, csrf_token, expires_at)"
        " VALUES (%s, %s, %s, %s, now() + make_interval(hours => %s))",
        (token_hash(token), staff_id, student_id, csrf, SESSION_HOURS),
    )
    set_cookie(response, request, SESSION_COOKIE, token, http_only=True)
    set_cookie(response, request, CSRF_COOKIE, csrf, http_only=False)


STUDENT_COLUMNS = (
    "st.id, st.name, st.code, st.lesson_session_id, l.state, c.name, c.message_limit"
)
# A student cookie works only while the student is bound and not removed and
# the lesson session is not closed, even if a delete of the cookie row is missed.
STUDENT_LIVE = "st.name IS NOT NULL AND NOT st.removed AND l.state <> 'closed'"


def student_from_row(row) -> dict:
    keys = ("id", "name", "code", "lesson_session_id", "state", "class_name", "message_limit")
    return {"kind": "student", **dict(zip(keys, row))}


async def find_session(pool, token: str | None) -> dict | None:
    """The staff member or student a session cookie belongs to, or None."""
    if not token:
        return None
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT s.id, s.username, s.role, a.csrf_token FROM auth_sessions a"
            " JOIN staff s ON s.id = a.staff_id"
            " WHERE a.token_hash = %s AND a.expires_at > now()",
            (token_hash(token),),
        )
        if row := await cur.fetchone():
            return {"kind": "staff", "id": row[0], "username": row[1], "role": row[2], "csrf": row[3]}
        cur = await conn.execute(
            f"SELECT {STUDENT_COLUMNS}, a.csrf_token FROM auth_sessions a"
            " JOIN students st ON st.id = a.student_id"
            " JOIN lesson_sessions l ON l.id = st.lesson_session_id"
            " JOIN classes c ON c.id = l.class_id"
            f" WHERE a.token_hash = %s AND a.expires_at > now() AND {STUDENT_LIVE}",
            (token_hash(token),),
        )
        if row := await cur.fetchone():
            return {**student_from_row(row[:-1]), "csrf": row[-1]}
    return None


async def load_session(request: Request) -> dict | None:
    """The signed-in staff member or student, if any. Cached on the request
    scope so the CSRF middleware can reuse it."""
    if SESSION_KEY not in request.scope:
        request.scope[SESSION_KEY] = await find_session(request.app.state.pool, request.cookies.get(SESSION_COOKIE))
    return request.scope[SESSION_KEY]


def check_session_csrf(request: Request, session: dict) -> None:
    """A signed-in state-changing request must present this session's token."""
    if request.method not in SAFE_METHODS and not same_token(request.headers.get(CSRF_HEADER), session["csrf"]):
        raise HTTPError(403, "csrf")


def same_token(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


async def require_staff(request: Request, role: str = "teacher") -> dict:
    """The signed-in staff member, or 401/403. An admin passes a teacher check.

    For state-changing requests the CSRF header must also match the token
    bound to this session, not only the cookie.
    """
    staff = await load_session(request)
    if staff is None or staff["kind"] != "staff":
        raise HTTPError(401, "not_signed_in")
    check_session_csrf(request, staff)
    if role == "admin" and staff["role"] != "admin":
        raise HTTPError(403, "forbidden")
    return staff


async def require_student(request: Request, sending: bool = False) -> dict:
    """The signed-in student, or 401. When sending a message, a paused lesson
    session refuses with 409 (R4.6)."""
    student = await load_session(request)
    if student is None or student["kind"] != "student":
        raise HTTPError(401, "not_signed_in")
    check_session_csrf(request, student)
    if sending and student["state"] == "paused":
        raise HTTPError(409, "paused")
    return student


def public(staff: dict | None) -> dict:
    if staff is None or staff["kind"] != "staff":
        return {"staff": None}
    return {"staff": {"username": staff["username"], "role": staff["role"]}}


class CSRFMiddleware:
    """Double-submit check on /api/*: a state-changing request must carry the
    X-CSRF-Token header equal to the csrf_token cookie. A cross-site page can
    neither read the cookie nor send a custom header without a CORS preflight,
    which the app never grants.

    It also keeps the browser's csrf_token cookie right: when a response does
    not set one itself and the request's cookie is missing or (with a
    session) not the session's token, it sets the session's token, or a
    random one before sign-in.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        cookie = request.cookies.get(CSRF_COOKIE)
        if (
            request.method not in SAFE_METHODS
            and scope["path"] not in CSRF_EXEMPT
            and not same_token(request.headers.get(CSRF_HEADER), cookie)
        ):
            response = JSONResponse({"error": "csrf"}, status_code=403)
            return await response(scope, receive, send)

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                sets_csrf = any(
                    k == b"set-cookie" and CSRF_COOKIE in SimpleCookie(v.decode())
                    for k, v in headers
                )
                if not sets_csrf and scope["path"] not in CSRF_EXEMPT:
                    try:
                        wanted = await self.wanted_token(request, cookie)
                    except Exception:
                        # Database trouble: answer without fixing the cookie;
                        # the next request will try again.
                        log.warning("could not choose a CSRF cookie", exc_info=True)
                        wanted = None
                    if wanted is not None:
                        response = Response()
                        set_cookie(response, request, CSRF_COOKIE, wanted, http_only=False)
                        headers += [(k, v) for k, v in response.raw_headers if k == b"set-cookie"]
                        message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    @staticmethod
    async def wanted_token(request: Request, cookie: str | None) -> str | None:
        if SESSION_KEY in request.scope:
            staff = request.scope[SESSION_KEY]
        elif cookie is None and request.cookies.get(SESSION_COOKIE):
            staff = await find_session(request.app.state.pool, request.cookies[SESSION_COOKIE])
        else:
            staff = None
        if staff is not None:
            return None if cookie == staff["csrf"] else staff["csrf"]
        return None if cookie else secrets.token_urlsafe(32)


# Request bodies.


MAX_BODY = 64 * 1024


async def json_body(request: Request) -> dict:
    """The request body as a JSON object (an empty body is {}), or 400/413."""
    raw = await request.body()
    if len(raw) > MAX_BODY:
        raise HTTPError(413, "too_large")
    try:
        body = json.loads(raw) if raw else {}
    except ValueError:
        raise HTTPError(400, "bad_request") from None
    if not isinstance(body, dict):
        raise HTTPError(400, "bad_request")
    return body


async def json_fields(request: Request, *names: str) -> dict[str, str]:
    body = await json_body(request)
    if not all(isinstance(body.get(n), str) for n in names):
        raise HTTPError(400, "bad_request")
    return {n: body[n] for n in names}


def clean_username(value: str) -> str:
    username = value.strip().lower()
    if not USERNAME.match(username):
        raise HTTPError(400, "bad_username")
    return username


def check_password(value: str) -> str:
    if not PASSWORD_MIN <= len(value) <= PASSWORD_MAX:
        raise HTTPError(400, "bad_password")
    return value


async def audit(conn, staff_id: int | None, username: str, action: str, detail: dict) -> None:
    await conn.execute(
        "INSERT INTO audit (staff_id, username, action, detail) VALUES (%s, %s, %s, %s)",
        (staff_id, username, action, Jsonb(detail)),
    )


# First-run setup.


def new_setup_code() -> str:
    return "".join(secrets.choice(SETUP_ALPHABET) for _ in range(12))


def normalise_code(value: str) -> str:
    return re.sub(r"[\s-]", "", value).upper()


async def ensure_setup_code(pool) -> None:
    """While no admin exists, make sure a setup code exists and log it.

    Shares an advisory lock with the setup endpoint, so a start that overlaps
    a completed setup cannot bring the code back.
    """
    async with pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK_KEY,))
        cur = await conn.execute("SELECT 1 FROM staff WHERE role = 'admin' LIMIT 1")
        if await cur.fetchone():
            return
        await conn.execute(
            "INSERT INTO settings (key, value) VALUES (%s, %s) ON CONFLICT (key) DO NOTHING",
            (SETUP_KEY, Jsonb(new_setup_code())),
        )
        cur = await conn.execute("SELECT value FROM settings WHERE key = %s", (SETUP_KEY,))
        code = (await cur.fetchone())[0]
    shown = "-".join(code[i : i + 4] for i in range(0, len(code), 4))
    log.warning("setup code: %s (open /setup on this server to create the admin account)", shown)


async def setup_status(request: Request) -> JSONResponse:
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute("SELECT 1 FROM staff WHERE role = 'admin' LIMIT 1")
        needed = await cur.fetchone() is None
    return JSONResponse({"needed": needed})


async def setup(request: Request) -> JSONResponse:
    check_rate(request)
    body = await json_fields(request, "code", "username", "password")
    username, password = clean_username(body["username"]), check_password(body["password"])
    # Check the code before spending a 128 MiB hash on it, then again under the lock.
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute("SELECT value FROM settings WHERE key = %s", (SETUP_KEY,))
        row = await cur.fetchone()
    if row is None:
        raise HTTPError(409, "setup_done")
    if not same_token(normalise_code(body["code"]), row[0]):
        raise HTTPError(403, "wrong_code")
    password_hash = await hash_password(password)
    response = JSONResponse({"staff": {"username": username, "role": "admin"}})
    await load_session(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock(%s)", (SETUP_LOCK_KEY,))
        cur = await conn.execute("SELECT value FROM settings WHERE key = %s", (SETUP_KEY,))
        row = await cur.fetchone()
        cur = await conn.execute("SELECT 1 FROM staff WHERE role = 'admin' LIMIT 1")
        if row is None or await cur.fetchone():
            raise HTTPError(409, "setup_done")
        if not same_token(normalise_code(body["code"]), row[0]):
            raise HTTPError(403, "wrong_code")
        cur = await conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, 'admin') RETURNING id",
            (username, password_hash),
        )
        staff_id = (await cur.fetchone())[0]
        await conn.execute("DELETE FROM settings WHERE key = %s", (SETUP_KEY,))
        await audit(conn, staff_id, username, "staff.create", {"username": username, "role": "admin"})
        await start_session(conn, request, response, staff_id=staff_id)
    log.info("admin account created")
    return response


# Sign in and out.


async def me(request: Request) -> JSONResponse:
    return JSONResponse(public(await load_session(request)))


async def login(request: Request) -> JSONResponse:
    check_rate(request)
    body = await json_fields(request, "username", "password")
    username = body["username"].strip().lower()
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, username, role, password_hash FROM staff WHERE username = %s", (username,)
        )
        row = await cur.fetchone()
    if row is None:
        await dummy_verify(body["password"])
        raise HTTPError(401, "bad_login")
    if not await verify_password(body["password"], row[3]):
        raise HTTPError(401, "bad_login")
    response = JSONResponse({"staff": {"username": row[1], "role": row[2]}})
    await load_session(request)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        await start_session(conn, request, response, staff_id=row[0])
    return response


async def logout(request: Request) -> Response:
    """Succeeds with or without a session, so a stale tab can recover, and
    clears both cookies. With a live session the CSRF header must be that
    session's token, as for any signed-in request."""
    session = await load_session(request)
    if session is not None:
        check_session_csrf(request, session)
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        async with request.app.state.pool.connection() as conn:
            await conn.execute("DELETE FROM auth_sessions WHERE token_hash = %s", (token_hash(token),))
    response = Response(status_code=204)
    clear_cookie(response, request, SESSION_COOKIE)
    clear_cookie(response, request, CSRF_COOKIE)
    return response


# Staff accounts (admin only).


async def list_staff(request: Request) -> JSONResponse:
    await require_staff(request, "admin")
    async with request.app.state.pool.connection() as conn:
        cur = await conn.execute("SELECT username, role, created_at FROM staff ORDER BY username")
        rows = await cur.fetchall()
    return JSONResponse({"staff": [{"username": u, "role": r, "created_at": c.isoformat()} for u, r, c in rows]})


async def create_staff(request: Request) -> JSONResponse:
    admin = await require_staff(request, "admin")
    body = await json_fields(request, "username", "password", "role")
    username, password = clean_username(body["username"]), check_password(body["password"])
    if body["role"] not in ROLES:
        raise HTTPError(400, "bad_role")
    password_hash = await hash_password(password)
    async with request.app.state.pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, %s)"
            " ON CONFLICT (username) DO NOTHING RETURNING id",
            (username, password_hash, body["role"]),
        )
        if await cur.fetchone() is None:
            raise HTTPError(409, "username_taken")
        await audit(conn, admin["id"], admin["username"], "staff.create", {"username": username, "role": body["role"]})
    return JSONResponse({"username": username, "role": body["role"]}, status_code=201)


routes = [
    Route("/api/setup", setup_status, methods=["GET"]),
    Route("/api/setup", setup, methods=["POST"]),
    Route("/api/me", me, methods=["GET"]),
    Route("/api/login", login, methods=["POST"]),
    Route("/api/logout", logout, methods=["POST"]),
    Route("/api/staff", list_staff, methods=["GET"]),
    Route("/api/staff", create_staff, methods=["POST"]),
]
