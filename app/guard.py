"""Guard transport plus the safety-check seam used by the chat pipeline.

The winner-neutral transport stores bounded raw evidence. The chat seam fails
closed unless GUARD_DISABLED=1 while Step 6 connects the selected guard.
"""

import dataclasses
import os

import httpx

MAX_RESPONSE_BYTES = 8192
ACTIONS = ("allow", "allow_flag", "block_flag", "block")
FLAGGING = ("allow_flag", "block_flag")
BLOCKING = ("block_flag", "block")


@dataclasses.dataclass(frozen=True)
class Verdict:
    action: str = "allow"
    categories: tuple[str, ...] = ()
    message: str = ""

    @property
    def blocks(self) -> bool:
        return self.action in BLOCKING

    @property
    def flags(self) -> bool:
        return self.action in FLAGGING

    def to_json(self) -> dict:
        return {"action": self.action, "categories": list(self.categories), "message": self.message}


@dataclasses.dataclass(frozen=True)
class Context:
    conversation_id: int
    user_message_id: int
    prompt: str


class GuardUnavailable(Exception):
    """No valid guard response exists, so the caller must release no text."""


class ReplyBlocked(Exception):
    def __init__(self, verdict: Verdict):
        super().__init__(verdict.action)
        self.verdict = verdict


async def request_verdict(client: httpx.AsyncClient, url: str, messages: list[dict]) -> str:
    """Call the guard and return its exact bounded response for later parsing."""
    try:
        response = await client.post(
            f"{url.rstrip('/')}/v1/chat/completions",
            json={"messages": messages, "stream": False},
            timeout=10,
        )
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_RESPONSE_BYTES:
            raise ValueError
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        raise GuardUnavailable("guard did not return a valid verdict") from None
    return content


def disabled_from_env() -> bool:
    return os.environ.get("GUARD_DISABLED") == "1"


async def check_prompt(app, ctx: Context) -> Verdict:
    """Return the prompt verdict, failing closed until Step 6 connects the guard."""
    if app.state.guard_disabled:
        return Verdict()
    raise GuardUnavailable()


class ReplyGate:
    """Decide which reply text is safe to show, one segment at a time."""

    def __init__(self, app, ctx: Context):
        self.app, self.ctx = app, ctx
        self.verdicts: list[Verdict] = []

    async def feed(self, delta: str) -> str:
        if not self.app.state.guard_disabled:
            raise GuardUnavailable()
        return delta

    async def finish(self) -> str:
        if not self.app.state.guard_disabled:
            raise GuardUnavailable()
        return ""


def reply_gate(app, ctx: Context) -> ReplyGate:
    return ReplyGate(app, ctx)
