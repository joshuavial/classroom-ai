"""Guard transport plus the safety-check seam used by the chat pipeline.

The Qwen3Guard adapter stores bounded raw evidence and fails closed whenever a
check cannot be completed or parsed.
"""

import dataclasses
import logging
import re
import time
import unicodedata

import httpx
from psycopg.types.json import Jsonb

from app.guard_settings import InvalidGuardSettings, normalized_name, validate_document

log = logging.getLogger(__name__)

MAX_RESPONSE_BYTES = 8192
GUARD_MODEL = "qwen3guard-0.6b"
GUARD_TIMEOUT_SECONDS = 60.0
PUBLISHED_CATEGORIES = (
    "Violent",
    "Non-violent Illegal Acts",
    "Sexual Content or Sexual Acts",
    "PII",
    "Suicide & Self-Harm",
    "Unethical Acts",
    "Politically Sensitive Topics",
    "Copyright Violation",
    "Jailbreak",
)
ACTIONS = ("allow", "allow_flag", "block_flag", "block")
FLAGGING = ("allow_flag", "block_flag")
BLOCKING = ("block_flag", "block")


@dataclasses.dataclass(frozen=True)
class Verdict:
    action: str = "allow"
    categories: tuple[str, ...] = ()
    message: str = ""
    resolutions: tuple["ActionResolution", ...] = ()
    raw_response: str | None = None
    safe: bool | None = None
    raw_categories: tuple[str, ...] = ()
    duration_ms: int | None = None

    @property
    def blocks(self) -> bool:
        return self.action in BLOCKING

    @property
    def flags(self) -> bool:
        return self.action in FLAGGING or any(row.flags for row in self.action_resolutions)

    @property
    def action_resolutions(self) -> tuple["ActionResolution", ...]:
        if self.resolutions:
            return self.resolutions
        return tuple(ActionResolution(category, self.action, self.message) for category in self.categories)

    @property
    def has_evidence(self) -> bool:
        return self.raw_response is not None

    def to_json(self) -> dict:
        return {"action": self.action, "categories": list(self.categories), "message": self.message}


@dataclasses.dataclass(frozen=True)
class Context:
    conversation_id: int
    user_message_id: int
    prompt: str


class GuardUnavailable(Exception):
    """No valid guard response exists, so the caller must release no text."""


async def health(client: httpx.AsyncClient, url: str) -> bool:
    """Return whether llama-server is ready to accept guard checks."""
    try:
        response = await client.get(f"{url.rstrip('/')}/health", timeout=2)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


class ReplyBlocked(Exception):
    def __init__(self, verdict: Verdict):
        super().__init__(verdict.action)
        self.verdict = verdict


@dataclasses.dataclass(frozen=True)
class ActionResolution:
    """One enabled category action, in the guard model's output order."""

    category: str
    action: str
    message: str

    @property
    def blocks(self) -> bool:
        return self.action in {"block_flag", "block"}

    @property
    def flags(self) -> bool:
        return self.action in {"allow_flag", "block_flag"}


@dataclasses.dataclass(frozen=True)
class CheckResult:
    """A parsed guard response plus the evidence needed for durable storage."""

    safe: bool
    categories: tuple[str, ...]
    raw_response: str
    duration_ms: int


def verdict_from(result: CheckResult, resolutions: tuple[ActionResolution, ...]) -> Verdict:
    deciding = next((row for row in resolutions if row.blocks), resolutions[0] if resolutions else None)
    return Verdict(
        deciding.action if deciding else "allow",
        tuple(row.category for row in resolutions),
        deciding.message if deciding else "",
        resolutions,
        result.raw_response,
        result.safe,
        result.categories,
        result.duration_ms,
    )


