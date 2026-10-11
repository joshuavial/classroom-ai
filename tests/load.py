"""Run a small, repeatable student load test against the classroom HTTP API.

This is an executable test harness, not part of the application test suite. It
uses one cookie jar per simulated student and prints a JSON result suitable for
keeping with a capacity test record.
"""

from __future__ import annotations

import argparse
import asyncio
import codecs
import json
import os
import random
import secrets
import sys
import tempfile
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import httpx


BENIGN_QUESTIONS = (
    "What is a clear first step for solving this maths problem?",
    "How can I check whether my science explanation is supported by evidence?",
    "Can you help me make a short plan for revising this topic?",
    "What is one useful question to ask when reading this source?",
)


class HarnessError(Exception):
    """A setup error that prevents a meaningful capacity run."""


@dataclass(frozen=True)
class Config:
    students: int = 30
    turns: int = 3
    seed: int = 1
    startup_window: float = 60.0
    min_pause: float = 20.0
    max_pause: float = 60.0
    timeout: float = 60.0
    model: str = "gemma-4-e2b-it"
    base_url: str = "http://127.0.0.1"
    teacher_username: str = "teacher"
    teacher_password: str = ""
    verify: bool | str = True
    output: Path | None = None

    def public(self) -> dict[str, Any]:
        values = asdict(self)
        values.pop("teacher_password")
        values["output"] = str(self.output) if self.output else None
        return values


@dataclass
class Failure:
    kind: str
    stage: str
    student: int | None = None
    turn: int | None = None
    status: int | None = None
    detail: str | None = None


@dataclass
class Attempt:
    student: int
    turn: int
    prompt: str
    reply: str = ""
    terminal: str | None = None
    time_to_first_segment_ms: float | None = None


@dataclass
class RunState:
    attempts: list[Attempt] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)

    def failure(self, kind: str, stage: str, **values: Any) -> None:
        self.failures.append(Failure(kind=kind, stage=stage, **values))


class SSEParser:
    """Incrementally parse the small subset of SSE used by the chat endpoint."""

    def __init__(self) -> None:
        self._buffer = ""
        self._event = "message"
        self._data: list[str] = []
        self._decoder = codecs.getincrementaldecoder("utf-8")("replace")

    def feed(self, chunk: bytes) -> list[tuple[str, str]]:
        self._buffer += self._decoder.decode(chunk)
        events: list[tuple[str, str]] = []
        while True:
            newline = self._buffer.find("\n")
            if newline < 0:
                break
            line, self._buffer = self._buffer[:newline], self._buffer[newline + 1:]
            if line.endswith("\r"):
                line = line[:-1]
            if not line:
                if self._data:
                    events.append((self._event, "\n".join(self._data)))
                self._event, self._data = "message", []
            elif line.startswith("event:"):
                self._event = line[6:].lstrip()
            elif line.startswith("data:"):
                self._data.append(line[5:].lstrip())
        return events

    def finish(self) -> list[tuple[str, str]]:
        """Flush a final event when a server ends without its last blank line."""
        self._buffer += self._decoder.decode(b"", final=True)
        events: list[tuple[str, str]] = []
        if self._buffer:
            events.extend(self.feed(b"\n"))
        if self._data:
            events.append((self._event, "\n".join(self._data)))
            self._event, self._data = "message", []
        return events


def parse_sse_json(data: str) -> dict[str, Any]:
    try:
        value = json.loads(data) if data else {}
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def percentile(values: Iterable[float], fraction: float) -> float | None:
    ordered = sorted(values)
    if not ordered:
        return None
    position = (len(ordered) - 1) * fraction
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def timing_summary(values: Iterable[float]) -> dict[str, float | int | bool | None]:
    samples = list(values)
    if not samples:
        return {"available": False, "count": 0, "p50_ms": None, "p95_ms": None, "max_ms": None}
    return {"available": True, "count": len(samples), "p50_ms": percentile(samples, .5),
            "p95_ms": percentile(samples, .95), "max_ms": max(samples)}


