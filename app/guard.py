"""Winner-neutral transport for the local guard service."""

import httpx

MAX_RESPONSE_BYTES = 8192


class GuardUnavailable(Exception):
    """No valid guard response exists, so the caller must release no text."""


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
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        raise GuardUnavailable("guard did not return a valid verdict") from None
    return content
