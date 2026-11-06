from types import SimpleNamespace

import httpx
import pytest

from app.guard import (ActionResolution, CheckResult, Context, GUARD_TIMEOUT_SECONDS,
                       GuardUnavailable, ReplyGate, Verdict, parse_qwen_verdict,
                       resolve_actions, run_check, request_verdict, verdict_from)
from tests.fakes import fake_guard


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://guard")


async def test_guard_transport_returns_the_exact_verdict():
    verdict = "Safety: Unsafe\nCategories: Violent"
    async with client_for(fake_guard(verdict)) as client:
        assert await request_verdict(client, "http://guard", [{"role": "user", "content": "x"}]) == verdict


@pytest.mark.parametrize(
    ("raw", "reply", "expected"),
    [
        ("Safety: Safe\nCategories: None", False, (True, ())),
        ("Safety: Controversial\nCategories: Copyright Violation", False,
         (False, ("Copyright Violation",))),
        ("Safety: Unsafe\nCategories: New Label\nRefusal: Yes", True,
         (False, ("New Label",))),
    ],
)
def test_qwen_parser_accepts_every_valid_shape_and_preserves_unknown_labels(raw, reply, expected):
    assert parse_qwen_verdict(raw, reply) == expected


@pytest.mark.parametrize("raw,reply", [
    ("garbage", False),
    ("Safety: Safe\nCategories: Violent", False),
    ("Safety: Unsafe\nCategories: None", False),
    ("Safety: Unsafe\nCategories: Violent", True),
    ("Safety: Unsafe\nCategories: Jailbreak\nRefusal: No", True),
    ("Safety: Unsafe\nCategories: jailbreak\nRefusal: No", True),
    ("Safety: Unsafe\nCategories: Ｊａｉｌｂｒｅａｋ\nRefusal: No", True),
    ("Safety: Unsafe\nCategories: None, Violent\nRefusal: No", True),
])
def test_qwen_parser_fails_closed_on_malformed_or_inconsistent_shapes(raw, reply):
    with pytest.raises(GuardUnavailable):
        parse_qwen_verdict(raw, reply)


async def test_qwen_adapter_sends_prompt_and_cumulative_reply_with_frozen_sampling():
    requests = []

    async def answer(request):
        requests.append(await request.aread())
        body = '{"choices":[{"message":{"content":"Safety: Safe\\nCategories: None\\nRefusal: No"}}]}'
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(answer)) as client:
        app = SimpleNamespace(state=SimpleNamespace(guard_client=client, guard_url="http://guard"))
        result = await run_check(app, Context(1, 2, "question"), "answer so far")

    import json
    payload = json.loads(requests[0])
    assert payload["messages"] == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer so far"},
    ]
    assert payload | {"messages": None} == {
        "model": "qwen3guard-0.6b", "messages": None, "max_tokens": 128,
        "temperature": 0, "seed": 424242, "top_k": 0, "top_p": 1,
        "min_p": 0, "repeat_penalty": 1, "presence_penalty": 0,
        "frequency_penalty": 0, "stream": False,
    }
    assert result.safe is True and result.duration_ms >= 0


async def test_guard_transport_accepts_an_exact_multibyte_byte_limit():
    verdict = "€" * 2730 + "xx"
    async with client_for(fake_guard(verdict)) as client:
        assert await request_verdict(client, "http://guard", []) == verdict


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(503, json={"error": "down"}),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{"message": {"content": 3}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "x" * 8193}}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": "€" * 2731}}]}),
    ],
)
async def test_guard_transport_fails_closed(response):
    async def reply(request):
        return response

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as client:
        with pytest.raises(GuardUnavailable, match="valid verdict"):
            await request_verdict(client, "http://guard", [])


async def test_guard_transport_fails_closed_when_service_is_unreachable():
    async def down(request):
        raise httpx.ConnectError("down", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(down)) as client:
        with pytest.raises(GuardUnavailable, match="valid verdict"):
            await request_verdict(client, "http://guard", [])


def settings():
    return {
        "version": 1,
        "categories": {
            "Violent": {"enabled": True, "action": "allow_flag", "message": "Violence."},
            "Self-Harm": {"enabled": True, "action": "block", "message": "Please get help."},
            "PII": {"enabled": False, "action": "block_flag", "message": "Private information."},
        },
        "uncategorized": {"action": "block_flag", "message": "Please ask a teacher."},
    }


PUBLISHED = ("Violent", "Self-Harm", "PII")


def test_safe_verdict_takes_no_action_after_configuration_validation():
    assert resolve_actions(True, ["Violent"], settings(), PUBLISHED) == ()