def initial_delay(seed: int, student: int, startup_window: float) -> float:
    return random.Random(f"{seed}:initial:{student}").uniform(0, startup_window)


def turn_pause(seed: int, student: int, turn: int, minimum: float, maximum: float) -> float:
    return random.Random(f"{seed}:pause:{student}:{turn}").uniform(minimum, maximum)


def csrf_headers(client: httpx.AsyncClient) -> dict[str, str]:
    for cookie in client.cookies.jar:
        if cookie.name == "csrf_token":
            return {"X-CSRF-Token": cookie.value}
    raise HarnessError("the API did not set a csrf_token cookie")


async def checked_json(response: httpx.Response, stage: str) -> dict[str, Any]:
    if response.is_error:
        raise HarnessError(f"{stage} returned HTTP {response.status_code}")
    try:
        value = response.json()
    except json.JSONDecodeError as exc:
        raise HarnessError(f"{stage} did not return JSON") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"{stage} returned an unexpected JSON value")
    return value


def model_names(models: Any) -> set[str]:
    if not isinstance(models, list):
        return set()
    return {item if isinstance(item, str) else item.get("name", item.get("id"))
            for item in models if isinstance(item, str) or isinstance(item, dict)} - {None}


async def setup(config: Config, run_id: str) -> tuple[int, list[tuple[int, httpx.AsyncClient, str]], list[httpx.AsyncClient]]:
    clients: list[httpx.AsyncClient] = []
    teacher = httpx.AsyncClient(base_url=config.base_url, timeout=config.timeout, verify=config.verify)
    clients.append(teacher)
    try:
        await checked_json(await teacher.get("/api/me"), "teacher CSRF setup")
        await checked_json(await teacher.post("/api/login", json={"username": config.teacher_username,
                                                                    "password": config.teacher_password},
                                              headers=csrf_headers(teacher)), "teacher login")
        class_data = await checked_json(await teacher.post("/api/classes", json={
            "name": f"Load harness {run_id}", "instructions": "Answer classroom questions clearly.",
            "message_limit": max(config.turns + 1, 10),
        }, headers=csrf_headers(teacher)), "class creation")
        class_id = class_data.get("id")
        if class_id is None:
            raise HarnessError("class creation did not return an id")
        lesson = await checked_json(await teacher.post(f"/api/classes/{class_id}/sessions", json={"count": config.students},
                                                        headers=csrf_headers(teacher)), "lesson creation")
        lesson_id = lesson.get("id")
        if not isinstance(lesson_id, int) or isinstance(lesson_id, bool):
            raise HarnessError("lesson creation did not return an id")
        roster = (await checked_json(await teacher.get(f"/api/sessions/{lesson_id}"), "lesson roster")).get("roster")
        codes = [row.get("code") for row in roster if isinstance(row, dict) and isinstance(row.get("code"), str)] \
            if isinstance(roster, list) else []
        if len(codes) < config.students:
            raise HarnessError(f"lesson roster has {len(codes)} codes, but {config.students} are required")
        students: list[tuple[int, httpx.AsyncClient, str]] = []
        for number, code in enumerate(codes[:config.students], 1):
            client = httpx.AsyncClient(base_url=config.base_url, timeout=config.timeout, verify=config.verify)
            clients.append(client)
            await checked_json(await client.get("/api/me"), f"student {number} CSRF setup")
            await checked_json(await client.post("/api/join", json={"code": code, "name": f"Load Student {number}"},
                                                 headers=csrf_headers(client)), f"student {number} join")
            models = (await checked_json(await client.get("/api/student/models"), f"student {number} models")).get("models")
            if config.model not in model_names(models):
                raise HarnessError(f"student {number} cannot use requested model {config.model!r}")
            conversation = await checked_json(await client.post("/api/student/conversations", json={"model": config.model},
                                                                 headers=csrf_headers(client)), f"student {number} conversation")
            conversation_id = conversation.get("id")
            if conversation_id is None:
                raise HarnessError(f"student {number} conversation did not return an id")
            students.append((number, client, str(conversation_id)))
        return lesson_id, students, clients
    except Exception:
        await close_clients(clients)
        raise


