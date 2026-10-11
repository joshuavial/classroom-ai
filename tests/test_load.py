"""Unit tests for the standalone load-test harness."""

import httpx
import pytest

from tests.load import (Attempt, Config, Failure, SSEParser, exit_code,
                        failed_guard_latency, guard_schema_is_pending,
                        initial_delay,
                        pending_guard_latency, reconcile, result, send_turn,
                        read_guard_latency, summarize_guard_rows,
                        timing_summary, turn_pause)


def test_sse_parser_handles_arbitrary_byte_boundaries_and_multiple_data_lines():
    parser = SSEParser()
    chunks = [b"eve", b"nt: delta\r\ndata: {\"text\": \"hel", b"lo \xc3", b"\xa9\"}\r\n\r\nevent: done\ndata: {}\n\n"]
    events = [event for chunk in chunks for event in parser.feed(chunk)]
    assert events == [("delta", '{"text": "hello é"}'), ("done", "{}")]


def test_sse_parser_flushes_a_terminal_without_final_blank_line():
    parser = SSEParser()
    assert parser.feed(b"event: blocked\ndata: {\"message\": \"stop\"}") == []
    assert parser.finish() == [("blocked", '{"message": "stop"}')]


def test_sse_parser_finish_keeps_event_completed_by_added_newline():
    parser = SSEParser()
    assert parser.feed(b"event: done\ndata: {}\n") == []
    assert parser.finish() == [("done", "{}")]


def test_guard_latency_is_summarized_by_kind_without_chat_text():
    summary = summarize_guard_rows(42, "prompt|10\nprompt|20\nreply_segment|5\n")
    assert summary["status"] == "available"
    assert summary["session_id"] == 42
    assert summary["by_kind"]["prompt"]["p50_ms"] == 15
    assert summary["by_kind"]["reply_segment"]["p95_ms"] == 5
    assert pending_guard_latency(42) == {
        "available": False, "status": "pending_step_6", "source": "postgres",
        "session_id": 42, "by_kind": {},
    }
    with pytest.raises(ValueError):
        summarize_guard_rows(42, "unexpected output")


def test_only_the_expected_missing_guard_schema_is_pending():
    assert guard_schema_is_pending('ERROR:  relation "guard_checks" does not exist\nLINE 1: SELECT duration_ms')
    assert guard_schema_is_pending("ERROR:  column gc.duration_ms does not exist")
    assert not guard_schema_is_pending(
        'ERROR:  column s.lesson_session_id does not exist\n'
        'LINE 1: SELECT gc.duration_ms FROM guard_checks gc WHERE s.lesson_session_id = 1')


@pytest.mark.asyncio
@pytest.mark.parametrize(("stderr", "status"), [
    ('ERROR:  relation "guard_checks" does not exist\n', "pending_step_6"),
    ('ERROR:  column s.lesson_session_id does not exist\n'
     'LINE 1: SELECT gc.duration_ms FROM guard_checks gc', "query_failed"),
])
async def test_guard_query_classifies_psql_errors(monkeypatch, stderr, status):
    class FailedProcess:
        returncode = 1

        async def communicate(self):
            return b"", stderr.encode()

    async def create_process(*_args, **_kwargs):
        return FailedProcess()

    monkeypatch.setattr("tests.load.asyncio.create_subprocess_exec", create_process)
    assert (await read_guard_latency(42, 1))["status"] == status


@pytest.mark.asyncio
async def test_prompt_guard_block_is_counted_from_json_response():
    async def blocked(_request):
        return httpx.Response(200, json={"blocked": "Ask your teacher."})

    async with httpx.AsyncClient(transport=httpx.MockTransport(blocked), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(1, 1, "prompt")
        state = type("State", (), {"failures": [], "failure": lambda self, *args, **kwargs: None})()
        await send_turn(client, "7", attempt, state)

    assert attempt.terminal == "blocked"
    assert state.failures == []


def test_seeded_pacing_is_repeatable_and_within_its_bounds():
    assert initial_delay(8, 2, 4) == initial_delay(8, 2, 4)
    assert initial_delay(8, 2, 4) != initial_delay(9, 2, 4)
    assert 0 <= initial_delay(8, 2, 4) <= 4
    assert turn_pause(8, 2, 3, 0.2, 1.5) == turn_pause(8, 2, 3, 0.2, 1.5)
    assert 0.2 <= turn_pause(8, 2, 3, 0.2, 1.5) <= 1.5


def test_timing_summary_uses_interpolated_percentiles():
    assert timing_summary([]) == {"available": False, "count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    assert timing_summary([10, 20, 30, 40]) == {"available": True, "count": 4, "p50_ms": 25.0, "p95_ms": 38.5, "max_ms": 40}


def test_reconciliation_separates_lost_prompts_from_done_reply_mismatches():
    ok = Attempt(1, 1, "unique one", reply="answer", terminal="done")
    mismatch = Attempt(1, 2, "unique two", reply="expected", terminal="done")
    lost = Attempt(1, 3, "unique three", terminal="blocked")
    transcript = [
        {"role": "user", "text": "unique one"}, {"role": "assistant", "text": "answer"},
        {"role": "user", "text": "unique two"}, {"role": "assistant", "text": "different"},
    ]
    failures = reconcile([ok, mismatch, lost], transcript)
    assert [(f.kind, f.turn) for f in failures] == [("unrecorded_reply", 2), ("lost_message", 3)]


def test_reconciliation_accepts_block_message_suffix_and_does_not_borrow_next_reply():
    blocked = Attempt(1, 1, "blocked prompt", terminal="blocked")
    missing_reply = Attempt(1, 2, "first prompt", reply="same answer", terminal="done")
    later = Attempt(1, 3, "second prompt", reply="same answer", terminal="done")
    transcript = [
        {"role": "user", "status": "blocked", "text": "blocked prompt\nAsk your teacher."},
        {"role": "user", "status": "ok", "text": "first prompt"},
        {"role": "user", "status": "ok", "text": "second prompt"},
        {"role": "assistant", "status": "ok", "text": "same answer"},
    ]
    failures = reconcile([blocked, missing_reply, later], transcript)
    assert [(failure.kind, failure.turn) for failure in failures] == [("unrecorded_reply", 2)]


def test_failure_accounting_and_exit_do_not_treat_blocked_as_infrastructure_loss():
    state = type("State", (), {})()
    state.attempts = [Attempt(1, 1, "p", terminal="blocked"), Attempt(1, 2, "q", terminal="done")]
    state.failures = [Failure("lost_message", "reconcile", 1, 2)]
    guard_latency = pending_guard_latency(1)
    report = result(Config(teacher_password="secret"), "run", state, 2, guard_latency)
    assert report["configuration"].get("teacher_password") is None
    assert report["counts"]["blocked"] == 1
    assert report["counts"]["lost_messages"] == 1
    assert exit_code(report) == 1
    state.failures = []
    assert exit_code(result(Config(teacher_password="secret"), "run", state, 2, guard_latency)) == 0
    assert exit_code(result(Config(), "run", state, 2, failed_guard_latency(1))) == 1
