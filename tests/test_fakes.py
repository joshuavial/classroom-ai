import json

import httpx

from tests.fakes import fake_guard, fake_worker


def client_for(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fake")


async def test_fake_worker_lists_models_and_streams_reply():
    async with client_for(fake_worker(models=["m1"], reply=["a", "b"], api_key="k")) as c:
        assert (await c.get("/v1/models")).status_code == 401
        headers = {"authorization": "Bearer k"}
        models = (await c.get("/v1/models", headers=headers)).json()
        assert [m["id"] for m in models["data"]] == ["m1"]
        async with c.stream("POST", "/v1/chat/completions", headers=headers,
                            json={"model": "m1", "stream": True, "messages": []}) as r:
            lines = [line async for line in r.aiter_lines() if line.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    text = "".join(json.loads(l[6:])["choices"][0]["delta"].get("content", "") for l in lines[:-1])
    assert text == "ab"


async def test_fake_guard_returns_configured_verdict():
    async with client_for(fake_guard("Safety: Unsafe\nCategories: Violent")) as c:
        reply = (await c.post("/v1/chat/completions", json={"messages": []})).json()
    assert reply["choices"][0]["message"]["content"] == "Safety: Unsafe\nCategories: Violent"