def test_unsafe_categories_resolve_in_first_model_order_and_ignore_disabled():
    resolutions = resolve_actions(
        False,
        ["violent", "PII", "Self-Harm", "VIOLENT"],
        settings(),
        PUBLISHED,
    )
    assert [(row.category, row.action) for row in resolutions] == [
        ("Violent", "allow_flag"),
        ("Self-Harm", "block"),
    ]
    assert [row.category for row in resolutions if row.flags] == ["Violent"]
    assert next(row.message for row in resolutions if row.blocks) == "Please get help."


@pytest.mark.parametrize("categories", [[], ["New"], ["New", "Other", "new"]])
def test_unsafe_missing_or_unknown_categories_use_one_fallback(categories):
    assert resolve_actions(False, categories, settings(), PUBLISHED) == (
        ActionResolution("Uncategorized", "block_flag", "Please ask a teacher."),
    )


def test_unknown_fallback_keeps_its_position_among_known_categories():
    resolutions = resolve_actions(False, ["Other", "Violent", "Unknown"], settings(), PUBLISHED)
    assert [row.category for row in resolutions] == ["Uncategorized", "Violent"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["categories"].pop("PII"),
        lambda value: value["categories"].update({"Extra": value["categories"]["PII"]}),
        lambda value: value.update(version=2),
    ],
)
def test_incomplete_or_malformed_category_settings_fail_closed(mutate):
    value = settings()
    mutate(value)
    with pytest.raises(GuardUnavailable, match="settings are invalid"):
        resolve_actions(False, ["Violent"], value, PUBLISHED)


def test_evidence_verdict_uses_first_blocking_action_and_keeps_raw_categories():
    result = CheckResult(False, ("violent", "New"), "Safety: Unsafe", 17)
    resolutions = (
        ActionResolution("Violent", "allow_flag", "Violence."),
        ActionResolution("Uncategorized", "block_flag", "Please ask a teacher."),
    )
    verdict = verdict_from(result, resolutions)
    assert verdict.action == "block_flag"
    assert verdict.message == "Please ask a teacher."
    assert verdict.categories == ("Violent", "Uncategorized")
    assert verdict.raw_categories == ("violent", "New")
    assert verdict.raw_response == "Safety: Unsafe"
    assert verdict.duration_ms == 17


async def test_reply_segments_stop_at_300_before_a_later_sentence_end():
    class RecordingGate(ReplyGate):
        def __init__(self):
            app = SimpleNamespace(state=SimpleNamespace())
            super().__init__(app, None)
            self.segments = []

        async def _release(self, segment):
            self.segments.append(segment)
            return segment

    gate = RecordingGate()
    first = await gate.feed("x" * 350 + ".")
    tail = await gate.finish()

    assert [len(segment) for segment in gate.segments] == [300, 51]
    assert first == "x" * 350 + "."
    assert tail == ""


# --- Lifecycle and boundary tests (cai-1ir.58) ---


async def test_prompt_verdict_deadline_margin_below_stale_window():
    """Verify GUARD_TIMEOUT_SECONDS configures transport operations with a 60-second
    threshold, providing an expected 60-second margin below chat.PENDING_STALE_MINUTES
    (120s user sweep), and that the configured timeout is explicitly propagated
    to the HTTP transport."""
    import time
    from app import chat

    stale_sweep_seconds = chat.PENDING_STALE_MINUTES * 60
    assert stale_sweep_seconds == 120.0
    assert GUARD_TIMEOUT_SECONDS == 60.0

    margin = stale_sweep_seconds - GUARD_TIMEOUT_SECONDS
    assert margin == 60.0

    async def hanging_guard(request):
        assert request.extensions.get("timeout", {}).get("connect") == GUARD_TIMEOUT_SECONDS
        assert request.extensions.get("timeout", {}).get("read") == GUARD_TIMEOUT_SECONDS
        raise httpx.ReadTimeout("guard request timed out after 60.0s", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(hanging_guard), timeout=1.0) as client:
        start = time.monotonic()
        with pytest.raises(GuardUnavailable, match="valid verdict"):
            await request_verdict(client, "http://guard", [{"role": "user", "content": "hello"}])
        elapsed = time.monotonic() - start
        assert elapsed < 1.0


