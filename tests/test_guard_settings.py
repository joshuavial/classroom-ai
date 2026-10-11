import asyncio
import copy
import uuid

import httpx
import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.guard_settings import InvalidGuardSettings, load, save, validate_document
from app.main import create_app
from tests.test_auth import csrf, password_hash, sign_in  # noqa: F401


def document():
    return {
        "version": 1,
        "categories": {
            "Violent": {"enabled": True, "action": "block_flag", "message": "Please ask a teacher."},
            "Self-harm": {"enabled": False, "action": "block", "message": "Please ask for help."},
        },
        "uncategorized": {"action": "block_flag", "message": "This needs a teacher to check it."},
    }


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(extra=True),
        lambda d: d.update(version=2),
        lambda d: d.update(categories=[]),
        lambda d: d["categories"].update({" violent ": d["categories"]["Violent"]}),
        lambda d: d["categories"].update({"Ｖｉｏｌｅｎｔ": d["categories"]["Violent"]}),
        lambda d: d["categories"].update({"Uncategorized": d["categories"]["Violent"]}),
        lambda d: d["categories"].update({"Bad\nName": d["categories"]["Violent"]}),
        lambda d: d["categories"]["Violent"].update(enabled=1),
        lambda d: d["categories"]["Violent"].update(action="ignore"),
        lambda d: d["categories"]["Violent"].update(message=""),
        lambda d: d["categories"]["Violent"].update(message="x" * 501),
        lambda d: d["uncategorized"].update(enabled=False),
    ],
)
def test_validation_rejects_malformed_or_ambiguous_documents(mutate):
    value = document()
    mutate(value)
    with pytest.raises(InvalidGuardSettings):
        validate_document(value)


def test_validation_returns_a_detached_document():
    value = document()
    checked = validate_document(value)
    value["categories"]["Violent"]["message"] = "changed"
    assert checked["categories"]["Violent"]["message"] == "Please ask a teacher."


async def test_load_save_and_concurrent_last_commit_wins(app):
    first = document()
    second = document()
    first["uncategorized"]["message"] = "First."
    second["uncategorized"]["message"] = "Second."
    async with app.state.pool.connection() as conn:
        await save(conn, first)
        assert await load(conn) == first

    first_saved = asyncio.Event()
    let_first_commit = asyncio.Event()

    async def write_first():
        async with app.state.pool.connection() as conn, conn.transaction():
            await save(conn, first)
            first_saved.set()
            await let_first_commit.wait()

    async def write_second():
        await first_saved.wait()
        async with app.state.pool.connection() as conn, conn.transaction():
            let_first_commit.set()
            await save(conn, second)

    await asyncio.gather(write_first(), write_second())
    async with app.state.pool.connection() as conn:
        assert await load(conn) == second


@pytest.fixture
async def admin_env(empty_dsn, password_hash):  # noqa: F811
    app = create_app(empty_dsn)
    async with app.router.lifespan_context(app):
        username = f"admin-{uuid.uuid4().hex[:6]}"
        with psycopg.connect(empty_dsn) as conn:
            conn.execute(
                "INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, 'admin')",
                (username, password_hash),
            )
            conn.execute(
                "UPDATE settings SET value = %s WHERE key = 'guard_categories'",
                (Jsonb(document()),),
            )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://test"
        ) as client:
            await sign_in(client, username)
            yield app, empty_dsn, client, username


async def test_admin_can_replace_settings_and_audit_excludes_messages(admin_env):
    app, dsn, client, username = admin_env
    assert (await client.get("/api/admin/guard")).json() == document()
    replacement = copy.deepcopy(document())
    replacement["categories"]["Violent"] = {
        "enabled": False,
        "action": "allow_flag",
        "message": "A private replacement that must not be audited.",
    }
    response = await client.put("/api/admin/guard", json=replacement, headers=await csrf(client))
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == replacement
    with psycopg.connect(dsn) as conn:
        action, detail = conn.execute(
            "SELECT action, detail FROM audit WHERE username = %s", (username,)
        ).fetchone()
    assert action == "guard.settings"
    assert "message" not in str(detail).lower()
    assert detail["after"][0] == {"name": "Violent", "enabled": False, "action": "allow_flag"}


async def test_guard_settings_require_admin_csrf_and_the_exact_category_set(admin_env):
    app, dsn, admin, username = admin_env
    teacher_name = f"teacher-{uuid.uuid4().hex[:6]}"
    with psycopg.connect(dsn) as conn:
        password = conn.execute("SELECT password_hash FROM staff WHERE username = %s", (username,)).fetchone()[0]
        conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES (%s, %s, 'teacher')",
            (teacher_name, password),
        )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as anonymous:
        assert (await anonymous.get("/api/admin/guard")).status_code == 401
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="https://test"
    ) as teacher:
        await sign_in(teacher, teacher_name)
        assert (await teacher.get("/api/admin/guard")).status_code == 403
    assert (await admin.put("/api/admin/guard", json=document())).status_code == 403
    added = document()
    added["categories"]["New"] = added["categories"]["Violent"]
    assert (await admin.put("/api/admin/guard", json=added, headers=await csrf(admin))).status_code == 400
    renamed = document()
    renamed["categories"]["violent"] = renamed["categories"].pop("Violent")
    assert (await admin.put("/api/admin/guard", json=renamed, headers=await csrf(admin))).status_code == 400


async def test_missing_or_malformed_stored_settings_fail_closed(admin_env):
    app, dsn, client, username = admin_env
    with psycopg.connect(dsn) as conn:
        conn.execute("UPDATE settings SET value = '{}' WHERE key = 'guard_categories'")
    assert (await client.get("/api/admin/guard")).status_code == 503
    assert (await client.put("/api/admin/guard", json=document(), headers=await csrf(client))).status_code == 503