async def send_turn(client: httpx.AsyncClient, conversation_id: str, attempt: Attempt, state: RunState) -> None:
    started = time.perf_counter()
    parser = SSEParser()
    try:
        async with client.stream("POST", f"/api/student/conversations/{conversation_id}/messages", json={"text": attempt.prompt},
                                 headers=csrf_headers(client)) as response:
            if response.is_error:
                state.failure("http", "send", student=attempt.student, turn=attempt.turn, status=response.status_code)
                return
            if not response.headers.get("content-type", "").startswith("text/event-stream"):
                payload = parse_sse_json((await response.aread()).decode("utf-8", "replace"))
                if isinstance(payload.get("blocked"), str):
                    attempt.terminal = "blocked"
                else:
                    state.failure("stream", "send", student=attempt.student, turn=attempt.turn,
                                  detail="unexpected non-stream response")
                return
            events: list[tuple[str, str]] = []
            async for chunk in response.aiter_bytes():
                events.extend(parser.feed(chunk))
                for event, data in events:
                    payload = parse_sse_json(data)
                    if event == "delta":
                        text = payload.get("text")
                        if isinstance(text, str):
                            if attempt.time_to_first_segment_ms is None:
                                attempt.time_to_first_segment_ms = (time.perf_counter() - started) * 1000
                            attempt.reply += text
                    elif event in {"done", "blocked", "error"}:
                        attempt.terminal = event
                events.clear()
            for event, data in parser.finish():
                payload = parse_sse_json(data)
                if event == "delta":
                    if isinstance(payload.get("text"), str):
                        if attempt.time_to_first_segment_ms is None:
                            attempt.time_to_first_segment_ms = (time.perf_counter() - started) * 1000
                        attempt.reply += payload["text"]
                elif event in {"done", "blocked", "error"}:
                    attempt.terminal = event
    except (httpx.HTTPError, UnicodeError):
        state.failure("stream", "send", student=attempt.student, turn=attempt.turn)
        return
    if attempt.terminal is None:
        state.failure("stream", "send", student=attempt.student, turn=attempt.turn, detail="missing terminal event")
    elif attempt.terminal == "error":
        state.failure("error_terminal", "send", student=attempt.student, turn=attempt.turn)


async def run_student(number: int, client: httpx.AsyncClient, conversation_id: str, config: Config, run_id: str,
                      state: RunState) -> None:
    await asyncio.sleep(initial_delay(config.seed, number, config.startup_window))
    for turn in range(1, config.turns + 1):
        prompt = f"load-{run_id}-student-{number}-turn-{turn}: {BENIGN_QUESTIONS[(number + turn) % len(BENIGN_QUESTIONS)]}"
        attempt = Attempt(number, turn, prompt)
        state.attempts.append(attempt)
        await send_turn(client, conversation_id, attempt, state)
        if turn < config.turns:
            await asyncio.sleep(turn_pause(config.seed, number, turn, config.min_pause, config.max_pause))


def reconcile(attempts: Iterable[Attempt], transcript: list[dict[str, Any]]) -> list[Failure]:
    failures: list[Failure] = []
    for attempt in attempts:
        indices = [index for index, message in enumerate(transcript)
                   if message.get("role") == "user" and (
                       message.get("text") == attempt.prompt or (
                           message.get("status") == "blocked"
                           and isinstance(message.get("text"), str)
                           and message["text"].startswith(f"{attempt.prompt}\n")
                       ))]
        if not indices:
            failures.append(Failure("lost_message", "reconcile", attempt.student, attempt.turn))
            continue
        if attempt.terminal != "done":
            continue
        start = indices[0] + 1
        end = next((index for index in range(start, len(transcript))
                    if transcript[index].get("role") == "user"), len(transcript))
        following = next((message for message in transcript[start:end] if message.get("role") == "assistant"), None)
        if following is None or following.get("text") != attempt.reply:
            failures.append(Failure("unrecorded_reply", "reconcile", attempt.student, attempt.turn))
    return failures