async def test_concurrent_student_admission_with_prompt_verdict_in_flight(app, dsn):
    """Verify student admission (admit) uses per-student row locks so that
    concurrent student admissions proceed without blocking each other,
    while pending rows count toward the submitting student's message limit."""
    import asyncio
    from starlette.requests import Request
    from app import chat
    from app.auth import HTTPError

    async with app.state.pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES ('teacher-admission', 'hash', 'teacher') RETURNING id"
        )
        teacher_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO classes (name, instructions, message_limit, teacher_id) VALUES ('Bio', 'Test', 1, %s) RETURNING id",
            (teacher_id,),
        )
        class_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO lesson_sessions (class_id, state) VALUES (%s, 'open') RETURNING id",
            (class_id,),
        )
        session_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO students (lesson_session_id, code, name, bound_at) VALUES (%s, '111111', 'Student A', now()) RETURNING id",
            (session_id,),
        )
        student_a_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO students (lesson_session_id, code, name, bound_at) VALUES (%s, '222222', 'Student B', now()) RETURNING id",
            (session_id,),
        )
        student_b_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO models (name, enabled) VALUES ('gemma-4-e2b-it', true) ON CONFLICT (name) DO UPDATE SET enabled = true"
        )
        cur = await conn.execute(
            "INSERT INTO conversations (student_id, model) VALUES (%s, 'gemma-4-e2b-it') RETURNING id",
            (student_a_id,),
        )
        conv_a_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO conversations (student_id, model) VALUES (%s, 'gemma-4-e2b-it') RETURNING id",
            (student_b_id,),
        )
        conv_b_id = (await cur.fetchone())[0]

    scope = {"type": "http", "app": app}
    req = Request(scope)

    # 1. Concurrently admit turns for Student A and Student B via asyncio.gather
    (msg_a_id, model_a, inst_a, sess_a), (msg_b_id, model_b, inst_b, sess_b) = await asyncio.gather(
        chat.admit(req, {"id": student_a_id}, conv_a_id, "Prompt from A"),
        chat.admit(req, {"id": student_b_id}, conv_b_id, "Prompt from B"),
    )
    assert msg_a_id > 0
    assert msg_b_id > 0
    assert msg_b_id != msg_a_id

    # 2. Student A tries to admit another turn while prompt A is in-flight -> limit reached
    with pytest.raises(HTTPError) as exc_info:
        await chat.admit(req, {"id": student_a_id}, conv_a_id, "Second prompt from A")
    assert exc_info.value.status == 409
    assert exc_info.value.code == "limit_reached"

    # 3. Finish Student A's prompt verdict via store_verdict -> status transitions from 'pending' to 'ok'
    verdict = Verdict(action="allow", safe=True, duration_ms=25, raw_response="Safety: Safe\nCategories: None")
    async with app.state.pool.connection() as conn, conn.transaction():
        await chat.store_verdict(conn, msg_a_id, verdict, "ok")
        cur = await conn.execute("SELECT status FROM messages WHERE id = %s", (msg_a_id,))
        assert (await cur.fetchone())[0] == "ok"


async def test_multiple_reply_segments_and_per_segment_timeout_behavior(app, monkeypatch):
    """Verify ReplyGate splits streaming reply into multiple segments,
    checks each segment independently, releases approved segments, and fails
    closed on a per-segment timeout without releasing the timed-out segment."""
    timeouts = 0
    checked_calls = []

    async def mock_check(app_, ctx_, reply_text):
        nonlocal timeouts
        checked_calls.append(reply_text)
        if "second sentence" in reply_text.lower():
            timeouts += 1
            raise GuardUnavailable("guard request timed out after 60.0s")
        return Verdict(action="allow", safe=True, duration_ms=12, raw_response="Safety: Safe\nCategories: None")

    async with app.state.pool.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO staff (username, password_hash, role) VALUES ('teacher-segments', 'hash', 'teacher') RETURNING id"
        )
        teacher_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO classes (name, instructions, teacher_id) VALUES ('Art', 'Test', %s) RETURNING id",
            (teacher_id,),
        )
        class_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO lesson_sessions (class_id, state) VALUES (%s, 'open') RETURNING id",
            (class_id,),
        )
        session_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO students (lesson_session_id, code, name, bound_at) VALUES (%s, '333333', 'Student C', now()) RETURNING id",
            (session_id,),
        )
        student_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO models (name, enabled) VALUES ('gemma-4-e2b-it', true) ON CONFLICT (name) DO UPDATE SET enabled = true"
        )
        cur = await conn.execute(
            "INSERT INTO conversations (student_id, model) VALUES (%s, 'gemma-4-e2b-it') RETURNING id",
            (student_id,),
        )
        conv_id = (await cur.fetchone())[0]
        cur = await conn.execute(
            "INSERT INTO messages (conversation_id, role, text, status, shown_length)"
            " VALUES (%s, 'assistant', '', 'pending', 0) RETURNING id",
            (conv_id,),
        )
        reply_msg_id = (await cur.fetchone())[0]

    ctx = Context(conv_id, student_id, "test user prompt")
    gate = ReplyGate(app, ctx)
    gate.bind(reply_msg_id)

    import app.guard as guard_mod
    monkeypatch.setattr(guard_mod, "check", mock_check)

    released_1 = await gate.feed("First sentence. ")
    assert released_1 == "First sentence. "
    assert gate.checked == "First sentence. "
    assert gate.sequence == 1
    assert len(gate.verdicts) == 1

    with pytest.raises(GuardUnavailable, match="timed out"):
        await gate.feed("Second sentence. ")

    assert gate.checked == "First sentence. "
    assert gate.sequence == 1
    assert timeouts == 1
    assert checked_calls == ["First sentence. ", "First sentence. Second sentence. "]