def resolve_actions(
    safe: bool,
    categories: list[str] | tuple[str, ...],
    settings: object,
    published_categories: tuple[str, ...],
) -> tuple[ActionResolution, ...]:
    """Resolve an already-parsed verdict without letting label drift fail open."""
    try:
        document = validate_document(settings)
        configured = document["categories"]
        if len(set(published_categories)) != len(published_categories):
            raise InvalidGuardSettings("duplicate published category")
        if set(configured) != set(published_categories):
            raise InvalidGuardSettings("incomplete category settings")
        published = {normalized_name(name): name for name in published_categories}
        if len(published) != len(published_categories):
            raise InvalidGuardSettings("ambiguous published category")
    except InvalidGuardSettings:
        raise GuardUnavailable("guard category settings are invalid") from None

    if safe:
        return ()

    raw_categories: tuple[str | None, ...] = tuple(categories) or (None,)
    seen: set[str] = set()
    used_fallback = False
    resolutions: list[ActionResolution] = []
    for raw in raw_categories:
        if raw is None:
            match = ""
        elif isinstance(raw, str):
            match = normalized_name(raw)
        else:
            raise GuardUnavailable("guard categories are invalid")
        if match in seen:
            continue
        seen.add(match)
        display = published.get(match)
        if display is None:
            if used_fallback:
                continue
            used_fallback = True
            fallback = document["uncategorized"]
            resolutions.append(ActionResolution("Uncategorized", fallback["action"], fallback["message"]))
            continue
        record = configured[display]
        if record["enabled"]:
            resolutions.append(ActionResolution(display, record["action"], record["message"]))
    return tuple(resolutions)


async def request_verdict(client: httpx.AsyncClient, url: str, messages: list[dict]) -> str:
    """Call the guard and return its exact bounded response for later parsing."""
    try:
        response = await client.post(
            f"{url.rstrip('/')}/v1/chat/completions",
            json={
                "model": GUARD_MODEL,
                "messages": messages,
                "max_tokens": 128,
                "temperature": 0,
                "seed": 424242,
                "top_k": 0,
                "top_p": 1,
                "min_p": 0,
                "repeat_penalty": 1,
                "presence_penalty": 0,
                "frequency_penalty": 0,
                "stream": False,
            },
            timeout=GUARD_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        log.warning("guard request failed (%s)", type(exc).__name__)
        raise GuardUnavailable("guard did not return a valid verdict") from None
    return content


async def check_prompt(app, ctx: Context) -> Verdict:
    """Check and resolve a prompt, failing closed on service or configuration errors."""
    return await check(app, ctx)


def parse_qwen_verdict(raw: str, reply: bool = False) -> tuple[bool, tuple[str, ...]]:
    """Parse the selected model's exact labelled verdict shape."""
    lines = [line.strip() for line in raw.strip().splitlines() if line.strip()]
    if len(lines) != (3 if reply else 2):
        raise GuardUnavailable("guard verdict has an unexpected number of lines")
    safety = re.fullmatch(r"Safety: (Safe|Unsafe|Controversial)", lines[0])
    categories = re.fullmatch(r"Categories: (.+)", lines[1])
    if safety is None or categories is None:
        raise GuardUnavailable("guard verdict is malformed")
    if reply and re.fullmatch(r"Refusal: (Yes|No)", lines[2]) is None:
        raise GuardUnavailable("guard reply verdict is malformed")

    category_text = categories.group(1)
    parsed = () if category_text == "None" else tuple(part.strip() for part in category_text.split(","))
    if any(
        not part
        or len(part) > 100
        or any(unicodedata.category(char) in {"Cc", "Cf"} for char in part)
        for part in parsed
    ):
        raise GuardUnavailable("guard categories are malformed")
    if any(normalized_name(category) == "none" for category in parsed):
        raise GuardUnavailable("guard None category is malformed")
    safe = safety.group(1) == "Safe"
    if safe == bool(parsed):
        raise GuardUnavailable("guard safety and categories disagree")
    if reply and any(normalized_name(category) == "jailbreak" for category in parsed):
        raise GuardUnavailable("guard returned a prompt-only reply category")
    return safe, parsed


async def run_check(app, ctx: Context, reply: str | None = None) -> CheckResult:
    """Run one Qwen3Guard prompt or cumulative-reply check."""
    messages = [{"role": "user", "content": ctx.prompt}]
    if reply is not None:
        messages.append({"role": "assistant", "content": reply})
    started = time.perf_counter()
    raw = await request_verdict(app.state.guard_client, app.state.guard_url, messages)
    duration_ms = max(0, round((time.perf_counter() - started) * 1000))
    try:
        safe, categories = parse_qwen_verdict(raw, reply is not None)
    except GuardUnavailable as exc:
        log.warning("parse_qwen_verdict failed (%s): len=%d", exc, len(raw) if raw is not None else 0)
        raise
    return CheckResult(safe, categories, raw, duration_ms)


async def check(app, ctx: Context, reply: str | None = None) -> Verdict:
    from app import guard_settings

    try:
        async with app.state.pool.connection() as conn:
            settings = await guard_settings.load(conn)
    except InvalidGuardSettings:
        raise GuardUnavailable("guard category settings are invalid") from None
    # Validate completeness before spending a guard call or contacting a worker.
    resolve_actions(True, (), settings, PUBLISHED_CATEGORIES)
    result = await run_check(app, ctx, reply)
    if (
        not isinstance(result, CheckResult)
        or type(result.safe) is not bool
        or not isinstance(result.categories, tuple)
        or any(not isinstance(category, str) for category in result.categories)
        or (result.safe and bool(result.categories))
        or not isinstance(result.raw_response, str)
        or len(result.raw_response.encode("utf-8")) > MAX_RESPONSE_BYTES
        or type(result.duration_ms) is not int
        or result.duration_ms < 0
    ):
        raise GuardUnavailable("guard returned invalid evidence")
    resolutions = resolve_actions(result.safe, result.categories, settings, PUBLISHED_CATEGORIES)
    return verdict_from(result, resolutions)


async def store_check(conn, message_id: int, kind: str, sequence: int, verdict: Verdict) -> None:
    if not verdict.has_evidence or verdict.safe is None or verdict.duration_ms is None:
        raise GuardUnavailable("guard verdict has no evidence")
    await conn.execute(
        "INSERT INTO guard_checks"
        " (message_id, kind, sequence, raw_response, safe, categories, duration_ms)"
        " VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (message_id, kind, sequence, verdict.raw_response, verdict.safe,
         Jsonb(list(verdict.raw_categories)), verdict.duration_ms),
    )
    for resolution in verdict.action_resolutions:
        if resolution.flags:
            await conn.execute(
                "INSERT INTO flags (message_id, category, action)"
                " SELECT %s, %s, %s WHERE NOT EXISTS ("
                " SELECT 1 FROM flags WHERE message_id = %s AND category = %s AND action = %s)",
                (message_id, resolution.category, resolution.action,
                 message_id, resolution.category, resolution.action),
            )