async def reconcile_all(students: list[tuple[int, httpx.AsyncClient, str]], state: RunState) -> None:
    by_student: dict[int, list[Attempt]] = {}
    for attempt in state.attempts:
        by_student.setdefault(attempt.student, []).append(attempt)
    for number, client, conversation_id in students:
        try:
            data = await checked_json(await client.get(f"/api/student/conversations/{conversation_id}"), f"student {number} transcript")
            messages = data.get("messages")
            if not isinstance(messages, list):
                raise HarnessError("transcript has no messages list")
            state.failures.extend(reconcile(by_student.get(number, []), [m for m in messages if isinstance(m, dict)]))
        except (HarnessError, httpx.HTTPError):
            state.failure("http", "reconcile", student=number)


def pending_guard_latency(session_id: int) -> dict[str, Any]:
    return {"available": False, "status": "pending_step_6", "source": "postgres",
            "session_id": session_id, "by_kind": {}}


def failed_guard_latency(session_id: int) -> dict[str, Any]:
    return {"available": False, "status": "query_failed", "source": "postgres",
            "session_id": session_id, "by_kind": {}}


def guard_schema_is_pending(error: str) -> bool:
    """Recognize only the two schema gaps that Step 6 is expected to fill."""
    errors = [line.removeprefix("ERROR:").strip() for line in error.splitlines()
              if line.startswith("ERROR:")]
    return any(detail in {
        'relation "guard_checks" does not exist',
        'column gc.duration_ms does not exist',
    } for detail in errors)


def summarize_guard_rows(session_id: int, output: str) -> dict[str, Any]:
    samples: dict[str, list[float]] = {}
    for line in output.splitlines():
        kind, separator, raw_duration = line.partition("|")
        if not separator or kind not in {"prompt", "reply_segment"}:
            raise ValueError("unexpected guard latency query output")
        samples.setdefault(kind, []).append(float(raw_duration))
    summaries = {kind: timing_summary(values) for kind, values in sorted(samples.items())}
    return {"available": bool(samples), "status": "available" if samples else "no_samples",
            "source": "postgres", "session_id": session_id, "by_kind": summaries}


