"""Independent QA for step 7: races and accounting around the console."""

import asyncio

import psycopg

from tests.helpers import make_staff, new_client, post, sign_in
from tests.test_classes import join
from tests.test_console import lesson_with_students, seed, teacher  # noqa: F401


async def flag_of(teacher, student_id):
    t = (await teacher.get(f"/api/students/{student_id}/transcript")).json()
    return t["conversations"][0]["messages"][0]["flags"][0]["id"]


async def test_concurrent_reviews_succeed_exactly_once(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "bad", "blocked")], flag="Violent")
    flag_id = await flag_of(teacher, roster[0]["id"])
    async with new_client(app) as second:
        await sign_in(second, await make_staff(dsn, "admin"))
        results = await asyncio.gather(post(teacher, f"/api/flags/{flag_id}/review"),
                                       post(second, f"/api/flags/{flag_id}/review"))
    assert sorted(r.status_code for r in results) == [200, 409]
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM audit WHERE action = 'flag.review'"
                            " AND (detail->>'flag_id')::bigint = %s", (flag_id,)).fetchone()[0] == 1


async def test_other_teacher_cannot_review_and_flag_stays_open(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "bad", "blocked")], flag="Violent")
    flag_id = await flag_of(teacher, roster[0]["id"])
    async with new_client(app) as other:
        await sign_in(other, await make_staff(dsn, "teacher"))
        assert (await post(other, f"/api/flags/{flag_id}/review")).status_code == 404
    cards = (await teacher.get(f"/api/sessions/{session_id}/students")).json()["students"]
    assert cards[0]["unreviewed_flags"] == 1


async def test_unbind_keeps_old_messages_in_usage_but_not_on_cards(app, teacher, dsn):
    session_id, roster = await lesson_with_students(app, teacher, ["Aroha", "Ben"])
    seed(dsn, roster[0]["id"], "gemma", [("user", "hi", "ok"), ("assistant", "yo", "ok")])
    seed(dsn, roster[1]["id"], "gemma", [("user", "hey", "ok")])
    assert (await post(teacher, f"/api/students/{roster[0]['id']}/unbind")).status_code == 200
    cards = (await teacher.get(f"/api/sessions/{session_id}/students")).json()["students"]
    usage = (await teacher.get(f"/api/sessions/{session_id}/usage")).json()
    assert [c["name"] for c in cards] == ["Ben"]
    assert usage["total_messages"] == 2  # counts the retired Aroha row too
    assert [s["name"] for s in usage["per_student"]] == ["Aroha", "Ben"]
    assert usage["models"] == [{"model": "gemma", "conversations": 2}]


async def test_user_message_counts_ignore_assistant_and_other_sessions(app, teacher, dsn):
    s1, r1 = await lesson_with_students(app, teacher, ["Aroha"])
    s2, r2 = await lesson_with_students(app, teacher, ["Ben"])
    seed(dsn, r1[0]["id"], "gemma", [("user", "a", "ok"), ("assistant", "b", "ok"), ("user", "c", "blocked")])
    seed(dsn, r2[0]["id"], "other", [("user", "z", "ok")], flag="Violent")
    c1 = (await teacher.get(f"/api/sessions/{s1}/students")).json()["students"]
    u1 = (await teacher.get(f"/api/sessions/{s1}/usage")).json()
    assert c1[0]["messages"] == 2 and u1["total_messages"] == 2 and u1["active_students"] == 1
    assert u1["flags_by_category"] == [] and u1["models"] == [{"model": "gemma", "conversations": 1}]
