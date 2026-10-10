"""Teacher console reads and flag review (step 7, slice B part 1).

Messages and flags are seeded directly; steps 4 and 6 write them for real.
"""

import asyncio

import psycopg
import pytest

from app.main import create_app
from tests.helpers import make_staff, new_client, post, sign_in
from tests.test_classes import join, new_lesson


@pytest.fixture
async def teacher(client, dsn):
    await sign_in(client, await make_staff(dsn, "teacher"))
    return client


def seed(dsn, student_id: int, model: str, turns: list[tuple[str, str, str]], flag: str | None = None,
         minutes_ago: int = 0) -> list[int]:
    """Add a conversation with (role, text, status) turns; flag the last message."""
    with psycopg.connect(dsn) as conn:
        convo = conn.execute("INSERT INTO conversations (student_id, model) VALUES (%s, %s) RETURNING id",
                             (student_id, model)).fetchone()[0]
        ids = [conn.execute(
            "INSERT INTO messages (conversation_id, role, text, status, created_at)"
            " VALUES (%s, %s, %s, %s, now() - make_interval(mins => %s)) RETURNING id",
            (convo, role, text, status, minutes_ago)).fetchone()[0] for role, text, status in turns]
        if flag:
            conn.execute("INSERT INTO flags (message_id, category, action) VALUES (%s, %s, 'block_flag')",
                         (ids[-1], flag))
    return ids


async def lesson_with_students(app, teacher, names):
    session_id, roster = await new_lesson(teacher, len(names) + 1)
    for entry, name in zip(roster, names):
        await join(app, entry["code"], name)
    return session_id, roster


async def test_cards_show_latest_message_counts_and_flags(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha", "Ben"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "hi", "ok"), ("assistant", "hello!", "ok")])
    seed(dsn, roster[0]["id"], "gemma", [("user", "how do I hurt someone", "blocked")], flag="Violent")
    cards = (await teacher.get(f"/api/sessions/{session_id}/students")).json()["students"]
    assert [c["name"] for c in cards] == ["Aroha", "Ben"]  # the unused code has no card
    aroha, ben = cards
    assert aroha["messages"] == 2 and aroha["unreviewed_flags"] == 1
    assert aroha["latest"]["text"] == "how do I hurt someone" and aroha["latest"]["status"] == "blocked"
    assert ben["messages"] == 0 and ben["latest"] is None and ben["unreviewed_flags"] == 0


async def test_usage_summary(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha", "Ben"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "a", "ok"), ("assistant", "b", "ok"), ("user", "c", "ok")])
    seed(dsn, roster[1]["id"], "qwen", [("user", "d", "ok")], flag="Jailbreak", minutes_ago=30)
    usage = (await teacher.get(f"/api/sessions/{session_id}/usage")).json()
    assert usage["active_students"] == 1
    assert usage["total_messages"] == 3  # student messages only
    assert usage["per_student"] == [{"id": roster[0]["id"], "name": "Aroha", "messages": 2},
                                    {"id": roster[1]["id"], "name": "Ben", "messages": 1}]
    assert usage["models"] == [{"model": "gemma", "conversations": 1}, {"model": "qwen", "conversations": 1}]
    assert usage["flags_by_category"] == [{"category": "Jailbreak", "flags": 1}]


async def test_transcript_includes_blocked_messages_and_flag_reasons(app, teacher, dsn):
    _, roster = await lesson_with_students(app, teacher, ["Aroha"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "hi", "ok"), ("assistant", "hello", "ok")])
    seed(dsn, roster[0]["id"], "gemma", [("user", "bad thing", "blocked")], flag="Violent")
    t = (await teacher.get(f"/api/students/{roster[0]['id']}/transcript")).json()
    assert t["student"]["name"] == "Aroha"
    assert [len(c["messages"]) for c in t["conversations"]] == [2, 1]
    blocked = t["conversations"][1]["messages"][0]
    assert blocked["status"] == "blocked" and blocked["flags"][0]["category"] == "Violent"
    assert blocked["flags"][0]["reviewed_at"] is None


async def test_transcript_stays_readable_after_remove_and_close(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "hi", "ok")])
    await post(teacher, f"/api/students/{roster[0]['id']}/remove")
    await post(teacher, f"/api/sessions/{session_id}/state", {"state": "closed"})
    t = (await teacher.get(f"/api/students/{roster[0]['id']}/transcript")).json()
    assert t["student"]["removed"] is True and t["conversations"][0]["messages"][0]["text"] == "hi"


async def test_mark_reviewed_once_with_audit_and_event(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "bad", "blocked")], flag="Violent")
    t = (await teacher.get(f"/api/students/{roster[0]['id']}/transcript")).json()
    message = t["conversations"][0]["messages"][0]
    flag_id = message["flags"][0]["id"]
    other = create_app(dsn)
    async with other.router.lifespan_context(other):
        queue = other.state.hub.subscribe(session_id)
        try:
            r = await post(teacher, f"/api/flags/{flag_id}/review")
            assert r.status_code == 200 and r.json()["reviewed_by"].startswith("teacher-")
            assert await asyncio.wait_for(queue.get(), 5) == {
                "k": "flag", "flag": flag_id, "student": roster[0]["id"]}
        finally:
            other.state.hub.unsubscribe(session_id, queue)
    again = await post(teacher, f"/api/flags/{flag_id}/review")
    assert (again.status_code, again.json()) == (409, {"error": "already_reviewed"})
    cards = (await teacher.get(f"/api/sessions/{session_id}/students")).json()["students"]
    assert cards[0]["unreviewed_flags"] == 0
    with psycopg.connect(dsn) as conn:
        detail = conn.execute(
            "SELECT detail FROM audit WHERE action = 'flag.review' AND (detail->>'flag_id')::bigint = %s",
            (flag_id,),
        ).fetchone()[0]
        assert detail == {"flag_id": flag_id, "message_id": message["id"]}


async def test_console_reads_refuse_other_teachers_students_and_anonymous(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "bad", "blocked")], flag="Violent")
    flag_id = (await teacher.get(f"/api/students/{roster[0]['id']}/transcript")).json()[
        "conversations"][0]["messages"][0]["flags"][0]["id"]
    paths = [f"/api/sessions/{session_id}/students", f"/api/sessions/{session_id}/usage",
             f"/api/students/{roster[0]['id']}/transcript"]
    async with new_client(app) as other:
        await sign_in(other, await make_staff(dsn, "teacher"))
        for path in paths:
            assert (await other.get(path)).status_code == 404, path
        assert (await post(other, f"/api/flags/{flag_id}/review")).status_code == 404
    async with new_client(app) as anon:
        for path in paths:
            assert (await anon.get(path)).status_code == 401, path
    student, _ = await join(app, roster[1]["code"], "Ben")
    for path in paths:
        assert (await student.get(path)).status_code == 401, path
    async with new_client(app) as admin:
        await sign_in(admin, await make_staff(dsn, "admin"))
        assert (await admin.get(paths[2])).status_code == 200