async def read_guard_latency(session_id: int, timeout: float) -> dict[str, Any]:
    """Read aggregate guard timing only; no message text leaves Postgres."""
    if not isinstance(session_id, int) or isinstance(session_id, bool):
        raise ValueError("session_id must be an integer")
    query = (
        "SELECT gc.kind, gc.duration_ms FROM guard_checks gc "
        "JOIN messages m ON m.id = gc.message_id "
        "JOIN conversations c ON c.id = m.conversation_id "
        "JOIN students s ON s.id = c.student_id "
        f"WHERE s.lesson_session_id = {session_id} ORDER BY gc.kind, gc.id"
    )
    process: asyncio.subprocess.Process | None = None
    try:
        process = await asyncio.create_subprocess_exec(
            "docker", "compose", "exec", "-T", "db", "sh", "-c",
            'exec psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F "|" -c "$1"',
            "load-guard-latency", query,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
    except TimeoutError:
        if process is not None and process.returncode is None:
            process.kill()
            await process.wait()
        return failed_guard_latency(session_id)
    except OSError:
        return failed_guard_latency(session_id)
    if process.returncode:
        error = stderr.decode("utf-8", "replace")
        if guard_schema_is_pending(error):
            return pending_guard_latency(session_id)
        return failed_guard_latency(session_id)
    try:
        return summarize_guard_rows(session_id, stdout.decode("utf-8"))
    except (UnicodeError, ValueError):
        return failed_guard_latency(session_id)


def result(config: Config, run_id: str, state: RunState, elapsed_s: float,
           guard_latency: dict[str, Any]) -> dict[str, Any]:
    counts = {"attempted": len(state.attempts), "done": sum(a.terminal == "done" for a in state.attempts),
              "blocked": sum(a.terminal == "blocked" for a in state.attempts),
              "error_terminals": sum(f.kind == "error_terminal" for f in state.failures),
              "http_failures": sum(f.kind == "http" for f in state.failures),
              "stream_failures": sum(f.kind == "stream" for f in state.failures),
              "lost_messages": sum(f.kind == "lost_message" for f in state.failures),
              "unrecorded_replies": sum(f.kind == "unrecorded_reply" for f in state.failures)}
    return {"run_id": run_id, "configuration": config.public(), "counts": counts,
            "elapsed_seconds": elapsed_s, "throughput_attempts_per_second": len(state.attempts) / elapsed_s if elapsed_s else 0,
            "time_to_first_segment": timing_summary(a.time_to_first_segment_ms for a in state.attempts if a.time_to_first_segment_ms is not None),
            "guard_latency": guard_latency, "failures": [asdict(f) for f in state.failures]}


def exit_code(report: dict[str, Any]) -> int:
    counts = report["counts"]
    failed_run = any(counts[key] for key in (
        "http_failures", "stream_failures", "error_terminals", "lost_messages", "unrecorded_replies"))
    failed_guard_query = report.get("guard_latency", {}).get("status") == "query_failed"
    return int(failed_run or failed_guard_query)


async def close_clients(clients: Iterable[httpx.AsyncClient]) -> None:
    await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


async def run(config: Config) -> dict[str, Any]:
    run_id = secrets.token_hex(6)
    state, clients = RunState(), []
    started = time.perf_counter()
    try:
        lesson_id, students, clients = await setup(config, run_id)
        await asyncio.gather(*(run_student(*student, config, run_id, state) for student in students))
        await reconcile_all(students, state)
        guard_latency = await read_guard_latency(lesson_id, config.timeout)
    finally:
        await close_clients(clients)
    return result(config, run_id, state, time.perf_counter() - started, guard_latency)


def write_json_atomically(path: Path, report: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
        handle.write("\n")
        temporary = handle.name
    os.replace(temporary, path)


def verify_value(value: str) -> bool | str:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    return value


def parse_args(argv: list[str] | None = None) -> Config:
    parser = argparse.ArgumentParser(description="Run simulated students against the classroom chat API.")
    parser.add_argument("--students", type=int, default=30)
    parser.add_argument("--turns", type=int, default=3)
    parser.add_argument("--seed", type=int, default=1, help="Seed for deterministic student pacing.")
    parser.add_argument("--startup-window", type=float, default=60.0)
    parser.add_argument("--min-pause", type=float, default=20.0)
    parser.add_argument("--max-pause", type=float, default=60.0)
    parser.add_argument("--timeout", type=float, default=60.0, help="Per-request timeout in seconds.")
    parser.add_argument("--model", default="gemma-4-e2b-it")
    parser.add_argument("--base-url", default="http://127.0.0.1")
    parser.add_argument("--teacher-username", default="teacher")
    parser.add_argument("--teacher-password", required=True)
    parser.add_argument("--verify", default="true", help="true, false, or a CA certificate path.")
    parser.add_argument("--output", type=Path, help="Write the same JSON report atomically to this path.")
    args = parser.parse_args(argv)
    if args.students < 1 or args.turns < 1 or args.startup_window < 0 or args.min_pause < 0 or args.max_pause < args.min_pause or args.timeout <= 0:
        parser.error("students and turns must be positive; timing values must be valid")
    return Config(**{**vars(args), "verify": verify_value(args.verify)})


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    try:
        report = asyncio.run(run(config))
    except (HarnessError, httpx.HTTPError) as exc:
        report = {"configuration": config.public(), "counts": {"http_failures": 1, "stream_failures": 0,
                  "error_terminals": 0, "lost_messages": 0, "unrecorded_replies": 0, "blocked": 0,
                  "attempted": 0, "done": 0}, "failures": [{"kind": "setup", "stage": "setup", "detail": str(exc)}]}
    text = json.dumps(report, indent=2, sort_keys=True)
    print(text)
    if config.output:
        write_json_atomically(config.output, report)
    return exit_code(report)


if __name__ == "__main__":
    sys.exit(main())
