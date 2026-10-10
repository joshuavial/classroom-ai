"""Admin endpoints for records: audit list, retention setting, export, delete, backup."""

import os
import subprocess
import uuid

import httpx
import psycopg
import pytest

from app.main import create_app
from tests.test_auth import csrf, password_hash, sign_in  # noqa: F401
from tests.test_backup import snapshot
from tests.test_records import NOW, populated_student

PG_DUMP_SHIM = """#!/bin/sh
# pg_dump -Fc -f <file> <dsn>, run inside the test Postgres container.
out=$3
db=$(printf %s "$4" | sed -n "s/.*dbname=\\([^ ]*\\).*/\\1/p")
case "$*" in *password=*) echo "password on the command line" >&2; exit 3 ;; esac
[ -n "$PGPASSWORD" ] || { echo "no PGPASSWORD" >&2; exit 4; }
exec docker exec "$TEST_CONTAINER" pg_dump -Fc -U postgres -d "$db" > "$out"
"""


@pytest.fixture
async def env(empty_dsn, password_hash, tmp_path, monkeypatch):  # noqa: F811
    port = empty_dsn.rsplit(":", 1)[1].split("/")[0]
    names = subprocess.run(["docker", "ps", "--filter", "name=classroom-ai-test-", "--format", "{{.Names}}"],
                           check=True, capture_output=True, text=True).stdout.split()
    container = next(n for n in names if subprocess.run(
        ["docker", "port", n, "5432/tcp"], capture_output=True, text=True).stdout.strip().endswith(f":{port}"))
    (tmp_path / "pg_dump").write_text(PG_DUMP_SHIM)
    (tmp_path / "pg_dump").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")
    monkeypatch.setenv("TEST_CONTAINER", container)

    app = create_app(empty_dsn, clock=lambda: NOW)
    clients = []
    async with app.router.lifespan_context(app):
        async def client(role=None):
            c = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test")
            clients.append(c)
            if role:
                name = f"{role}-{uuid.uuid4().hex[:6]}"
                with psycopg.connect(empty_dsn) as conn:
                    conn.execute("INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, %s)",
                                 (name, password_hash, role))
                await sign_in(c, name)
                c.username = name
            return c

        yield app, empty_dsn, client
        for c in clients:
            await c.aclose()


def audit(dsn):
    with psycopg.connect(dsn) as conn:
        return conn.execute("SELECT username, action, detail FROM audit ORDER BY id").fetchall()


async def student(app):
    async with app.state.pool.connection() as conn:
        return await populated_student(conn)


ENDPOINTS = [("GET", "/api/admin/audit"), ("GET", "/api/admin/retention"), ("PUT", "/api/admin/retention"),
             ("GET", "/api/admin/students/1/export"), ("DELETE", "/api/admin/students/1"),
             ("POST", "/api/admin/backup"), ("GET", "/api/admin/students")]


@pytest.mark.parametrize("method,path", ENDPOINTS)
async def test_signed_out_and_teacher_refused(env, method, path):
    app, dsn, client = env
    await student(app)
    anon = await client()
    assert (await anon.request(method, path, json={"days": 5}, headers=await csrf(anon))).status_code == 401
    teacher = await client("teacher")
    r = await teacher.request(method, path, json={"days": 5}, headers=await csrf(teacher))
    assert r.status_code == 403
    assert audit(dsn) == []


@pytest.mark.parametrize("method,path", [e for e in ENDPOINTS if e[0] != "GET"])
async def test_changes_need_the_csrf_header(env, method, path):
    app, dsn, client = env
    await student(app)
    admin = await client("admin")
    assert (await admin.request(method, path, json={"days": 5})).status_code == 403
    assert audit(dsn) == []


async def test_retention_setting(env):
    app, dsn, client = env
    admin = await client("admin")
    assert (await admin.get("/api/admin/retention")).json() == {"days": 30}
    r = await admin.put("/api/admin/retention", json={"days": 14}, headers=await csrf(admin))
    assert r.json() == {"days": 14}
    assert (await admin.get("/api/admin/retention")).json() == {"days": 14}
    for bad in (0, 3651, "14", 1.5, True, None):
        r = await admin.put("/api/admin/retention", json={"days": bad}, headers=await csrf(admin))
        assert r.status_code == 400
    assert audit(dsn) == [(admin.username, "retention.set", {"days": 14})]


