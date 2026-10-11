import httpx
import pytest

from app.guard import GuardUnavailable, request_verdict
from tests.fakes import fake_guard


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://guard")


async def test_guard_transport_returns_the_exact_verdict():
    verdict = "Safety: Unsafe\nCategories: Violent"
    async with client_for(fake_guard(verdict)) as client:
        assert await request_verdict(client, "http://guard", [{"role": "user", "content": "x"}]) == verdict


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