class ReplyGate:
    """Decide which reply text is safe to show, one segment at a time."""

    def __init__(self, app, ctx: Context):
        self.app, self.ctx = app, ctx
        self.verdicts: list[Verdict] = []
        self.message_id: int | None = None
        self.buffer = ""
        self.checked = ""
        self.sequence = 0
        self.completed = False

    def bind(self, message_id: int) -> None:
        self.message_id = message_id

    async def _release(self, segment: str) -> str:
        if self.message_id is None:
            raise GuardUnavailable("reply guard has no message")
        verdict = await check(self.app, self.ctx, self.checked + segment)
        try:
            async with self.app.state.pool.connection() as conn, conn.transaction():
                await store_check(conn, self.message_id, "reply_segment", self.sequence, verdict)
                if not verdict.blocks:
                    await conn.execute(
                        "UPDATE messages SET guard_status = 'passed' WHERE id = %s", (self.message_id,))
        except GuardUnavailable:
            raise
        except Exception:
            raise GuardUnavailable("guard evidence could not be stored") from None
        self.sequence += 1
        self.verdicts.append(verdict)
        if verdict.blocks:
            raise ReplyBlocked(verdict)
        self.checked += segment
        return segment

    def _segment_length(self) -> int | None:
        sentence = re.search(r"[.!?](?:\s|$)", self.buffer)
        if sentence:
            return min(sentence.end(), 300)
        if len(self.buffer) >= 300:
            return 300
        return None

    async def feed(self, delta: str) -> str:
        self.buffer += delta
        released: list[str] = []
        while (length := self._segment_length()) is not None:
            segment, self.buffer = self.buffer[:length], self.buffer[length:]
            released.append(await self._release(segment))
        return "".join(released)

    async def finish(self) -> str:
        tail, self.buffer = self.buffer, ""
        released = await self._release(tail) if tail else ""
        self.completed = True
        return released


def reply_gate(app, ctx: Context) -> ReplyGate:
    return ReplyGate(app, ctx)
