from types import SimpleNamespace

import httpx
import pytest

from app.guard import (ActionResolution, CheckResult, Context, GuardUnavailable,
                       ReplyGate, parse_qwen_verdict, resolve_actions,
                       run_check, request_verdict, verdict_from)
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
