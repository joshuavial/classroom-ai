"""Unit tests for the standalone load-test harness."""

import asyncio

import httpx
import pytest

from tests.load import (Attempt, Config, Failure, RunState, SSEParser, exit_code,
                        failed_guard_latency, guard_schema_is_pending,
                        initial_delay, parse_sse_json,
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


def test_sse_parser_handles_invalid_utf8_replacement():
    parser = SSEParser()
    events = parser.feed(b"event: delta\r\ndata: \xff\xfe\r\n\r\n")
    assert events == [("delta", "\ufffd\ufffd")]


def test_sse_parser_ignores_comments_and_unrecognized_lines():
    parser = SSEParser()
    assert parser.feed(b": heartbeat comment\r\n\r\n") == []
    assert parser.feed(b"id: 12345\r\nretry: 3000\r\n\r\n") == []
    events = parser.feed(b": initial comment\r\nevent: done\r\ndata: {}\r\n\r\n")
    assert events == [("done", "{}")]


def test_sse_parser_finish_idempotent_on_empty():
    parser = SSEParser()
    assert parser.finish() == []
    assert parser.finish() == []


def test_parse_sse_json_robustness():
    assert parse_sse_json("") == {}
    assert parse_sse_json("{invalid json") == {}
    assert parse_sse_json("[1, 2, 3]") == {}
    assert parse_sse_json("12345") == {}
    assert parse_sse_json('"plain string"') == {}
    assert parse_sse_json("true") == {}
    assert parse_sse_json('{"key": "value"}') == {"key": "value"}


@pytest.mark.asyncio
async def test_send_turn_records_http_error():
    async def handler(_request):
        return httpx.Response(500, text="Internal Server Error")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(2, 1, "maths question")
        state = RunState()
        await send_turn(client, "12", attempt, state)

    assert len(state.failures) == 1
    failure = state.failures[0]
    assert failure.kind == "http"
    assert failure.stage == "send"
    assert failure.student == 2
    assert failure.turn == 1
    assert failure.status == 500


@pytest.mark.asyncio
async def test_send_turn_records_stream_connection_drop():
    async def drop_handler(_request):
        async def broken_stream():
            yield b"event: delta\ndata: {\"text\": \"start\"}\n\n"
            raise httpx.ReadError("connection dropped unexpectedly")

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=broken_stream(),
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(drop_handler), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(3, 2, "science inquiry")
        state = RunState()
        await send_turn(client, "15", attempt, state)

    assert len(state.failures) == 1
    failure = state.failures[0]
    assert failure.kind == "stream"
    assert failure.stage == "send"
    assert failure.student == 3
    assert failure.turn == 2


@pytest.mark.asyncio
async def test_send_turn_records_missing_terminal_event():
    async def truncated_handler(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"event: delta\ndata: {\"text\": \"partial reply\"}\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(truncated_handler), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(4, 1, "prompt")
        state = RunState()
        await send_turn(client, "20", attempt, state)

    assert attempt.terminal is None
    assert attempt.reply == "partial reply"
    assert len(state.failures) == 1
    failure = state.failures[0]
    assert failure.kind == "stream"
    assert failure.stage == "send"
    assert failure.detail == "missing terminal event"


@pytest.mark.asyncio
async def test_send_turn_records_error_terminal():
    async def error_terminal_handler(_request):
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=b"event: error\ndata: {\"message\": \"model crashed\"}\n\n",
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(error_terminal_handler), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(5, 3, "prompt")
        state = RunState()
        await send_turn(client, "22", attempt, state)

    assert attempt.terminal == "error"
    assert len(state.failures) == 1
    failure = state.failures[0]
    assert failure.kind == "error_terminal"
    assert failure.stage == "send"


@pytest.mark.asyncio
async def test_send_turn_handles_unexpected_non_stream_response():
    async def unexpected_handler(_request):
        return httpx.Response(200, headers={"content-type": "application/json"}, json={"status": "unknown"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(unexpected_handler), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(6, 1, "prompt")
        state = RunState()
        await send_turn(client, "25", attempt, state)

    assert len(state.failures) == 1
    failure = state.failures[0]
    assert failure.kind == "stream"
    assert failure.stage == "send"
    assert failure.detail == "unexpected non-stream response"


@pytest.mark.asyncio
async def test_send_turn_measures_first_segment_and_accumulates_reply(monkeypatch):
    current_time = 100.0

    def mock_perf_counter():
        return current_time

    monkeypatch.setattr("tests.load.time.perf_counter", mock_perf_counter)

    async def streaming_handler(_request):
        nonlocal current_time

        async def chunks():
            nonlocal current_time
            current_time = 100.042
            yield b"event: delta\r\ndata: {\"text\": \"First \"}\r\n\r\n"
            await asyncio.sleep(0.001)
            current_time = 100.090
            yield b"event: delta\r\ndata: {\"text\": \"second \"}\r\n\r\n"
            await asyncio.sleep(0.001)
            current_time = 100.150
            yield b"event: done\r\ndata: {}\r\n\r\n"

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())

    async with httpx.AsyncClient(transport=httpx.MockTransport(streaming_handler), base_url="http://test") as client:
        client.cookies.set("csrf_token", "token")
        attempt = Attempt(7, 1, "prompt")
        state = RunState()
        await send_turn(client, "30", attempt, state)

    assert attempt.terminal == "done"
    assert attempt.reply == "First second "
    # Must preserve the first segment timing (100.042 - 100.0 = 42.0 ms), NOT overwritten by second delta (90.0 ms)
    assert attempt.time_to_first_segment_ms == pytest.approx(42.0)
    assert state.failures == []


@pytest.mark.asyncio
async def test_concurrent_student_execution():
    students_count = 10
    barrier = asyncio.Barrier(students_count)
    active_requests = 0
    peak_active = 0
    lock = asyncio.Lock()

    async def chat_handler(_request):
        nonlocal active_requests, peak_active
        async with lock:
            active_requests += 1
            peak_active = max(peak_active, active_requests)

        # Force all students to overlap in-flight before proceeding
        await barrier.wait()

        async def chunks():
            yield b"event: delta\r\ndata: {\"text\": \"echo \"}\r\n\r\n"
            yield b"event: done\r\ndata: {}\r\n\r\n"

        async with lock:
            active_requests -= 1

        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=chunks())

    transport = httpx.MockTransport(chat_handler)
    state = RunState()

    async def simulate_student(student_id: int):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            client.cookies.set("csrf_token", "token")
            attempt = Attempt(student_id, 1, f"q-{student_id}")
            state.attempts.append(attempt)
            await send_turn(client, f"conv-{student_id}", attempt, state)

    await asyncio.gather(*(simulate_student(i) for i in range(1, students_count + 1)))

    assert peak_active == students_count, f"Expected {students_count} concurrent requests, got {peak_active}"
    assert len(state.attempts) == students_count
    assert all(a.terminal == "done" for a in state.attempts)
    assert all(a.reply == "echo " for a in state.attempts)
    assert len(state.failures) == 0

    report = result(Config(students=students_count, turns=1), "concurrent-run", state, 0.5, pending_guard_latency(1))
    assert report["counts"]["attempted"] == students_count
    assert report["counts"]["done"] == students_count
    assert report["time_to_first_segment"]["count"] == students_count
    assert exit_code(report) == 0


def test_timing_summary_boundary_conditions():
    # Boundary: single sample
    single = timing_summary([42.5])
    assert single == {"available": True, "count": 1, "p50_ms": 42.5, "p95_ms": 42.5, "max_ms": 42.5}

    # Boundary: large sample set (1000 samples from 1 to 1000)
    samples = list(range(1, 1001))
    large = timing_summary(samples)
    assert large["available"] is True
    assert large["count"] == 1000
    assert large["p50_ms"] == 500.5
    assert large["p95_ms"] == 950.05
    assert large["max_ms"] == 1000


def test_result_and_exit_boundary_conditions_for_zero_sample_and_high_concurrency():
    # Boundary: zero attempts, zero elapsed time
    empty_state = RunState()
    empty_report = result(Config(), "empty-run", empty_state, 0.0, pending_guard_latency(10))
    assert empty_report["counts"]["attempted"] == 0
    assert empty_report["counts"]["done"] == 0
    assert empty_report["elapsed_seconds"] == 0.0
    assert empty_report["throughput_attempts_per_second"] == 0
    assert empty_report["time_to_first_segment"]["available"] is False
    assert exit_code(empty_report) == 0

    # Boundary: high concurrency with 300 attempts (30 students x 10 turns)
    large_state = RunState()
    for student in range(1, 31):
        for turn in range(1, 11):
            attempt = Attempt(student, turn, f"prompt-{student}-{turn}")
            if student == 30 and turn == 9:
                attempt.terminal = "error"
                large_state.failure("error_terminal", "send", student=student, turn=turn)
            elif turn % 5 == 0:
                attempt.terminal = "blocked"
            else:
                attempt.terminal = "done"
                attempt.reply = "reply"
                attempt.time_to_first_segment_ms = float(student * 2 + turn)
            large_state.attempts.append(attempt)

    report = result(Config(students=30, turns=10), "high-concurrency-run", large_state, 15.0, pending_guard_latency(20))
    assert report["counts"]["attempted"] == 300
    assert report["counts"]["done"] == 300 - 30 * 2 - 1  # 60 blocked, 1 error terminal
    assert report["counts"]["blocked"] == 60
    assert report["counts"]["error_terminals"] == 1
    assert report["throughput_attempts_per_second"] == 300 / 15.0
    assert report["time_to_first_segment"]["available"] is True
    assert report["time_to_first_segment"]["count"] == 239
    assert exit_code(report) == 1