async def test_export_and_delete_one_student(env):
    app, dsn, client = env
    student_id, other = await student(app)
    admin = await client("admin")
    r = await admin.get(f"/api/admin/students/{student_id}/export")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == f'attachment; filename="student-{student_id}.json"'
    assert r.json()["conversations"][0]["messages"][0]["text"] == "What is a volcano?"
    assert (await admin.get("/api/admin/students/999/export")).status_code == 404
    assert (await admin.get("/api/admin/students/x/export")).status_code == 404
    r = await admin.delete(f"/api/admin/students/{student_id}", headers=await csrf(admin))
    assert r.json() == {"deleted": student_id}
    assert (await admin.delete(f"/api/admin/students/{student_id}", headers=await csrf(admin))).status_code == 404
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT id FROM students").fetchall() == [(other,)]
    rows = audit(dsn)
    assert [a for _, a, _ in rows] == ["student.export", "student.delete"]
    for _, _, detail in rows:
        assert set(detail) == {"student_id", "lesson_session_id"} and detail["student_id"] == student_id
    assert "volcano" not in str(rows) and "111111" not in str(rows)  # never text or codes


async def test_audit_list_newest_first_and_paged(env):
    app, dsn, client = env
    admin = await client("admin")
    with psycopg.connect(dsn) as conn:
        for i in range(105):
            conn.execute("INSERT INTO audit (username, action, detail) VALUES ('x', 'test', %s)",
                         (psycopg.types.json.Jsonb({"n": i}),))
    first = (await admin.get("/api/admin/audit")).json()["audit"]
    assert len(first) == 100 and first[0]["detail"] == {"n": 104}
    rest = (await admin.get(f"/api/admin/audit?before={first[-1]['id']}")).json()["audit"]
    assert [r["detail"]["n"] for r in rest] == [4, 3, 2, 1, 0]
    for bad in ("x", "0", "-1", str(2**63), "9" * 40):
        assert (await admin.get(f"/api/admin/audit?before={bad}")).status_code == 400


async def test_backup_download_is_a_restorable_dump(env, tmp_path, monkeypatch):
    app, dsn, client = env
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    await student(app)
    admin = await client("admin")
    before = snapshot(dsn)
    r = await admin.post("/api/admin/backup", headers=await csrf(admin))
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-disposition"] == 'attachment; filename="classroom-ai-20261010-120000.dump"'
    assert int(r.headers["content-length"]) == len(r.content) > 0
    assert r.content.startswith(b"PGDMP")
    assert not list(tmp_path.glob("backup-*"))
    assert [a for _, a, _ in audit(dsn)] == ["backup.download"]
    # It restores to what was there.
    container = os.environ["TEST_CONTAINER"]
    db = dsn.rsplit("/", 1)[1]
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("DROP SCHEMA public CASCADE")
        conn.execute("CREATE SCHEMA public AUTHORIZATION pg_database_owner")
    subprocess.run(["docker", "exec", "-i", container, "pg_restore", "--no-owner", "--exit-on-error",
                    "--single-transaction", "-U", "postgres", "-d", db], input=r.content, check=True)
    after = snapshot(dsn)
    before.pop("audit"), after.pop("audit")  # the download added its own row
    assert after == before


async def test_failed_backup_is_an_error_not_a_file(env, tmp_path, monkeypatch):
    app, dsn, client = env
    (tmp_path / "pg_dump").write_text("#!/bin/sh\nprintf partial > \"$3\"\nexit 1\n")
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    admin = await client("admin")
    r = await admin.post("/api/admin/backup", headers=await csrf(admin))
    assert (r.status_code, r.json()) == (500, {"error": "backup_failed"})
    assert not list(tmp_path.glob("backup-*"))
    assert audit(dsn) == []


async def test_backup_needs_post_with_csrf(env):
    app, dsn, client = env
    admin = await client("admin")
    assert (await admin.get("/api/admin/backup")).status_code == 405
    assert (await admin.post("/api/admin/backup")).status_code == 403
    assert audit(dsn) == []


async def test_student_list_includes_removed_and_searches(env):
    app, dsn, client = env
    student_id, other = await student(app)
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE students SET removed = true WHERE id = %s", (other,))
        conn.execute("UPDATE students SET name = %s WHERE id = %s", ("50%_off", student_id))
    admin = await client("admin")
    listed = (await admin.get("/api/admin/students")).json()["students"]
    assert [s["id"] for s in listed] == [other, student_id]  # newest first
    assert listed[0]["removed"] is True and listed[0]["name"] == "Ben"
    assert listed[1] | {"opened": None} == {
        "id": student_id, "code": "111111", "name": "50%_off", "removed": False,
        "lesson_session_id": listed[1]["lesson_session_id"], "opened": None, "class": "Class 1", "messages": 2}
    async def find(q):
        return [s["id"] for s in (await admin.get("/api/admin/students", params={"q": q})).json()["students"]]
    assert await find("ben") == [other]
    assert await find("222222") == [other]
    assert await find("%_") == [student_id]  # wildcards matched as typed
    assert await find("_") == [student_id]
    assert await find("nobody") == []
    assert (await admin.get("/api/admin/students", params={"q": "x" * 101})).status_code == 400
